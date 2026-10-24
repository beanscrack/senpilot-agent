"""Explicit live smoke test. Start `python -m regulatory_agent demo` first."""
import asyncio
from email import policy
from email.parser import BytesParser
import hashlib
import io
import json
from pathlib import Path
import zipfile
from playwright.async_api import async_playwright


async def main():
    async with async_playwright() as p:
        browser=await p.chromium.launch()
        page=await browser.new_page(viewport={"width":1440,"height":1100})
        errors=[]
        page.on('pageerror',lambda error: errors.append(str(error)))
        await page.goto('http://127.0.0.1:8765')
        await page.get_by_role('button',name='Fetch documents').click()
        await page.locator('#status').filter(has_text='Complete').wait_for(timeout=180000)
        await page.locator('#result').wait_for(state='visible')
        async with page.expect_download() as info:
            await page.get_by_role('link',name='Download ZIP').click()
        download=await info.value
        Path('tmp').mkdir(exist_ok=True)
        await download.save_as('tmp/verified-demo.zip')
        zip_bytes=Path('tmp/verified-demo.zip').read_bytes()
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
            assert archive.testzip() is None
            manifest=json.loads(archive.read('manifest.json'))
            assert len(manifest['documents'])==10
            assert manifest['total_documents']==sum(manifest['counts'].values())
            assert await page.locator('#downloaded').inner_text()==f"10 downloaded · {manifest['total_documents']} total in this matter"
            for document in manifest['documents']:
                data=archive.read(document['filename'])
                assert hashlib.sha256(data).hexdigest()==document['sha256']
                assert data.startswith(b'%PDF')
        async with page.expect_download() as info:
            await page.get_by_role('link',name='Download email (.eml)').click()
        await (await info.value).save_as('tmp/verified-reply.eml')
        reply=BytesParser(policy=policy.default).parsebytes(Path('tmp/verified-reply.eml').read_bytes())
        assert list(reply.iter_attachments())[0].get_payload(decode=True)==zip_bytes
        assert f"Total documents in this matter: {manifest['total_documents']}" in reply.get_body(preferencelist=('plain',)).get_content()
        await page.screenshot(path='tmp/demo-complete.png',full_page=True)
        await page.set_viewport_size({'width':390,'height':844})
        assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        await page.screenshot(path='tmp/demo-mobile.png',full_page=True)
        assert not errors,errors
        print('PASS: browser request, 10 PDF files, checksums, ZIP integrity, MIME attachment, counts, mobile layout, no JS errors')
        await browser.close()


asyncio.run(main())
