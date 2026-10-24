import base64
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
import json
from pathlib import Path
from types import SimpleNamespace
import pytest

from regulatory_agent.gmail import BASE_URL, SCOPES, GmailMailbox, authorize, load_credentials, save_credentials
from regulatory_agent.mail import DeliveryError, send
from regulatory_agent.worker import State, watch_email
from google.auth.exceptions import RefreshError


class Credentials:
    valid = True
    refresh_token = 'fake-refresh-token'

    def to_json(self):
        return json.dumps({'token':'fake-token','scopes':SCOPES})

    def has_scopes(self, scopes):
        return scopes == SCOPES


class Session:
    def __init__(self, responses):
        self.responses=list(responses)
        self.calls=[]
        self.closed=False

    def request(self, method, url, **kwargs):
        self.calls.append((method,url,kwargs))
        data=self.responses.pop(0)
        return SimpleNamespace(ok=True,status_code=200,json=lambda:data)

    def close(self):
        self.closed=True


@pytest.fixture(autouse=True)
def account(monkeypatch):
    monkeypatch.setenv('AGENT_EMAIL','agent@example.com')


def test_gmail_mime_roundtrip_and_mark_read():
    original=EmailMessage()
    original['From']='user@example.com'
    original['To']='agent@example.com'
    original.set_content('M12205 Exhibits')
    raw=base64.urlsafe_b64encode(original.as_bytes()).decode().rstrip('=')
    session=Session([{'emailAddress':'agent@example.com'}, {'messages':[{'id':'abc'}]},
                     {'raw':raw},{'id':'abc'},{'id':'sent-1'}])
    mailbox=GmailMailbox(credentials=Credentials(),session=session,persist=False)
    assert mailbox.unread()==['abc']
    assert mailbox.fetch('abc')==original.as_bytes()
    mailbox.mark_read('abc')
    reply=EmailMessage()
    reply['To']='user@example.com'
    reply.set_content('Files attached')
    reply.add_attachment(b'real-zip-payload',maintype='application',subtype='zip',filename='docs.zip')
    mailbox.send(reply)
    assert session.calls[1][2]['params']['labelIds']==['INBOX','UNREAD']
    assert session.calls[3][2]['json']=={'removeLabelIds':['UNREAD']}
    payload=session.calls[4][2]['json']['raw']
    decoded=BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(payload))
    assert list(decoded.iter_attachments())[0].get_payload(decode=True)==b'real-zip-payload'
    assert session.calls[4][1]==BASE_URL+'messages/send'
    assert mailbox.key('abc')=='gmail-api/agent@example.com/abc'
    mailbox.close()
    assert session.closed


def test_wrong_google_account_is_rejected_and_session_closed():
    session=Session([{'emailAddress':'different@example.com'}])
    with pytest.raises(ValueError,match='configured for'):
        GmailMailbox(credentials=Credentials(),session=session,persist=False)
    assert session.closed


def test_api_errors_do_not_include_response_secrets():
    session=Session([{'emailAddress':'agent@example.com'}])
    mailbox=GmailMailbox(credentials=Credentials(),session=session,persist=False)
    session.request=lambda *a,**k:SimpleNamespace(ok=False,status_code=403,text='secret-token')
    with pytest.raises(RuntimeError) as exc:
        mailbox.unread()
    assert '403' in str(exc.value)
    assert 'secret-token' not in str(exc.value)


def test_missing_signin_is_actionable(tmp_path,monkeypatch):
    monkeypatch.setenv('GOOGLE_TOKEN_FILE',str(tmp_path/'missing.json'))
    with pytest.raises(ValueError,match='authorize-google'):
        load_credentials()


def test_expired_access_token_is_refreshed_and_persisted(tmp_path,monkeypatch):
    token=tmp_path/'token.json'
    token.write_text('{}')
    monkeypatch.setenv('GOOGLE_TOKEN_FILE',str(token))
    class Expired(Credentials):
        valid=False
        def refresh(self,request): self.valid=True
    credentials=Expired()
    monkeypatch.setattr('regulatory_agent.gmail.Credentials.from_authorized_user_file',lambda _:credentials)
    monkeypatch.setattr('regulatory_agent.gmail.GoogleRequest',lambda:object())
    assert load_credentials() is credentials
    assert credentials.valid
    assert json.loads(token.read_text())['scopes']==SCOPES


def test_revoked_token_requests_signin_without_exposing_token(tmp_path,monkeypatch):
    token=tmp_path/'token.json'
    token.write_text('{}')
    monkeypatch.setenv('GOOGLE_TOKEN_FILE',str(token))
    class Revoked(Credentials):
        valid=False
        def refresh(self,request): raise RefreshError('do-not-expose-token')
    monkeypatch.setattr('regulatory_agent.gmail.Credentials.from_authorized_user_file',lambda _:Revoked())
    monkeypatch.setattr('regulatory_agent.gmail.GoogleRequest',lambda:object())
    with pytest.raises(ValueError) as exc:
        load_credentials()
    assert 'authorize-google' in str(exc.value)
    assert 'do-not-expose-token' not in str(exc.value)


def test_saved_token_must_have_the_real_required_scope(tmp_path,monkeypatch):
    token=tmp_path/'token.json'
    token.write_text('{}')
    monkeypatch.setenv('GOOGLE_TOKEN_FILE',str(token))
    class ReadOnly(Credentials):
        def has_scopes(self,scopes): return False
    monkeypatch.setattr('regulatory_agent.gmail.Credentials.from_authorized_user_file',lambda _:ReadOnly())
    with pytest.raises(ValueError,match='permission is incomplete'):
        load_credentials()


def test_tokens_are_saved_atomically_without_temp_files(tmp_path):
    destination=tmp_path/'private'/'token.json'
    save_credentials(Credentials(),destination)
    assert json.loads(destination.read_text())['scopes']==SCOPES
    assert list(destination.parent.iterdir())==[destination]


def test_google_flow_uses_pkce_loopback_and_validates_before_save(tmp_path,monkeypatch):
    client=tmp_path/'client.json'
    client.write_text(json.dumps({'installed':{'client_id':'test-client'}}))
    token=tmp_path/'token.json'
    monkeypatch.setenv('GOOGLE_TOKEN_FILE',str(token))
    calls={}
    class Flow:
        def run_local_server(self,**kwargs):
            calls.update(kwargs)
            return Credentials()
    def from_config(config,scopes,**kwargs):
        assert config['installed']['client_id']=='test-client'
        assert scopes==SCOPES
        assert kwargs['autogenerate_code_verifier'] is True
        return Flow()
    monkeypatch.setattr('regulatory_agent.gmail.InstalledAppFlow.from_client_config',from_config)
    class Box:
        def __init__(self,**kwargs):
            assert not token.exists()
            assert kwargs['persist'] is False
            self.address='agent@example.com'
        def close(self): pass
    monkeypatch.setattr('regulatory_agent.gmail.GmailMailbox',Box)
    assert authorize(client,log=lambda _:None)=='agent@example.com'
    assert token.exists()
    assert calls['host']=='127.0.0.1' and calls['port']==0
    assert calls['access_type']=='offline' and calls['prompt']=='consent'


def test_gmail_send_failure_is_delivery_error(monkeypatch):
    monkeypatch.setenv('EMAIL_TRANSPORT','gmail-api')
    class Box:
        def send(self,message): raise RuntimeError('Connection interrupted')
        def close(self): pass
    monkeypatch.setattr('regulatory_agent.gmail.GmailMailbox',Box)
    with pytest.raises(DeliveryError):
        send(EmailMessage())


def test_worker_gmail_mode_needs_no_password_and_does_not_duplicate_reply(tmp_path,monkeypatch):
    monkeypatch.setenv('EMAIL_TRANSPORT','gmail-api')
    monkeypatch.delenv('EMAIL_PASSWORD',raising=False)
    monkeypatch.delenv('ALLOWED_SENDERS',raising=False)
    request=EmailMessage()
    request['From']='user@example.com'
    request.set_content('M12205 Other Documents')
    class Box:
        marks=0
        def unread(self): return ['abc']
        def key(self,uid): return 'gmail-api/agent@example.com/abc'
        def fetch(self,uid): return request.as_bytes()
        def mark_read(self,uid):
            self.marks+=1
            if self.marks==1: raise RuntimeError('label failed')
        def close(self): pass
    box=Box()
    monkeypatch.setattr('regulatory_agent.worker.open_inbox',lambda:box)
    handled=[]
    def handle(data,output,**kwargs):
        assert kwargs['deliver'] is True
        handled.append(data)
        return tmp_path/'reply.eml'
    monkeypatch.setattr('regulatory_agent.worker.handle',handle)
    watch_email(tmp_path,once=True,log=lambda _:None)
    assert State(tmp_path/'email-state.sqlite3').status(box.key('abc'))=='sent'
    watch_email(tmp_path,once=True,log=lambda _:None)
    assert len(handled)==1
