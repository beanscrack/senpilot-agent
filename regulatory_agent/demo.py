"""Small loopback-only demo. No email account or external email is used."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
from urllib.parse import urlparse
import uuid
from .requests import parse_request
from .workflow import run


HTML = r'''<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Regulatory Document Agent</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#f4f6f8;color:#172536;font:16px/1.5 system-ui,sans-serif}main{max-width:1120px;margin:36px auto;padding:0 24px}header{margin-bottom:24px}.eyebrow{color:#296052;font-size:13px;font-weight:700;letter-spacing:.12em}h1{font-size:34px;letter-spacing:-1px;margin:6px 0}p{color:#536475}.grid{display:grid;grid-template-columns:1fr 1fr;gap:20px}.card{background:white;border:1px solid #dce3e9;border-radius:12px;padding:24px;margin-bottom:20px}h2{font-size:18px;margin-top:0}label{display:block;font-weight:600;margin:12px 0 6px}input,textarea,select{font:inherit;width:100%;padding:12px;border:1px solid #b8c6d2;border-radius:6px;resize:vertical}textarea{min-height:150px}button,.download{display:inline-block;background:#236955;color:white;border:0;border-radius:6px;padding:12px 18px;font:600 14px system-ui;cursor:pointer;text-decoration:none;margin:14px 8px 0 0}button:disabled{background:#87958f;cursor:wait}.muted{color:#637284;font-size:14px}.badge{display:inline-block;background:#e7f2ec;color:#27654c;border-radius:20px;padding:4px 10px;font-size:12px}pre{font:13px/1.65 ui-monospace,Consolas,monospace;white-space:pre-wrap;overflow-wrap:anywhere;max-height:390px;overflow:auto;margin-bottom:0}#log{background:#152934;color:#def0e7;padding:16px;border-radius:7px;min-height:180px}table{border-collapse:collapse;width:100%;font-size:14px}td,th{text-align:left;padding:8px;border-bottom:1px solid #e4e9ee}td:last-child,th:last-child{text-align:right}#result{display:none}#status{font-weight:600;margin-left:6px}.hint{margin-top:8px;font-size:13px;color:#637284}.error{color:#a32e2e}@media(max-width:780px){.grid{grid-template-columns:1fr}h1{font-size:28px}}
</style>
<main><header><span class="eyebrow">NOVA SCOTIA · PUBLIC DOCUMENTS</span><h1>Regulatory Document Agent</h1><p>Ask for a matter’s documents. Get a ZIP and a ready-to-send email.</p><span class="badge">Local demo · No external email sent</span></header>
<div class="grid"><section class="card"><h2>1. Send a request</h2><form id="form"><label for="request">Email message</label><textarea id="request" required>Hi Agent, can you give me Other Documents files from M12205? Thanks!</textarea><div class="hint">Types: Exhibits, Key Documents, Other Documents, Transcripts, Recordings.</div><label for="limit">Maximum documents</label><select id="limit"><option>10</option><option>3</option><option>1</option></select><button id="submit">Fetch documents</button><span id="status"></span></form><p class="muted">Fetches live records from the public database. Your output includes document counts across all five categories.</p></section>
<section class="card"><h2>2. Follow the agent</h2><pre id="log">Ready. Enter a request to begin.</pre></section></div>
<section class="card" id="result"><h2>3. Review the reply</h2><div id="links"></div><div class="grid"><div><h3 id="matter"></h3><p id="title"></p><table><thead><tr><th>Document type</th><th>Total</th></tr></thead><tbody id="counts"></tbody></table><p id="downloaded"></p><p class="muted" id="failures"></p></div><div><h3>Email preview</h3><pre id="reply"></pre></div></div></section>
<p class="muted">Generated locally. The email worker can connect a dedicated inbox using IMAP and SMTP after configuration.</p></main>
<script>
const $=id=>document.getElementById(id);let activeJob=null;
function link(label,href){const a=document.createElement('a');a.className='download';a.textContent=label;a.href=href;$('links').append(a)}
async function poll(){try{let r=await fetch('/jobs/'+activeJob);if(!r.ok)throw Error('Could not load job');let j=await r.json();$('log').textContent=j.logs.join('\n');$('log').scrollTop=$('log').scrollHeight;if(j.status==='running'){setTimeout(poll,650);return}$('submit').disabled=false;$('status').textContent=j.status==='complete'?'Complete':'Needs attention';if(j.error){$('log').textContent+='\n'+j.error;return}let m=j.manifest;$('result').style.display='block';$('matter').textContent=m.request.matter+' · '+m.request.category;$('title').textContent=m.metadata.title;$('counts').replaceChildren();for(const [name,count]of Object.entries(m.counts)){let tr=document.createElement('tr');for(let v of[name,count]){let td=document.createElement('td');td.textContent=v;tr.append(td)}$('counts').append(tr)}$('downloaded').textContent=m.documents.length+' downloaded · '+m.total_documents+' total in this matter';$('failures').textContent=m.failures.join('\n');$('reply').textContent=j.reply;$('links').replaceChildren();if(j.zip)link('Download ZIP','/jobs/'+activeJob+'/zip');link('Download email (.eml)','/jobs/'+activeJob+'/email');link('View manifest','/jobs/'+activeJob+'/manifest')}catch(e){$('status').textContent='Connection lost';$('log').textContent+='\n'+e.message;$('submit').disabled=false}}
$('form').onsubmit=async e=>{e.preventDefault();$('submit').disabled=true;$('result').style.display='none';$('status').textContent='Working…';$('log').textContent='Starting…';try{let r=await fetch('/jobs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({request:$('request').value,limit:Number($('limit').value)})});let j=await r.json();if(!r.ok)throw Error(j.error);activeJob=j.id;poll()}catch(e){$('log').textContent=e.message;$('status').textContent='Needs attention';$('submit').disabled=false}};
</script></html>'''


def serve(output: Path, *, port=8765, headed=False):
    jobs = {}
    lock = threading.Lock()
    executor = ThreadPoolExecutor(max_workers=1)

    def task(job_id, request, limit):
        def log(text):
            print(text, flush=True)
            with lock:
                jobs[job_id]["logs"].append(f"{datetime.now():%H:%M:%S}  {text}")
        try:
            artifacts = asyncio.run(run(request, output, headed=headed, limit=limit, log=log))
            with lock:
                jobs[job_id].update(status="complete", manifest=artifacts.result.to_dict(),
                    reply=(artifacts.directory / "reply.txt").read_text(encoding="utf8"),
                    zip=bool(artifacts.archive), artifacts=artifacts)
        except Exception as exc:
            with lock:
                jobs[job_id].update(status="failed", error=str(exc))

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self, data, kind="application/json", status=200, filename=None):
            if isinstance(data, (dict, list)):
                data = json.dumps(data, ensure_ascii=False).encode("utf8")
            if isinstance(data, str):
                data = data.encode("utf8")
            self.send_response(status)
            self.send_header("Content-Type", kind + ("; charset=utf-8" if kind.startswith("text/") or kind=="application/json" else ""))
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            if filename:
                self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/":
                return self.respond(HTML, "text/html")
            parts = path.strip("/").split("/")
            if len(parts) not in {2,3} or parts[0] != "jobs":
                return self.respond({"error":"Not found"}, status=404)
            with lock:
                job = jobs.get(parts[1])
                if not job:
                    return self.respond({"error":"Unknown job"}, status=404)
                snapshot = {k:v for k,v in job.items() if k != "artifacts"}
                artifacts = job.get("artifacts")
            if len(parts)==2:
                return self.respond(snapshot)
            if not artifacts:
                return self.respond({"error":"Job is not complete"},status=409)
            file_map={"zip":(artifacts.archive,"application/zip"),"email":(artifacts.reply,"message/rfc822"),
                "manifest":(artifacts.directory/"manifest.json","application/json")}
            item=file_map.get(parts[2])
            if not item or not item[0]:
                return self.respond({"error":"File not found"},status=404)
            return self.respond(item[0].read_bytes(),item[1],filename=item[0].name)

        def do_POST(self):
            # Block cross-site requests and DNS-rebinding against the local demo.
            allowed_hosts={f"127.0.0.1:{port}",f"localhost:{port}"}
            if self.headers.get("Host") not in allowed_hosts:
                return self.respond({"error":"Invalid host"},status=403)
            origin=self.headers.get("Origin")
            if origin and origin not in {f"http://{h}" for h in allowed_hosts}:
                return self.respond({"error":"Invalid origin"},status=403)
            if urlparse(self.path).path != "/jobs":
                return self.respond({"error":"Not found"},status=404)
            try:
                size=int(self.headers.get("Content-Length","0"))
                if not 0<size<=16000:
                    raise ValueError("Request is too large or empty")
                data=json.loads(self.rfile.read(size))
                if not isinstance(data,dict):
                    raise ValueError("Expected a JSON object")
                parsing_logs=[]
                request=parse_request(str(data.get("request","")),log=parsing_logs.append)
                limit=int(data.get("limit",10))
                if not 1<=limit<=10:
                    raise ValueError("Limit must be between 1 and 10")
            except (ValueError,TypeError) as exc:
                return self.respond({"error":str(exc)},status=400)
            with lock:
                if any(j["status"]=="running" for j in jobs.values()):
                    return self.respond({"error":"An agent request is already running. Wait for it to complete."},status=409)
                job_id=uuid.uuid4().hex
                jobs[job_id]={"status":"running","logs":parsing_logs}
            executor.submit(task,job_id,request,limit)
            self.respond({"id":job_id},status=202)

    server=ThreadingHTTPServer(("127.0.0.1",port),Handler)
    print(f"Demo ready: http://127.0.0.1:{port} (no external email is sent)",flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        executor.shutdown(wait=False,cancel_futures=True)
