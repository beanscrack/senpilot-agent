import email.policy
import imaplib
import os
import smtplib
import ssl
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import formatdate, make_msgid, parseaddr
from html import unescape
from pathlib import Path
import re
from .models import Result


def read_email(data: bytes) -> tuple[EmailMessage, str]:
    message = BytesParser(policy=email.policy.default).parsebytes(data)
    part = message.get_body(preferencelist=("plain", "html"))
    body = part.get_content() if part else ""
    if part and part.get_content_type() == "text/html":
        body = unescape(re.sub(r"<[^>]+>", " ", body))
    return message, str(message.get("Subject", "")) + "\n" + body


def reply_address(message: EmailMessage) -> str:
    address = parseaddr(str(message.get("From", "")))[1]
    if not address or "@" not in address or any(c in address for c in "\r\n"):
        raise ValueError("Email has no valid From address")
    # Reply only to the sender, never to destinations requested in the body.
    return address


def should_ignore(message: EmailMessage, own_address: str = "") -> bool:
    if message.get("Auto-Submitted", "no").lower() != "no":
        return True
    if message.get("Precedence", "").lower() in {"bulk", "list", "junk"}:
        return True
    sender = parseaddr(str(message.get("From", "")))[1].lower()
    return bool(own_address and sender == own_address.lower())


def summary(result: Result) -> str:
    meta = result.metadata
    lines = ["Hi,", "", f"{result.request.matter}: {meta.get('title', 'Title unavailable')}", ""]
    for key, label in [("status", "Status"), ("type", "Type"), ("category", "Category"),
                       ("date_received", "Date received"), ("decision_date", "Decision date"), ("outcome", "Outcome")]:
        if meta.get(key):
            lines.append(f"{label}: {meta[key]}")
    lines += ["", f"Total documents in this matter: {result.total}"]
    lines += [f"- {category}: {count}" for category, count in result.counts.items()]
    available = result.counts[result.request.category]
    lines += ["", f"Downloaded {len(result.documents)} of {available} files from {result.request.category} (maximum 10)."]
    if result.documents:
        lines += ["The ZIP attachment contains the documents and a manifest with source details and SHA-256 checksums."]
    else:
        lines += ["No ZIP is attached because no files were downloaded."]
    if result.failures:
        lines += ["", "Some files could not be included:"] + [f"- {x}" for x in result.failures]
    lines += ["", f"Source: {result.source_url}", f"Retrieved: {result.retrieved_at}", "", "Regulatory Document Agent"]
    return "\n".join(lines)


def make_reply(recipient: str, subject: str, body: str, archive: Path | None = None,
               original: EmailMessage | None = None, sender: str | None = None) -> EmailMessage:
    message = EmailMessage()
    message["From"] = sender or os.getenv("AGENT_EMAIL") or "agent@localhost"
    message["To"] = recipient
    message["Subject"] = subject
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = make_msgid()
    message["Auto-Submitted"] = "auto-replied"
    if original and original.get("Message-ID"):
        message["In-Reply-To"] = str(original["Message-ID"])
        message["References"] = str(original["Message-ID"])
    message.set_content(body)
    if archive:
        message.add_attachment(archive.read_bytes(), maintype="application", subtype="zip", filename=archive.name)
    return message


def send(message: EmailMessage):
    host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    port = int(os.getenv("SMTP_PORT", "465"))
    username = os.environ["AGENT_EMAIL"]
    password = os.environ["EMAIL_PASSWORD"]
    context = ssl.create_default_context()
    if port == 465:
        client = smtplib.SMTP_SSL(host, port, context=context, timeout=60)
    else:
        client = smtplib.SMTP(host, port, timeout=60)
        client.ehlo()
        client.starttls(context=context)
        client.ehlo()
    with client:
        client.login(username, password)
        client.send_message(message)


def connect_imap():
    client = imaplib.IMAP4_SSL(os.getenv("IMAP_HOST", "imap.gmail.com"), int(os.getenv("IMAP_PORT", "993")), timeout=60)
    client.login(os.environ["AGENT_EMAIL"], os.environ["EMAIL_PASSWORD"])
    status, _ = client.select(os.getenv("IMAP_FOLDER", "INBOX"))
    if status != "OK":
        client.logout()
        raise RuntimeError("Could not select the agent inbox")
    return client
