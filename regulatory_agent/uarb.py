"""The site adapter: selectors and FileMaker-specific interaction live here."""
import asyncio
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import re
from playwright.async_api import async_playwright, TimeoutError as BrowserTimeout
from .models import CATEGORIES, Document, Request, Result

URL = "https://uarb.novascotia.ca/fmi/webd/UARB15"
# Leave MIME/base64 headroom under common 25 MB message limits.
DEFAULT_BUDGET = 17 * 1024 * 1024


def safe_filename(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")[:150]
    if not name:
        name = "document.bin"
    if name.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1,10)), *(f"LPT{i}" for i in range(1,10))}:
        name = "_" + name
    return name


def extract_counts(buttons: list[str]) -> dict[str, int]:
    counts = {}
    for category in CATEGORIES:
        matches = [re.fullmatch(re.escape(category) + r"\s*[-–]\s*([\d,]+)", text.strip(), re.I) for text in buttons]
        match = next((m for m in matches if m), None)
        if not match:
            raise RuntimeError(f"Could not read the {category} count. The website may have changed.")
        counts[category] = int(match.group(1).replace(",", ""))
    return counts


class UarbBrowser:
    def __init__(self, *, headed=False, log=print, diagnostics: Path | None = None):
        self.headed, self.log, self.diagnostics = headed, log, diagnostics

    async def __aenter__(self):
        self.playwright = await async_playwright().start()
        try:
            self.browser = await self.playwright.chromium.launch(headless=not self.headed)
            self.context = await self.browser.new_context(accept_downloads=True, viewport={"width":1440,"height":1100})
            self.page = await self.context.new_page()
            self.page.set_default_timeout(30000)
            return self
        except Exception:
            await self.playwright.stop()
            raise

    async def __aexit__(self, kind, exc, tb):
        if self.diagnostics:
            try:
                await self.page.screenshot(path=str(self.diagnostics / ("error.png" if exc else "source.png")), full_page=True)
                if exc:
                    (self.diagnostics / "error.html").write_text(await self.page.content(),encoding="utf8")
            except Exception:
                pass
        await self.browser.close()
        await self.playwright.stop()

    async def fetch(self, request: Request, destination: Path, limit=10) -> Result:
        page = self.page
        self.log("Opening the public documents database")
        await page.goto(URL, wait_until="domcontentloaded", timeout=90000)
        field = page.locator(".fm-textarea").filter(has=page.get_by_text("eg M01234", exact=True)).locator(".text")
        await field.wait_for(state="visible", timeout=90000)
        await field.click()
        editor = page.locator('[contenteditable="true"]')
        await editor.wait_for(state="visible")
        await editor.fill(request.matter)
        await editor.press("Tab")
        # FileMaker creates and commits its edit widget asynchronously.
        await page.locator('.fm_object_258 button').click()
        self.log(f"Searching for {request.matter}")
        try:
            await page.wait_for_function("""matter => {
                const value = document.querySelector('.fm_object_286 .text');
                const text = document.body.innerText;
                return value?.innerText.trim() === matter || /no records|no matching|not found|no matters/i.test(text);
            }""", arg=request.matter, timeout=60000)
        except BrowserTimeout as exc:
            raise RuntimeError(f"Could not open {request.matter}. Check the matter number or retry when the site is available.") from exc
        observed = await page.locator(".fm_object_286 .text").inner_text() if await page.locator(".fm_object_286 .text").count() else ""
        if observed.strip() != request.matter:
            raise RuntimeError(f"Matter {request.matter} was not found. No documents were downloaded.")
        counts = extract_counts(await page.get_by_role("button").all_inner_texts())
        metadata = {}
        for key, object_id in {"title":290, "status":289, "type":287, "category":298,
                               "date_received":292, "decision_date":294, "outcome":295}.items():
            locator = page.locator(f".fm_object_{object_id}").first
            if key == "title":
                # FileMaker puts the title object's class on its background and
                # the text on a sibling element with this object ID.
                locator = page.locator('[id="b0p0o290i0i0r1"]')
            metadata[key] = (await locator.inner_text()).strip() if await locator.count() else ""
        result = Result(request, metadata, counts, retrieved_at=datetime.now(timezone.utc).isoformat())
        self.log(f"Matter: {metadata['title']}")
        self.log("Counts: " + ", ".join(f"{k}: {v}" for k,v in counts.items()) + f". Total: {result.total}")
        if not counts[request.category]:
            self.log(f"No {request.category} found; preparing a reply without an attachment")
            return result
        await page.get_by_role("button", name=re.compile(r"^"+re.escape(request.category)+r"\s*[-–]" )).click()
        await page.get_by_role("button", name="GO GET IT", exact=True).first.wait_for(state="visible",timeout=60000)
        # The virtualized list preloads at least the first ten rows. Read their IDs
        # before clicking so subsequent viewport recycling cannot change the target.
        rows = await page.locator('tr').evaluate_all("""rows => rows.filter(r=>r.querySelector('.fm-textarea.hand-cursor .text')).map(r=>({
            id:r.querySelector('.fm-textarea.hand-cursor .text').innerText.trim(),
            title:r.querySelector('.fm_object_202 .text')?.innerText.trim() || '',
            security:r.querySelector('.fm_object_204 .text')?.innerText.trim() || '',
            button:[...r.querySelectorAll('button')].find(b=>b.innerText.trim()==='GO GET IT')?.id,
            text:r.innerText
        }))""")
        rows = [r for r in rows if r.get("button")][:min(limit,10,counts[request.category])]
        if not rows:
            raise RuntimeError("The document list loaded without usable rows. The website layout may have changed.")
        destination.mkdir(parents=True, exist_ok=True)
        total_bytes = 0
        for index, row in enumerate(rows,1):
            if row["security"].casefold() != "public":
                result.failures.append(f"{row['id']}: not marked Public; skipped")
                continue
            self.log(f"Downloading {index}/{len(rows)}: {row['id']} - {row['title']}")
            downloaded = None
            for attempt in range(2):
                try:
                    downloaded = await self.download(row, destination)
                    break
                except Exception as exc:
                    await self.close_download_dialog()
                    if attempt == 0:
                        self.log(f"Retrying document {row['id']}: {type(exc).__name__}")
                        await asyncio.sleep(1)
                    else:
                        result.failures.append(f"{row['id']} ({row['title']}): {type(exc).__name__}: {str(exc)[:160]}")
            if downloaded:
                size = downloaded.stat().st_size
                if total_bytes + size > DEFAULT_BUDGET:
                    downloaded.unlink()
                    result.failures.append(f"{row['id']}: omitted because it exceeds the 17 MiB attachment budget")
                    continue
                total_bytes += size
                result.documents.append(Document(downloaded.name, row["title"], size, hashlib.sha256(downloaded.read_bytes()).hexdigest(),row["id"]))
        if len(rows) < min(limit,10,counts[request.category]):
            result.failures.append("The site rendered fewer rows than requested; only visible loaded rows were attempted.")
        if not result.documents and result.failures:
            self.log("No downloads succeeded; the reply will explain the failures")
        return result

    async def close_download_dialog(self):
        close = self.page.get_by_role("button", name="Close", exact=True)
        if await close.count() and await close.first.is_visible():
            await close.first.click()
            await close.first.wait_for(state="hidden")

    async def download(self, row, destination):
        page = self.page
        await page.locator(f'[id="{row["button"]}"]').click()
        await page.get_by_text("Your files are ready for download.", exact=False).wait_for(state="visible",timeout=45000)
        # GO GET IT opens a FileMaker download dialog. Its filename link starts
        # the actual download; it is not a conventional hyperlink in the list.
        filename = page.get_by_text(re.compile(r"^"+re.escape(row["id"])+r"\.[A-Za-z0-9.]+$" )).last
        await filename.wait_for(state="visible",timeout=15000)
        async with page.expect_download(timeout=60000) as info:
            await filename.click()
        download = await info.value
        name = safe_filename(download.suggested_filename)
        if not name.startswith(row["id"]):
            name = row["id"] + "-" + name
        path = destination / name
        try:
            await asyncio.wait_for(download.save_as(str(path)),timeout=180)
        except Exception:
            await download.cancel()
            path.unlink(missing_ok=True)
            raise
        failure = await download.failure()
        if failure:
            path.unlink(missing_ok=True)
            raise RuntimeError(failure)
        if path.stat().st_size == 0:
            path.unlink()
            raise RuntimeError("The downloaded file is empty")
        # Never attach a login/error HTML page masquerading as a document.
        with path.open('rb') as f:
            prefix=f.read(512).lstrip().lower()
        if prefix.startswith((b"<!doctype html", b"<html")) and path.suffix.lower() not in {".htm", ".html"}:
            path.unlink()
            raise RuntimeError("The site returned an HTML page instead of the document")
        await self.close_download_dialog()
        return path
