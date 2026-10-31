# Mail (IMAP) setup

The `mail` service connects each workspace to the mailboxes it needs (a workspace can have several) and shows them in the
app. Mail stays on the mail server: nothing is copied into Stiamo's database except the connection details, and the
password is encrypted.

## How it stays live

It does not poll. For a mailbox that is open on screen the service holds **one IMAP connection in IDLE** (RFC 2177): the
server pushes `EXISTS` / `EXPUNGE` / `FETCH` the moment something changes, the service forwards a `changed` event to the
browser over server-sent events (`GET /v1/mail/accounts/{id}/events?folder=`), and the browser re-reads the list.

- The first viewer starts the connection, further viewers (other tabs) share it, the last one leaving closes it.
- IDLE is re-issued every 25 minutes (servers drop silent connections at ~30), and a broken connection is rebuilt with a
  growing pause (2 s up to 2 min). The screen shows the state: `connecting`, `live`, `down`, `auth` (wrong password).
- Everything else (folders, list pages, a message, search) is a short connection made when asked.
- Run **one replica** of `mail`: the IDLE connections live in the process.

## Safety rules it keeps

- Apart from two flags (seen, flagged) the only change it can make is **moving messages to the Trash folder** (used by the
  spam-filter agent, below). It can never delete mail for good or move it anywhere else: the provider keeps Trash for a
  while, so anything moved by mistake can be put back from the mailbox. Bodies are fetched with `BODY.PEEK`, so reading
  never marks anything seen on its own.
- Passwords are sealed with `MAIL_SECRET_KEY` and never returned by the API.
- Servers on private networks (localhost, 10.x, 192.168.x, link-local) are refused, so the service can't be used to probe
  the machines behind it. `MAIL_ALLOW_PRIVATE_HOSTS=true` is for development and refused in production.
- Mail is hostile input: HTML is cleaned (no scripts, forms, frames, event handlers), images that travel inside the
  message are inlined, remote images are not loaded unless the person asks, and the app shows it in a sandboxed frame.
  Attachments are only ever served as downloads.

## Connecting a mailbox

Use the provider's IMAP address, port 993 with SSL (or 143 with STARTTLS) and an **app password**, not the main password:

| Provider | Server | Notes |
|---|---|---|
| Gmail / Google Workspace | `imap.gmail.com` | turn on 2-step verification, then create an App password |
| Microsoft 365 / Outlook | `outlook.office365.com` | basic auth must be allowed by the tenant; otherwise OAuth is needed (not supported yet) |
| Zoho | `imap.zoho.com` | app-specific password |
| Others | their IMAP address | |

## Deploying

1. Add to `.env` on the VPS: `MAIL_DB_PASSWORD=$(openssl rand -hex 24)` and `MAIL_SECRET_KEY` (a Fernet key:
   `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`).
2. Re-download `docker-compose.yml`, `add-databases.sh`, `deploy.sh`; then `./add-databases.sh && ./deploy.sh`.
3. Losing `MAIL_SECRET_KEY` only means entering the mailbox passwords again.

## Filtering a mailbox with an agent

A workflow agent can read a mailbox (source `mail`: the mailbox and folder), keep what matters and move the rest to Trash.
It is built in the app (Agents, New agent, "Mailbox spam filter") and works like the others in
[WORKFLOW_AGENTS.md](WORKFLOW_AGENTS.md):

1. **Rules in code first.** Mail from protected domains, mail mentioning protected phrases, and mail from anyone the person
   has written to (read from the Sent folder) is never judged: it stays.
2. **The decision model (Jev) or the agent's own model** judges the rest as `keep` or `spam` and says how sure it is.
3. **Only when confident** (the threshold, 85% by default) is the mail moved to Trash. Unsure mail stays and waits in
   Review. Moving can be set to wait for approval instead.
4. A **test run never moves anything**. Mail already judged is not judged again. Each run looks at a limited number of
   new messages (50 by default), newest first.

The agent service reaches this service at `AGENT_MAIL_URL` (default `http://mail:8000`) with the owner's own access token.
