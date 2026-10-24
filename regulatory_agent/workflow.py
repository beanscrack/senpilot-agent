import json
from datetime import datetime
from pathlib import Path
import uuid
import zipfile
from .models import Artifacts, Request
from .mail import make_reply, summary


async def run(request: Request, output: Path, *, headed=False, limit=10,
              recipient="demo@localhost", original=None, log=print) -> Artifacts:
    from .uarb import UarbBrowser
    directory = output / f"{request.matter}-{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
    directory.mkdir(parents=True, exist_ok=False)
    log(f"Request: {request.matter} / {request.category}")
    try:
        async with UarbBrowser(headed=headed, log=log, diagnostics=directory) as browser:
            result = await browser.fetch(request, directory / "documents", min(10, max(1, limit)))
        manifest = json.dumps(result.to_dict(), indent=2, ensure_ascii=False)
        (directory / "manifest.json").write_text(manifest, encoding="utf8")
        archive = None
        if result.documents:
            archive = directory / f"{request.matter}-{request.category.lower().replace(' ', '-')}.zip"
            with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as z:
                for document in result.documents:
                    z.write(directory / "documents" / document.filename, document.filename)
                z.writestr("manifest.json", manifest)
            log(f"ZIP ready: {archive.name} ({archive.stat().st_size:,} bytes)")
        reply = make_reply(recipient, f"Re: {request.matter} - {request.category}", summary(result), archive, original)
        reply_path = directory / "reply.eml"
        reply_path.write_bytes(reply.as_bytes())
        (directory / "reply.txt").write_text(summary(result), encoding="utf8")
        log(f"Complete: {len(result.documents)} downloaded; {result.total} documents in the matter")
        log(f"Email preview: {reply_path}")
        return Artifacts(result, directory, archive, reply_path)
    except Exception as exc:
        (directory / "error.json").write_text(json.dumps({"error": str(exc), "matter": request.matter, "category": request.category}), encoding="utf8")
        raise
