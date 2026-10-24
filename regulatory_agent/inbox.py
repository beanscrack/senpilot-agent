"""Mailbox adapter shared by password and OAuth transports."""
import os
from .mail import connect_imap, transport


class ImapMailbox:
    def __init__(self):
        self.client = connect_imap()
        validity = self.client.response("UIDVALIDITY")[1]
        self.account = f"{os.environ['AGENT_EMAIL']}/{os.getenv('IMAP_FOLDER','INBOX')}/{validity}"

    def unread(self):
        status, data = self.client.uid("search", None, "UNSEEN")
        if status != "OK":
            raise RuntimeError("Inbox search failed")
        return data[0].split()[:10]

    def key(self, uid):
        return f"{self.account}/{uid.decode()}"

    def fetch(self, uid):
        status, raw = self.client.uid("fetch", uid, "(BODY.PEEK[])")
        if status != "OK":
            raise RuntimeError("Email fetch failed")
        return next(item[1] for item in raw if isinstance(item, tuple))

    def mark_read(self, uid):
        status, _ = self.client.uid("store", uid, "+FLAGS", "(\\Seen)")
        if status != "OK":
            raise RuntimeError("Could not mark processed email read")

    def close(self):
        self.client.logout()


def open_inbox():
    if transport() == "gmail-api":
        from .gmail import GmailMailbox
        return GmailMailbox()
    return ImapMailbox()
