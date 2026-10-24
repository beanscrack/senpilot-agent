import argparse
import asyncio
from pathlib import Path
import sys
from dotenv import load_dotenv
from .requests import parse_request, validate


def main():
    # Do not accidentally load credentials from an unrelated parent project.
    load_dotenv(dotenv_path=Path.cwd() / ".env")
    parser = argparse.ArgumentParser(description="UARB regulatory document agent")
    sub = parser.add_subparsers(dest="command", required=True)
    fetch = sub.add_parser("fetch", help="Fetch documents and create a local email reply")
    fetch.add_argument("--request", help='Email text, e.g. "Other Documents from M12205"')
    fetch.add_argument("--matter")
    fetch.add_argument("--type", dest="category")
    fetch.add_argument("--limit", type=int, default=10, choices=range(1,11))
    fetch.add_argument("--to", default="demo@localhost", help="Recipient written to the preview (no email is sent)")
    local = sub.add_parser("local-inbox", help="Process .eml files without sending external email")
    local.add_argument("--inbox", type=Path, default=Path("inbox"))
    local.add_argument("--once", action="store_true")
    email = sub.add_parser("email", help="Listen via IMAP and send replies via SMTP")
    email.add_argument("--once", action="store_true")
    eml = sub.add_parser("email-file", help="Process one local .eml and write a reply preview")
    eml.add_argument("path", type=Path)
    serve = sub.add_parser("demo", help="Start the local demo in a browser")
    serve.add_argument("--port", type=int, default=8765)
    authorize = sub.add_parser("authorize-google", help="Connect Gmail through Google browser sign-in")
    authorize.add_argument("--client", type=Path, help="Downloaded Desktop app OAuth client JSON")
    sub.add_parser("check-email", help="Check email authentication without sending or changing messages")
    sub.add_parser("check-llm", help="Verify a real model interpretation without email or downloads")
    for item in (fetch, local, email, eml, serve):
        item.add_argument("--output", type=Path, default=Path("output"))
        item.add_argument("--headed", action="store_true", help="Show browser navigation")
    args = parser.parse_args()
    def log(text):
        print(text, flush=True)
    try:
        if args.command == "fetch":
            from .workflow import run
            request = parse_request(args.request) if args.request else validate(args.matter or "", args.category or "")
            asyncio.run(run(request, args.output, headed=args.headed, limit=args.limit, recipient=args.to, log=log))
        elif args.command == "email-file":
            from .worker import handle
            handle(args.path.read_bytes(), args.output, headed=args.headed, log=log)
        elif args.command == "local-inbox":
            from .worker import watch_local
            watch_local(args.inbox, args.output, once=args.once, headed=args.headed, log=log)
        elif args.command == "email":
            from .worker import watch_email
            watch_email(args.output, once=args.once, headed=args.headed, log=log)
        elif args.command == "demo":
            from .demo import serve
            serve(args.output, port=args.port, headed=args.headed)
        elif args.command == "authorize-google":
            from .gmail import authorize
            authorize(args.client, log=log)
        elif args.command == "check-email":
            from .mail import check_connection
            check_connection(log=log)
        elif args.command == "check-llm":
            from .llm import configured_provider
            if configured_provider() != "openai-compatible":
                raise ValueError("Set LLM_PROVIDER=openai-compatible before running check-llm")
            request = parse_request("Please get the miscellaneous filings for M12205", log=log, allow_fallback=False)
            if request.parser != "openai-compatible" or request.category != "Other Documents":
                raise ValueError("The model did not interpret the verification request correctly")
            log(f"Model check passed: {request.model} interpreted {request.matter} / {request.category}")
    except KeyboardInterrupt:
        print("Stopped.", flush=True)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr, flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
