# Call reminders

When a call on your planner isn't marked done, Stiamo reminds you on Telegram, and can say it aloud as a voice message.

## When it reminds

- Calls (follow-ups with a time) that are lined up one after the other, with no more than 10 minutes between them, are **one run**. Nothing is said until the whole run has ended.
- The first reminder comes **10 minutes after the run ends**. If something is still open: **1 hour** later, then **30 minutes**, then **10 minutes**. After that the run is left alone.
- It reminds only about what is still open: mark a call done and it drops out; mark them all and it stops.
- It never comes while you are busy (a hearing, a meeting, a Google Calendar event, a task with a time). It waits for the first free minute.
- Only between 09:00 and 21:00 on the day of the calls, by the clock of the planner's time zone (Developer → the daily planner; Asia/Kolkata unless changed).

The ladder is `LADDER` in `services/agent/app/domain/nudge.py`.

## What is sent

- A written message to every connected Telegram chat and WhatsApp number, listing the calls.
- On Telegram, a **voice message** too. A bot can't ring your phone (Telegram gives bots no way to start a call), so the reminder arrives as a spoken message that shows as a new message with a notification.

## Setting up the voice

The voice is [Deepgram Aura](https://deepgram.com/). A new account gets a free credit, and a reminder is a few hundred characters.

1. Create a Deepgram account and an API key.
2. In Stiamo, Assistant → **On your phone** → **Call reminders**: paste the key, switch the reminders on, and press **Send a test**.

Without a key, or with the voice switched off, the reminders are written only.

## Deploying

The agent service has a migration (`0013`, one column on `agent_settings`). `./deploy.sh` runs it when the container starts. No new environment variables.
