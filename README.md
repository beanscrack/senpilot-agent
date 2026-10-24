# Regulatory Document Agent

An email-driven agent that retrieves public Nova Scotia regulatory documents. It accepts a matter number and a document type, navigates UARB's FileMaker website, downloads up to ten files, and produces a ZIP and a reply with current matter metadata and counts across all five categories.

## Run the local demo

The environment on this machine is already installed. From PowerShell:

```powershell
cd C:\Users\fortn\senpilot-agent
.\.venv\Scripts\python.exe -m regulatory_agent demo
```

Open **http://127.0.0.1:8765**. Paste an email request and press **Fetch documents**. The demo shows progress and offers the ZIP, the manifest, and a complete `.eml` email containing the actual ZIP attachment. It fetches live documents; it does not send external email.

`start-demo.ps1` is an alternative launcher. Keep the process running while recording or using the demo. Use `--headed` to display the browser's navigation:

```powershell
.\.venv\Scripts\python.exe -m regulatory_agent demo --headed
```

On a fresh machine with Python 3.11 or newer:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[dev]'
.\.venv\Scripts\python.exe -m playwright install chromium
```

On macOS/Linux, use `.venv/bin/python` in place of `.\.venv\Scripts\python.exe`; Chromium may require `python -m playwright install --with-deps chromium`.

## Command-line and local email modes

```powershell
# Natural-language request; no inbox or model credentials required.
.\.venv\Scripts\python.exe -m regulatory_agent fetch --request "Other Documents from M12205"

# Explicit arguments and visible browser.
.\.venv\Scripts\python.exe -m regulatory_agent fetch --matter M12383 --type Exhibits --limit 3 --headed

# Process a real MIME email file and create a reply locally.
.\.venv\Scripts\python.exe -m regulatory_agent email-file examples/request.eml

# Watch a directory for .eml requests. Saved replies appear under output/.
.\.venv\Scripts\python.exe -m regulatory_agent local-inbox --inbox inbox
```

Supported types: **Exhibits**, **Key Documents**, **Other Documents**, **Transcripts**, **Recordings**. One matter and one type per email. Missing or ambiguous values produce a clarification reply instead of guessing.

Each completed request has its own folder under `output/`:

- `documents/`: downloaded original files.
- `manifest.json`: metadata, all category counts, total count, per-file hashes, retrieval time, and failures.
- `<matter>-<type>.zip`: downloaded files plus the manifest; omitted when there are no downloads.
- `reply.eml`: complete MIME message including the ZIP attachment.
- `reply.txt`: readable email body.
- `source.png`: screenshot of the matter's document page for debugging.

Failed runs preserve `error.json` and, when available, `error.png` and `error.html`.

## Connect a real email address

Live email handling supports **Google sign-in through the Gmail API** and **IMAP/SMTP with an app password**. The local demo works independently of either transport.

If app passwords are unavailable, follow [GOOGLE-SETUP.md](GOOGLE-SETUP.md). It covers the Google Desktop app OAuth client, browser sign-in, authentication check, and worker startup. The agent validates the signed-in mailbox and refreshes tokens automatically. No Gmail password is used in this mode. Live OAuth access and delivery remain unverified until a client is provided and browser authorization is completed.

For the IMAP/SMTP option:

1. Create a dedicated inbox for the agent.
2. Copy `.env.example` to `.env` and enter `AGENT_EMAIL` and `EMAIL_PASSWORD` locally. The default hosts are Gmail's IMAP and SMTP servers.
3. Set `EMAIL_TRANSPORT=imap-smtp`. For Gmail, use an [app password](https://support.google.com/mail/answer/185833), which requires two-step verification and may be restricted for some accounts.
4. Start the worker:

```powershell
.\.venv\Scripts\python.exe -m regulatory_agent email
```

Send `Hi Agent, can you give me Other Documents files from M12205?` to the configured address. The worker processes **unread** messages, replies to the original sender, then marks the message read. Use a new inbox: existing unread messages will also be considered. `--once` processes one polling batch and exits.

`check-email` verifies authentication without sending or marking messages read. `ALLOWED_SENDERS` optionally restricts the worker to a comma-separated list of sender addresses. `POLL_SECONDS` controls the interval, with a minimum of ten seconds. Leave the machine and process running for the agent address to remain responsive; a remote evaluator cannot reach a local `.eml` inbox.

The worker stores durable processing states in SQLite. Completed messages are not processed again. Failed or interrupted messages require operator inspection before retrying: this avoids resending after an uncertain SMTP result. Inspect `output/email-state.sqlite3` with SQLite and delete the specific failed message row only after checking whether a reply was delivered. The local inbox has a separate state database.

## Approach

The workflow is deliberately bounded:

`email -> validated request -> browser navigation -> metadata/counts -> downloads -> ZIP -> MIME reply`

- `requests.py` extracts the matter/type. Standard assignment phrasing uses a deterministic parser, so the local demo needs no API key and does not use a language model by default.
- An optional local **Ollama** adapter handles phrasing beyond the built-in aliases. Set `OLLAMA_MODEL` to an installed model and optionally `OLLAMA_URL`. Its structured output is validated against the matter supplied in the email and the five allowed categories. Missing matter numbers and multiple explicit categories still require clarification. See the [Ollama chat API](https://docs.ollama.com/api/chat).
- `uarb.py` handles FileMaker's delayed editable widgets, matter validation, category counts, virtualized rows, and the extra download dialog behind **GO GET IT**. It uses browser interactions rather than an undocumented backend API. The two download steps follow [Playwright's download handling](https://playwright.dev/python/docs/api/class-download).
- `workflow.py` packages the files and creates an email from observed facts. A language model does not invent summaries, counts, or metadata.
- `mail.py`, `inbox.py`, `gmail.py`, and `worker.py` provide MIME handling, IMAP/SMTP or Gmail API transport, Google OAuth sign-in, and durable deduplication.
- `demo.py` exposes the same retrieval workflow through a loopback-only local UI.

The largest discovery was that GO GET IT opens a second dialog; clicking the filename in that dialog triggers the actual download. Also, different categories use different FileMaker object IDs for document numbers and download buttons. The adapter uses row structure and button text for those fields.

## Limits and failure behavior

- Maximum ten documents per request. Files follow the site's current list order; counts come from all five matter tabs, independently of the download limit.
- Only rows explicitly marked **Public** are downloaded. Empty categories produce a factual reply with counts and no ZIP.
- Individual download failures retry once and are listed in the manifest and email. Partial results remain usable.
- A 17 MiB uncompressed file budget leaves room for ZIP/MIME overhead under common 25 MB message limits. Files that exceed it are omitted and reported. Large recordings may therefore need a different delivery mechanism. A large file is downloaded before its size can be checked; this is a known disk/bandwidth limit.
- Filename sanitization, per-run directories, SHA-256 hashes, download timeouts, and HTML-error-page detection protect output integrity.
- Auto-replies, mailing-list traffic, and self-sent mail are ignored to prevent reply loops. Email content cannot select a different website, shell command, or response recipient.
- This is a single-worker prototype. Site layout changes can require updates to the adapter. Live OAuth authorization and mailbox delivery, hosted operation, and positive transcript/recording downloads have not been verified.

## Verification

```powershell
.\.venv\Scripts\python.exe -m pytest -q --basetemp tmp/pytest-local

# Explicit live UI smoke test; keep the demo server running in another terminal.
.\.venv\Scripts\python.exe scripts/verify_demo.py
```

The automated tests cover request validation, ambiguous requests, quoted email handling, MIME parsing/threading, clarification messages, durable state, count parsing, safe filenames, ZIP/MIME integrity, mocked TLS SMTP transmission, Gmail API request/response handling, account validation, OAuth setup/credential storage, delivery deduplication, and validation of optional model output. The optional local model itself has not been run.

Live verification on October 23, 2026 (Toronto time):

- M12205 / Other Documents: ten actual PDFs downloaded, ZIP extracted and checksums verified, email attachment bytes matched the ZIP. Observed counts: 13 Exhibits, 6 Key Documents, 43 Other Documents, 0 Transcripts, 0 Recordings; 62 total.
- M12205 / Key Documents: a document downloaded successfully.
- M12383 / Exhibits: a document downloaded successfully.
- M12205 / Transcripts: empty category returned metadata/counts without an attachment.
- M99999: returned a not-found failure without downloading documents.
- Browser demo: form submission, live progress, ZIP and `.eml` downloads, desktop/mobile layout, and no JavaScript errors.

Counts and metadata may change after verification. The assignment's sample is not hardcoded.

## For the submission

Record the local demo, show the downloaded ZIP and reply, then explain the browser adapter and component boundaries. Connect the dedicated email inbox and keep the worker running before submitting an address for evaluators. Publish this source folder to a repository and share its URL. The confidential assignment PDF, credentials, downloaded documents, and local output folders are not included in source control.
