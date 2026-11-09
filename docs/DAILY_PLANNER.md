# A daily planner for each workspace

Each evening the planner plans **tomorrow for each workspace on its own**: Personal, S&B and FSA each get a plan made from that workspace's own tasks and calls, fitted around everything else on the day (the other workspaces' hearings, meetings and timed work, and every Google calendar event), so nothing is planned over anything else. Each plan goes to **that workspace's Telegram bot** with **Approve** and **Not this** buttons. Approving books the times and puts them in that workspace's Google calendar straight away.

## Set it up

1. **A bot per workspace.** Assistant → Workspace assistants → connect a bot for each (the pairing is the same as for the mail alerts). A workspace without a bot of its own sends its plan to your general bot instead.
2. **A Google account per workspace.** Calendar → Calendars → connect. One account can serve several workspaces, and one workspace can have several accounts. The first account connected for a workspace automatically receives that workspace's schedule in its own calendar (meetings, hearings, tasks and calls). To use another calendar, press **Send my schedule here** on it. Accounts connected before this version need that once.
3. **Switch the plan on.** Developer → Daily planner → Evening plan: choose the time, tick the workspaces that plan their own day, Save. **Plan now** beside a workspace makes its plan straight away.

Setup and health shows each of these: the bots, the Google accounts, and whether each workspace has a calendar its schedule goes to.

## What happens in the evening

| Step | What |
| --- | --- |
| Plan | For each ticked workspace the planner reads that workspace's open tasks and calls and the whole day's fixed commitments. A workspace with nothing open is skipped. |
| Send | The plan arrives on that workspace's bot: the day laid out, the proposed times, **Approve** / **Not this**. The same plan waits under **Needs me** in the app. |
| Approve | The tasks and calls get their times, and Stiamo asks Google to sync: the times appear in that workspace's Google calendar. |

Plans are never booked by themselves. If something changed since the plan was made (a new meeting at the same time), approving says so and asks for a new plan.

## Deploying this version

`./deploy.sh` runs migration `agent 0017` (a plan belongs to a workspace). Nothing else is new to set up: the Google credentials you already added are used.
