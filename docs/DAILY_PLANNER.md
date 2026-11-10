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

## What happens in the evening

| Step | What |
| --- | --- |
| Plan | For each ticked workspace the planner reads that workspace's open tasks and calls and the whole day's fixed commitments. A workspace with nothing open is skipped. |
| Send | The plan arrives on that workspace's bot: the day laid out, the proposed times, **Approve** / **Not this**. The same plan waits under **Needs me** in the app. |
| Approve | The tasks and calls get their times, and Stiamo asks Google to sync: the times appear in that workspace's Google calendar. |

Plans are never booked by themselves. If something changed since the plan was made (a new meeting at the same time), approving says so and asks for a new plan.

## Deploying this version

`./deploy.sh` runs migration `agent 0017` (a plan belongs to a workspace). Nothing else is new to set up: the Google credentials you already added are used.
