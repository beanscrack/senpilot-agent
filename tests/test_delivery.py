from email.message import EmailMessage
from regulatory_agent.mail import send


def test_tls_smtp_transmits_mime_message(monkeypatch):
    calls=[]
    class SMTP:
        def __init__(self,host,port,**kwargs): calls.append(('connect',host,port))
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def login(self,user,password): calls.append(('login',user,password))
        def send_message(self,message): calls.append(('sent',message))
    monkeypatch.setenv('AGENT_EMAIL','agent@example.com')
    monkeypatch.setenv('EMAIL_PASSWORD','test-only-password')
    monkeypatch.setenv('SMTP_HOST','smtp.example.com')
    monkeypatch.setenv('SMTP_PORT','465')
    monkeypatch.setattr('smtplib.SMTP_SSL',SMTP)
    message=EmailMessage()
    message['From']='agent@example.com'
    message['To']='user@example.com'
    message.set_content('Documents attached')
    message.add_attachment(b'zip-bytes',maintype='application',subtype='zip',filename='documents.zip')
    send(message)
    assert calls[0]==('connect','smtp.example.com',465)
    assert calls[1]==('login','agent@example.com','test-only-password')
    assert calls[2][1] is message
