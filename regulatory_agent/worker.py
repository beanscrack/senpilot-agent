"""Single-worker email intake with durable status and conservative retry behavior."""
import asyncio
import hashlib
import os
from pathlib import Path
import sqlite3
import time
from email.parser import BytesParser
import email.policy
from .mail import connect_imap, make_reply, read_email, reply_address, send, should_ignore
from .requests import ClarificationNeeded, parse_request
from .workflow import run


class State:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute("CREATE TABLE IF NOT EXISTS messages (id TEXT PRIMARY KEY, status TEXT, detail TEXT, updated TEXT DEFAULT CURRENT_TIMESTAMP)")
        self.db.commit()

    def status(self, key):
        row = self.db.execute("SELECT status FROM messages WHERE id=?", (key,)).fetchone()
        return row[0] if row else None

    def set(self, key, status, detail=""):
        self.db.execute("INSERT INTO messages(id,status,detail) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET status=excluded.status,detail=excluded.detail,updated=CURRENT_TIMESTAMP", (key, status, detail))
        self.db.commit()


def handle(data: bytes, output: Path, *, deliver=False, headed=False, log=print):
    message, text = read_email(data)
    recipient = reply_address(message)
    try:
        request = parse_request(text)
    except ClarificationNeeded as exc:
        reply = make_reply(recipient, "Re: Please clarify your document request", str(exc), original=message)
        output.mkdir(parents=True, exist_ok=True)
        path = output / f"clarification-{hashlib.sha256(data).hexdigest()[:12]}.eml"
        path.write_bytes(reply.as_bytes())
        log(f"Clarification: {exc}")
        if deliver:
            send(reply)
        return path
    artifacts = asyncio.run(run(request, output, headed=headed, recipient=recipient, original=message, log=log))
    if deliver:
        reply = BytesParser(policy=email.policy.default).parsebytes(artifacts.reply.read_bytes())
        send(reply)
        log(f"Email sent to {recipient}")
    return artifacts.reply


def watch_local(inbox: Path, output: Path, *, once=False, headed=False, log=print):
    inbox.mkdir(parents=True, exist_ok=True)
    state = State(output / "local-state.sqlite3")
    log(f"Local inbox: {inbox.resolve()} (drop .eml files here)")
    while True:
        for path in sorted(inbox.glob("*.eml")):
            data = path.read_bytes()
            key = hashlib.sha256(data).hexdigest()
            if state.status(key):
                continue
            state.set(key, "processing")
            try:
                message, _ = read_email(data)
                if should_ignore(message):
                    state.set(key, "ignored")
                    continue
                reply = handle(data, output, headed=headed, log=log)
                state.set(key, "complete", str(reply))
            except Exception as exc:
                state.set(key, "failed", str(exc))
                log(f"Failed {path.name}: {exc}")
        if once:
            return
        time.sleep(3)


def watch_email(output: Path, *, once=False, headed=False, log=print):
    for key in ("AGENT_EMAIL", "EMAIL_PASSWORD"):
        if not os.getenv(key):
            raise ValueError(f"Set {key} in .env before starting the email worker.")
    state = State(output / "email-state.sqlite3")
    own = os.environ["AGENT_EMAIL"]
    allowed = {x.strip().lower() for x in os.getenv("ALLOWED_SENDERS", "").split(",") if x.strip()}
    log(f"Listening for unread email at {own}")
    while True:
        client = None
        try:
            client = connect_imap()
            validity = client.response("UIDVALIDITY")[1]
            account = f"{own}/{os.getenv('IMAP_FOLDER', 'INBOX')}/{validity}"
            status, data = client.uid("search", None, "UNSEEN")
            if status != "OK":
                raise RuntimeError("Inbox search failed")
            for uid in data[0].split()[:10]:
                key = f"{account}/{uid.decode()}"
                current = state.status(key)
                if current in {"sent", "ignored"}:
                    client.uid("store", uid, "+FLAGS", "(\\Seen)")
                    continue
                if current:
                    # A crash during SMTP may have delivered a reply. Do not resend automatically.
                    log(f"Skipping UID {uid.decode()} with status {current}; inspect state before retrying")
                    continue
                status, raw = client.uid("fetch", uid, "(BODY.PEEK[])")
                if status != "OK":
                    raise RuntimeError("Email fetch failed")
                payload = next(x[1] for x in raw if isinstance(x, tuple))
                message, _ = read_email(payload)
                sender = reply_address(message)
                if should_ignore(message, own) or (allowed and sender.lower() not in allowed):
                    state.set(key, "ignored")
                    client.uid("store", uid, "+FLAGS", "(\\Seen)")
                    continue
                state.set(key, "processing")
                try:
                    reply = handle(payload, output, deliver=True, headed=headed, log=log)
                    state.set(key, "sent", str(reply))
                    client.uid("store", uid, "+FLAGS", "(\\Seen)")
                except Exception as exc:
                    state.set(key, "failed", str(exc))
                    log(f"Request UID {uid.decode()} failed: {exc}")
                    # Retrieval failures are actionable; send a truthful failure response.
                    # Do not send another message after an uncertain SMTP failure.
                    if not isinstance(exc, (OSError,)) and exc.__class__.__module__ != "smtplib":
                        error_reply = make_reply(sender, "Re: Document request could not be completed",
                            "I could not complete your request. No successful download is being claimed.\n"
                            "Please try again later or contact the agent operator.\n\n" + str(exc)[:800], original=message)
                        try:
                            send(error_reply)
                            state.set(key, "sent", "Failure notification")
                            client.uid("store", uid, "+FLAGS", "(\\Seen)")
                        except Exception:
                            log("Failure notification could not be delivered; operator action required")
        except Exception as exc:
            log(f"Mailbox error: {exc}")
            if once:
                raise
        finally:
            if client:
                try:
                    client.logout()
                except Exception:
                    pass
        if once:
            return
        time.sleep(max(10, int(os.getenv("POLL_SECONDS", "20"))))
