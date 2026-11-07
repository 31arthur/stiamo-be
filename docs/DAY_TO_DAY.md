# Working day to day

The app is organised around one question: **what needs me right now?**

## Where things are

| Where | What it is |
| --- | --- |
| **Assistant** (bold, top of the sidebar; opens in its own tab) | Ask, plan, add. One assistant for everything, and one per workspace on its own Telegram bot. |
| **Needs me** (where you land in a workspace) | Everything waiting on you in one list, most pressing first: urgent mail, overdue work, what agents proposed, the evening plan waiting for a yes, today's tasks and calls, meetings and hearings to write up. Act on each beside it: call, done, move to tomorrow, approve, open the mail. Click a mail's title to review it (the email, an event you can edit before adding it to the calendar, every decision), and Needs me moves on to the next. The footer links to the full Tasks, Follow-ups, Meetings and flagged-mail lists. The badge on the sidebar is the count. |
| **Today** | The day on a timeline. |
| **Insights** | Mail, news and work read together, with a written digest. |
| **Calendar, Mail, Contacts** | Communicate. Contacts come from Google, per workspace; a task or follow-up picked from them points at the contact, and the contact shows when you last spoke. |
| **Setup and health** (Tools) | Every connection (AI, Telegram, calls, mailboxes, mail watches, Google Calendar and Contacts) with ok / warn / error, what to do about it, a Test for each, and a log of what reached your phone. The badge is the number of problems. |
| **Developer** (Tools; opens in its own tab) | Building and tuning agents: models, prompts, themes, environment keys. |

## On your phone

- **Alerts and reminders carry buttons.** A call reminder has a button for each pending call (Done), and "In 30 min" or "Tomorrow" for all of them. Done marks the call done with a try that reached them. An urgent mail alert has Open the email, suggested replies, Write my own and Handled.
- **Voice notes.** Send a voice note to any paired chat: it is transcribed with your Deepgram key (Developer → Environment), shown back as heard, and answered like a typed message, so "add a task to call Hemla tomorrow" becomes a proposal you approve with YES.
- **A call for what is urgent**, not for everything: priority mail only, once per workspace per 15 minutes, and the Telegram notification is otherwise enough.

## Knowing it works

Open **Setup and health** when something doesn't arrive. Red is what will not work (no model, no linked chat, a mailbox that can't sign in, a Google account that must be connected again); amber is what works but is missing something (no app link in alerts, a call that has never gone through). Every send to your phone is kept for a month with where it went and, if it failed, why.

## Deploying this version

`./deploy.sh` runs migrations `agent 0016` (delivery log) and `practice 0006` (contact links). No new environment variables.
