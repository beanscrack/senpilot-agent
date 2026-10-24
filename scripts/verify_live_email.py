"""Explicit live test: download one document and email its ZIP to the agent itself.

Requires completed Google sign-in. This sends one actual message, exclusively to
AGENT_EMAIL; it never selects a third-party recipient or weakens loop protection.
"""
import asyncio
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
import hashlib
import json
import os
from pathlib import Path
import time
from uuid import uuid4

from dotenv import load_dotenv
from regulatory_agent.gmail import GmailMailbox
from regulatory_agent.requests import parse_request
from regulatory_agent.workflow import run


def main():
    load_dotenv(Path.cwd()/'.env')
    address=os.environ['AGENT_EMAIL']
    request=parse_request('Please get the miscellaneous filings for M12205')
    artifacts=asyncio.run(run(request,Path('output'),
                             limit=1,recipient=address))
    if not artifacts.archive:
        raise RuntimeError('The live test needs a downloaded ZIP attachment')
    message=BytesParser(policy=policy.default).parsebytes(artifacts.reply.read_bytes())
    del message['Subject']
    message['Subject']=f'Regulatory Agent delivery test - M12205 - {uuid4().hex[:8]}'
    mailbox=GmailMailbox()
    try:
        sent=mailbox.send(message)
        message_id=sent['id']
        print(f'Live ZIP email sent to the agent inbox: {address}',flush=True)
        labels=[]
        for attempt in range(10):
            info=mailbox.request('GET','messages/'+message_id,params={'format':'minimal'})
            labels=info.get('labelIds',[])
            if 'INBOX' in labels:
                break
            time.sleep(2)
        if 'INBOX' not in labels:
            raise RuntimeError('Gmail accepted the email, but inbox delivery has not been confirmed')
        received=BytesParser(policy=policy.default).parsebytes(mailbox.fetch(message_id))
        attachments=list(received.iter_attachments())
        expected=artifacts.archive.read_bytes()
        if len(attachments)!=1 or attachments[0].get_payload(decode=True)!=expected:
            raise RuntimeError('The delivered email attachment did not match the generated ZIP')
        report={
            'checked_at':datetime.now(timezone.utc).isoformat(),
            'recipient':address,
            'subject':str(received['Subject']),
            'message_id':message_id,
            'inbox_delivery_confirmed':True,
            'zip_attachment_verified':True,
            'zip_sha256':hashlib.sha256(expected).hexdigest(),
            'artifact_directory':str(artifacts.directory),
            'request_parser':artifacts.result.request.parser,
            'request_model':artifacts.result.request.model,
            'note':'Self-delivery test; an external sender request is a separate test.',
        }
        Path('output/live-email-check.json').write_text(json.dumps(report,indent=2),encoding='utf8')
        print('PASS: received in INBOX; ZIP attachment bytes match the downloaded archive.',flush=True)
    finally:
        mailbox.close()


if __name__=='__main__':
    main()
