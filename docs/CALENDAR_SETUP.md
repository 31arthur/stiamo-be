# Google Calendar in Stiamo

Connect as many Google accounts as you like, each to the workspace it belongs to (your S&B calendar to S&B, your own to Personal…).
In a workspace you see that workspace's calendars; in **All workspaces** you see them all together, each in its own colour.
Everything on your Stiamo schedule can also be sent to a calendar you choose for each workspace, so Google has it too: meetings,
hearings, tasks and follow-ups.

```
Google account ──OAuth (read + write events)──► calendar service ──► the Calendar page (one workspace, or all together)
                                                     ▲   │
                       meetings, hearings, tasks, follow-ups ┘   └──► one chosen Google calendar per workspace
```

## What it can and can't do

- **Reads** every calendar you leave switched on (the main one, shared team calendars, holidays), kept current every 5 minutes
  and whenever you press **Sync now**. It reads from 30 days back to about 13 months ahead, with recurring events unrolled.
- **Sends** to the calendar you choose for each workspace (from a week ago onwards):
  - *Meetings and appointments* at their time. *Hearings* at their time (all-day when they have none), titled `Hearing: purpose (SA-2026-001)`; they go to S&B.
  - *Tasks* that have a time on the planner, at that time; otherwise as an all-day entry on their due date, titled `Task: …`. A task with no
    day isn't sent.
  - *Follow-ups* with a day, at their time or all day, titled `Call: …`, `Message: …` and so on.
  - Tasks and follow-ups that are **done or cancelled come down** from Google. An edit in Stiamo edits the event, and deleting an item in
    Stiamo deletes it. Moving a task on the planner moves it in Google within a few minutes.
  - Google limits how fast events can be written, so a large first run (a long list of tasks) is spread over a few runs of at most 150 each.
- **Doesn't** change events that came from Google. Open them in Google (each has a link). What Stiamo sent is marked, so it is never
  shown twice.
- Access it asks Google for: the list of your calendars, and events (read and write). Nothing else: not your mail, contacts or
  Drive. You can withdraw it at any time at <https://myaccount.google.com/permissions>.
- Refresh tokens are kept sealed with `CALENDAR_SECRET_KEY` and never returned by the API. Disconnecting revokes the token at
  Google and forgets everything read from that account; events Stiamo already sent stay in Google.

## 1. Google Cloud: the OAuth client (about 10 minutes, free)

1. <https://console.cloud.google.com> → project picker → **New project** → `Stiamo` (or use the one you made for Drive).
2. **APIs & Services → Library** → search **Google Calendar API** → **Enable**.
3. **APIs & Services → OAuth consent screen**:
   - User type **External** (use **Internal** if all your accounts are in one Google Workspace organisation).
   - App name `Stiamo`, your email as support and developer contact.
   - **Scopes → Add or remove scopes** → add `.../auth/calendar.calendarlist.readonly` and `.../auth/calendar.events`
     (plus `openid` and `email`, which are added for you).
   - **Test users**: add every Google account you will connect. While the app is in *Testing* only these can sign in, which is
     what you want for a private tool. Note: Google expires refresh tokens of apps in *Testing* after **7 days**. To stop that,
     press **Publish app** (it stays private to you; Google shows an "unverified app" warning once at sign-in: choose
     **Advanced → Go to Stiamo**). Calendar scopes are sensitive, but an app with fewer than 100 users does not need to be verified.
4. **APIs & Services → Credentials → Create credentials → OAuth client ID** → type **Web application**:
   - **Authorised redirect URIs**: `https://stiamo-be.fullstackarthur.com/v1/calendar/oauth/callback`
     (for local development also `http://localhost:8080/v1/calendar/oauth/callback`).
   - **Create**, then copy the **Client ID** and **Client secret**.

## 2. Put it on the server

On the VPS, in `~/stiamo-be`, add to `.env`:

```bash
CALENDAR_DB_PASSWORD=$(openssl rand -hex 24)
CALENDAR_SECRET_KEY=<a Fernet key: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())">
GOOGLE_OAUTH_CLIENT_ID=<the client id>
GOOGLE_OAUTH_CLIENT_SECRET=<the client secret>
CALENDAR_APP_URL=https://stiamo.fullstackarthur.com
```

Then re-download `docker-compose.yml`, `add-databases.sh` and `deploy.sh` (see [DEPLOY.md](DEPLOY.md)) and run:

```bash
./add-databases.sh && ./deploy.sh
```

Check it (expect **401**: the route exists and wants a sign-in):

```bash
curl -s -o /dev/null -w "%{http_code}\n" https://stiamo-be.fullstackarthur.com/v1/calendar/status
```

Losing `CALENDAR_SECRET_KEY` only means connecting the calendars again.

## 3. Connect in Stiamo

Open **Calendar**, then **Calendars** (top right) and **Connect a Google account**. Choose the workspace, sign in to Google, allow
access, and you land back in Stiamo. In the panel you can, for each calendar: show or hide it, change its colour, and (for one
you can write to) choose **Send meetings and hearings here**: one per workspace.

## How it works

- `POST /v1/calendar/oauth/start` returns Google's consent address. The `state` it carries is signed and lasts 15 minutes; it is
  what lets the public callback trust whose account is being connected.
- `GET /v1/calendar/oauth/callback` (public, called by Google through the browser) swaps the code for tokens, finds the account's
  email and calendars, and sends the person back to the app.
- Each calendar keeps Google's changes token, so a read is only what changed; the whole window is read again once a day so it keeps
  moving, and again at once if Google drops the token.
- A background loop (every `CALENDAR_SYNC_MINUTES`, default 5) reads everyone's calendars and sends meetings and hearings, acting as
  their owner with a short token this service signs (the services share an HS256 secret). With `CALENDAR_SYNC_MINUTES=0` nothing
  runs by itself.
- Run a **single replica**: the background loop lives in the process.

## Settings (`CALENDAR_…`)

| Setting | Default | |
| --- | --- | --- |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | — | The OAuth client. Without them nothing can be connected. |
| `PUBLIC_URL` | `http://localhost:8000` | This service's public address; the redirect URI is `{PUBLIC_URL}/v1/calendar/oauth/callback`. |
| `APP_URL` | `http://localhost:5173` | The web app, where people land after connecting. |
| `SECRET_KEY` | — | Fernet key sealing refresh tokens. Required in production. |
| `SYNC_MINUTES` | `5` | Background read interval; `0` turns it off. |
| `ALLOWED_GOOGLE_EMAILS` | any | Comma separated; only these Google accounts can be connected. |
| `DEFAULT_TIMEZONE` | `Asia/Kolkata` | Until the web app reports the person's own (it does, on every sync). |
| `PRACTICE_URL` | `http://practice:8000` | Where meetings and hearings are read from to send them to Google. |

## API

| Call | What |
| --- | --- |
| `GET /v1/calendar/status` | Connected accounts and their calendars; what is shown or sent. |
| `POST /v1/calendar/oauth/start` | `{workspace, email?}` → `{url}` to send the browser to. |
| `PUT /v1/calendar/sources/{id}` | `{enabled?, export?, color?}`. |
| `DELETE /v1/calendar/accounts/{id}` | Disconnect. |
| `GET /v1/calendar/events?from=&to=&workspace=` | Events on the person's clock: `date`, `endDate`, `startMinute`, `endMinute`, `allDay`, colour, calendar, account, workspace. |
| `POST /v1/calendar/sync` | `{timezone?, accountId?}`: read now, and send meetings and hearings. |
