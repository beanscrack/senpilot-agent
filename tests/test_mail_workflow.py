import asyncio
from email.message import EmailMessage
from email.parser import BytesParser
import email.policy
import hashlib
import json
import zipfile
from regulatory_agent.models import CATEGORIES, Document, Request, Result
from regulatory_agent.mail import make_reply, read_email, reply_address, should_ignore, summary
from regulatory_agent.workflow import run
from regulatory_agent.worker import handle, State


def incoming(text='Other Documents for M12205'):
    message=EmailMessage()
    message['From']='User <user@example.com>'
    message['To']='agent@localhost'
    message['Subject']='Document request'
    message['Message-ID']='<input@example.com>'
    message.set_content(text)
    return message


def test_email_html_and_reply_threading():
    original=incoming()
    original.set_content('<p>M12205 Other Documents</p>',subtype='html')
    parsed,text=read_email(original.as_bytes())
    assert 'M12205 Other Documents' in text
    reply=make_reply(reply_address(parsed),'Re: Documents','Ready',original=parsed)
    assert reply['To']=='user@example.com'
    assert reply['In-Reply-To']=='<input@example.com>'
    assert should_ignore(reply)


def test_clarification_is_local(tmp_path):
    path=handle(incoming('M12205').as_bytes(),tmp_path)
    reply=BytesParser(policy=email.policy.default).parsebytes(path.read_bytes())
    assert 'clarify' in reply['Subject'].lower()
    assert not list(reply.iter_attachments())


def test_real_zip_and_mime_attachment_with_fake_site(tmp_path,monkeypatch):
    from regulatory_agent import uarb
    data=b'%PDF-1.7\nA fixture document\n'
    class FakeBrowser:
        def __init__(self,**kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self,*args): pass
        async def fetch(self,request,destination,limit):
            destination.mkdir(parents=True)
            (destination/'123.pdf').write_bytes(data)
            return Result(request,{'title':'Real workflow fixture'},dict(zip(CATEGORIES,[4,2,12,0,0])),
                [Document('123.pdf','A document',len(data),hashlib.sha256(data).hexdigest(),'123')],
                retrieved_at='2026-10-23T12:00:00Z')
    monkeypatch.setattr(uarb,'UarbBrowser',FakeBrowser)
    artifacts=asyncio.run(run(Request('M12205','Other Documents'),tmp_path,original=incoming(),log=lambda _:None))
    with zipfile.ZipFile(artifacts.archive) as archive:
        assert archive.testzip() is None
        manifest=json.loads(archive.read('manifest.json'))
        assert manifest['total_documents']==18
        assert archive.read('123.pdf')==data
        assert manifest['documents'][0]['sha256']==hashlib.sha256(data).hexdigest()
    reply=BytesParser(policy=email.policy.default).parsebytes(artifacts.reply.read_bytes())
    attachment=list(reply.iter_attachments())[0]
    assert attachment.get_content_type()=='application/zip'
    assert attachment.get_payload(decode=True)==artifacts.archive.read_bytes()
    assert 'Downloaded 1 of 12' in reply.get_body(preferencelist=('plain',)).get_content()


def test_durable_deduplication(tmp_path):
    path=tmp_path/'state.sqlite3'
    one=State(path)
    one.set('request-1','sent','reply.eml')
    two=State(path)
    assert two.status('request-1')=='sent'
    assert two.status('new') is None


def test_zero_files_summary():
    result=Result(Request('M12205','Transcripts'),{'title':'Water project'},dict(zip(CATEGORIES,[13,6,43,0,0])))
    text=summary(result)
    assert '62' in text and 'No ZIP is attached' in text
