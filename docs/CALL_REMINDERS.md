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
- On Telegram, a **voice message** too, if you have a Deepgram key. It arrives as a spoken message with a notification.
- A **Telegram call**, if you switch it on. A bot can't start a call (Telegram gives bots no way to), so Stiamo uses [CallMeBot](https://www.callmebot.com/?p=5310), a third-party service that rings your own Telegram account and reads the reminder aloud when you answer. It is not part of Telegram.

## Setting up the voice

The voice is [Deepgram Aura](https://deepgram.com/). A new account gets a free credit, and a reminder is a few hundred characters.

1. Create a Deepgram account and an API key.
2. In Stiamo, Developer → **Environment**: paste it as `DEEPGRAM_API_KEY`. It is sealed with the server's key the moment it arrives and is never shown again: you can replace it, remove it, and press **Test connection**.
3. Assistant → **On your phone** → **Call reminders**: switch the reminders on and press **Send a test**.

Without a key, or with the voice switched off, the reminders are written only.

## Setting up the Telegram call

1. Open Telegram and send `/start` to **@CallMeBot_txtbot** (this lets CallMeBot call you).
2. In Telegram, Settings → Privacy and Security → Calls: allow calls from everyone, or add CallMeBot as an exception. A call Telegram blocks fails with no sound.
3. In Stiamo, Assistant → **On your phone** → **Call reminders**: switch on *Ring me on Telegram*, give your `@username` (or your number with country code, like `+919876543210`), and press **Send a test**.

Good to know:

- It reads at most **256 characters**, so a call names as many pending calls as fit ("Stiamo. 3 calls pending: …. Please mark them done.") and says "and N more" for the rest. The written message has the whole list.
- The voice is a Google *Standard* voice (default `en-GB-Standard-B`; `en-IN-Standard-A` to `D` are the Indian English ones to try).
- The message is spoken twice per call.
- CallMeBot documents no response format and no rate limit, and its pages don't say the shared bot is free for good. If it stops working, the written message and the Deepgram voice message still arrive. A dedicated bot is $15 a month on their site.
- A known Telegram bug on some iOS versions connects the call with no sound.

## Deploying

The agent service has a migration (`0013`, one column on `agent_settings`). `./deploy.sh` runs it when the container starts. No new environment variables.
