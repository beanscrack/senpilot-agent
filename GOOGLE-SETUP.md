# Connect the agent with Google sign-in

Use this when Google app passwords are unavailable. No Gmail password is needed.
The agent uses Google's maintained desktop OAuth library and the Gmail API.

## One-time Google setup

1. Sign into [Google Cloud Console](https://console.cloud.google.com/) and select or create a project for the agent, for example **Regulatory Document Agent**.
2. In that project, [enable the Gmail API](https://console.cloud.google.com/apis/library/gmail.googleapis.com).
3. Open [Google Auth Platform](https://console.cloud.google.com/auth/overview). If prompted to get started, enter an app name and your contact/support email. For a personal Gmail account, select **External** as the audience. Keep the app in **Testing** and add **j692wu@gmail.com** under **Audience > Test users**. If you use a different agent address, add that address instead.
4. Under **Data Access**, add the scope `https://www.googleapis.com/auth/gmail.modify`. This lets the agent read requests, send responses, and mark processed messages read. It does not grant immediate permanent deletion. These capabilities apply to the signed-in mailbox, so use the agent's dedicated inbox.
5. Open [Clients](https://console.cloud.google.com/auth/clients), choose **Create client**, and select **Desktop app**. Name it **Regulatory Agent Local**. Download its JSON file.
6. Save the downloaded file at:

   ```text
   C:\Users\fortn\senpilot-agent\.secrets\google-client.json
   ```

The `.secrets` folder is already created and excluded from Git. Do not paste client JSON, passwords, authorization codes, or tokens into chat.

Official reference: [Google's Gmail Python quickstart](https://developers.google.com/workspace/gmail/api/quickstart/python). For personal Gmail, use the External audience and add the agent account as a test user.

## Sign into the agent

The local `.env` has been configured to use `EMAIL_TRANSPORT=gmail-api`. Its existing `AGENT_EMAIL` identifies the expected account. `EMAIL_PASSWORD` is not read in this mode.

From PowerShell:

```powershell
cd C:\Users\fortn\senpilot-agent
.\.venv\Scripts\python.exe -m regulatory_agent authorize-google
```

A browser window opens for Google sign-in. Select **j692wu@gmail.com** and grant the Gmail permission. The browser returns to a temporary local callback on this computer. The command validates the actual mailbox against `AGENT_EMAIL` before saving the sign-in. It times out after five minutes if sign-in is not completed.

The authorization is stored in `.secrets/google-token.json`. The agent refreshes access tokens automatically. Google may still require sign-in again after access is revoked or expires. For an External app in Testing, refresh tokens with Gmail scopes generally expire after seven days; run `authorize-google` again if needed. See [Google's token expiration rules](https://developers.google.com/identity/protocols/oauth2#expiration). Account or administrator restrictions may also block OAuth and must be resolved with the account administrator.

## Verify and start receiving email

```powershell
# Checks the authenticated account and inbox access; sends nothing.
.\.venv\Scripts\python.exe -m regulatory_agent check-email

# Starts the email worker. Keep this running.
.\.venv\Scripts\python.exe -m regulatory_agent email
```

From a **different email address**, send this to **j692wu@gmail.com**:

```text
Hi Agent, can you give me Other Documents files from M12205?
```

Leave the request unread in the inbox until the worker picks it up. The reply should arrive at the sending address with a ZIP and current matter information. Self-sent emails and automatic replies are ignored to prevent loops. The machine must remain awake and the worker must remain running.

## If Google reports an error

- **Access blocked / test user:** add the exact agent account under Audience > Test users in the same project as the downloaded client.
- **Gmail API disabled:** enable Gmail API in that project.
- **Wrong account:** rerun sign-in and select the account configured in `AGENT_EMAIL`.
- **Permission incomplete:** rerun sign-in and grant the requested Gmail permission.
- **Desktop app required:** download a Desktop app client, rather than a web client or service account.
- **Sign-in expired/revoked:** rerun `authorize-google`. It does not require an app password.

The OAuth adapter and MIME/deduplication behavior are covered by automated tests. Live OAuth sign-in and delivery still require your client JSON and browser authorization before they can be verified.
