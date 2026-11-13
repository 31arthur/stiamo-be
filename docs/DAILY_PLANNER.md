# A daily planner for each workspace

Each evening the planner plans **tomorrow for each workspace on its own**: Personal, S&B and FSA each get a plan made from that workspace's own tasks and calls, fitted around everything else on the day (the other workspaces' hearings, meetings and timed work, and every Google calendar event), so nothing is planned over anything else. Each plan goes to **that workspace's Telegram bot** with **Approve** and **Not this** buttons. Approving books the times and puts them in that workspace's Google calendar straight away.

## Set it up

1. **A bot per workspace.** Assistant → Workspace assistants → connect a bot for each (the pairing is the same as for the mail alerts). A workspace without a bot of its own sends its plan to your general bot instead.
2. **A Google account per workspace.** Calendar → Calendars → connect. One account can serve several workspaces, and one workspace can have several accounts. The first account connected for a workspace automatically receives that workspace's schedule in its own calendar (meetings, hearings, tasks and calls). To use another calendar, press **Send my schedule here** on it. Accounts connected before this version need that once.
3. **Switch the plan on.** Developer → Daily planner → Evening plan: choose the time, tick the workspaces that plan their own day, Save. **Plan now** beside a workspace makes its plan straight away.

Setup and health shows each of these: the bots, the Google accounts, and whether each workspace has a calendar its schedule goes to.

## Talk to it

Each workspace's bot is that workspace's assistant, so you can also plan with it any time. Examples, to the S&B bot:

- "Plan tomorrow": it reads S&B's open work and the whole day, and proposes times. Reply **YES** to book them (they go to Google too) or **NO**.
- "Put the rejoinder on my schedule tomorrow at 11 for 90 minutes", "Call Hemla tomorrow at 4": it shows a task or call with that time. **YES** saves it, and it appears in the app and in Google Calendar.
- "What's on today?", "What is overdue?", "Add a task to file the reply by Friday."

It only sees and saves that workspace's things, but it plans around the other workspaces' commitments so nothing lands on top of them. A voice note works too if the Deepgram key is set.

### Which bot gets the plan

A workspace's plan goes to the first of these it has: its bot set to **The daily plan**; else its bot set to **Everything**; else your master bot (all workspaces). A bot set to **Mail alerts** never gets a plan. So with a mail bot and a master bot, set the workspace's mail bot to **Mail alerts** (Assistant → On your phone → the workspace → *For*): its plan then comes to the master bot. A workspace bot left on *Everything* takes the plan, the alerts and the chat all together.

### How a plan reads on the phone

Short, so it can be read at a glance: a heading, a one-line summary, then a line for each thing with a small icon, and only the one fact that matters under it (the number to ring, where a hearing is). Each thing appears once (a hearing that is both fixed and needs preparing is one line), a few things per section with the rest counted, only the two longest free windows, and no signature on the planner's own bot. Confirmations ("Saved", "Plan approved") are a few lines, and the same thing proposed several times is one line with a count.

### Reading every kind of message

`services/agent/tests/test_message_gallery.py` drives each kind of message (pairing, the plan and its buttons, the time question, proposals and their YES and NO, reminders, mail alerts and their drafts, the story editor and the interviewer, the assistant's own answers) through a fake Telegram and keeps what the phone would receive. Each kind has a length budget, so a message that grows long again fails the test. To read them all in one file:

```bash
cd services/agent
STIAMO_GALLERY=gallery.txt pytest tests/test_message_gallery.py
```

### Attach the bots you already made

Telegram bot tokens are secrets, so they are entered in the app, not here: Assistant → Workspace assistants → pick the workspace → paste that bot's token from @BotFather → choose what it is for → Connect. Then open the bot in Telegram and press Start (or send the pairing code shown). Do it once per bot. A workspace can have several: one for everything, one for mail alerts, one for the daily plan (each purpose once per workspace).

**A bot you connected earlier without choosing a workspace** (for instance from the Daily planner's own Channels tab) is the *general* bot, and the general bot sees every workspace. Open it, and under "Limit to" choose the workspace and what it is for. It takes effect at once, and the chat starts afresh so nothing it said before carries over. Setup and health lists every bot with what it can see.

## Keeping each workspace's data apart

The rule is: **a bot has one scope, fixed when it is connected, and the server applies it to the data on every run**, whichever agent answers and whatever is typed. It is not a request to the model to behave.

| Where | What is enforced |
| --- | --- |
| What a bot's model is sent | Only that workspace's tasks, calls, meetings, calendar events, mail and (for S&B) cases, hearings and documents. A record with no workspace label is visible to **no** workspace. |
| The rest of the day | Another workspace's commitments reach the planner as bare **busy time** (when, never what): no title, place, person or workspace. That is how an FSA task is kept off an S&B hearing without S&B ever being told what the FSA item is. |
| A bot pinned to one agent | Held to its workspace the same way. (This was the leak: such a bot used to answer unscoped.) |
| What a bot is sent | Alerts and plans go to the workspace's own bot for that purpose, else its everything-bot, else the general bot; never another workspace's bot. |
| Changing a bot's scope | Its remembered chat and thread are dropped, so nothing said under the old scope carries over. |
| Saving | A workspace's assistant can't save a record that belongs to another workspace. |

So with a planner bot for S&B and one for FSA, each plans around the other's calendar (hearings, meetings, calls, timed tasks and every Google calendar, including what the other has already approved) yet shows and proposes only its own tasks. All-day events don't take time, so they aren't counted.

`tests/test_leaks.py` guards this: every workspace's data carries a unique canary, each bot is asked to plan the day, and everything sent to the model and everything said on the phone is searched for the other workspaces' canaries. The general bot is the control and must find all three.

## Events you can work through

A hospital visit from 9 to 12 is an event the planner won't schedule over, but you may be able to take calls during it. So the planner asks. In the evening, for each long event (an hour or more) tomorrow that it has no answer for, the workspace's plan bot sends one short message:

> 🏥 **Visit hospital**, tomorrow 09:00–12:00
> Free for calls then? 📞

with three buttons: **📞 Calls are fine**, **💻 Free for anything**, **🙅 Busy, leave it**. Then:

- **Calls are fine:** calls and follow-ups are planned inside the window (the ones promised for that day first), but no heavy tasks. Call reminders also come through during it.
- **Free for anything:** the time counts as free for everything.
- **Busy:** as before.

When the last question for a day is answered, the plan for that day is made again with your answer (if it isn't approved yet) and sent to you, with a full message saying what was understood. The answer is remembered by event title, so a recurring "Visit hospital" is asked once; change or forget it under Developer → Daily planner → Remembered answers. Hearings, courts, all-day events and short ones are never asked about, at most three questions are sent per workspace per day, and the same answer carries over to your other workspaces' plans as bare busy time (never what the event is).

## Calling from Telegram

Wherever a message is about a call, the person's number is in it and there is a **📞 Call …** button under it. Tap it and the phone's dialer opens with the number filled in, on an iPhone or on Android.

- **Call reminders:** each call has ✅ and 📞 beside it.
- **The evening plan:** the calls in it get a 📞 button each (up to four), and the number is printed beside the call.
- **After you approve a plan:** the confirmation lists each call with its number and keeps the 📞 buttons under it.

How it works: Telegram doesn't allow a `tel:` link on a button, so the button opens `https://<your app>/call.html`, a small page that hands the number to the dialer. The number is in the address' `#fragment`, which is never sent to any server. If the dialer doesn't open on its own, the page has a big **Call now** button. The number is also plain text in the message, which Telegram itself lets you tap. A call needs a number in its contact (pick it from Contacts, or type "Name · +91 …"), and `AGENT_APP_URL` must be the https address of the app.

## When you press a button

Every button answers at once: a short notice on screen, and a "⏳ …" message in the chat saying what is being done. That message then becomes the result: for an approved plan, the list of what is on your planner, what reached Google Calendar, what was left out and what to do next. If something fails you get the reason and "nothing was changed", and a button from an older message says so instead of doing nothing.

## What happens in the evening

| Step | What |
| --- | --- |
| Plan | For each ticked workspace the planner reads that workspace's open tasks and calls and the whole day's fixed commitments. A workspace with nothing open is skipped. |
| Send | The plan arrives on that workspace's bot: the day laid out, the proposed times, **Approve** / **Not this**. The same plan waits under **Needs me** in the app. |
| Approve | The tasks and calls get their times, and Stiamo asks Google to sync: the times appear in that workspace's Google calendar. |

Plans are never booked by themselves. If something changed since the plan was made (a new meeting at the same time), approving says so and asks for a new plan.

## Deploying this version

`./deploy.sh` runs migrations `agent 0017` (a plan belongs to a workspace) and `agent 0018` (answers about time inside events). Nothing else is new to set up: the Google credentials you already added are used.
