# The planning team

Four agents written in code (not built in the agent builder) that plan the day. They appear under **Agents → Team** in every
workspace (Hearing prep only in S&B), you can talk to them in the app or on Telegram/WhatsApp, and each only **reads**: what they
suggest is a card you approve.

| Agent | id | What it does |
| --- | --- | --- |
| Daily planner | `daily-planner` | Plans a day (tomorrow by default): the fixed things, what to prepare, who to call, the work that fits, as a briefing and a proposed schedule. |
| Hearing prep | `hearing-prep` | For a hearing: where the case stands, the last order, documents to carry, open questions. |
| Follow-up chaser | `follow-up-chaser` | Who to call or message next, in what order and when, from past tries and what people promised. |
| Deadlines & inbox scout | `deadline-scout` | What is due in the next days, and what the mail agents have put in Attention. |

They draw on two tool packs (`app/application/toolpacks/`):

- **agenda**: `day_agenda`, `free_time`, `upcoming`, `propose_schedule`. Working-day logic is in `app/domain/dayplan.py` (9:00–21:00,
  hearings and meetings are fixed, blocks may not overlap them or each other).
- **attention**: `waiting_in_attention`, the Attention queue as the mail agents left it.

## The evening plan

Most days end at 21:00, so the planner is run by itself at that time (the person can change the time and zone). It plans **tomorrow**
in the person's time zone, keeps the result in `day_plans` (one per person per day), and sends the briefing to the connected Telegram
chats and WhatsApp numbers. In the app it waits in **Attention** as a plan to approve or dismiss.

**Approving** books the blocks: a `slot` on each task, a time (and length) on each follow-up. It checks the day again first, and
refuses if a hearing or meeting has appeared since. A plan that was approved is not replaced unless asked to.

Nothing is written without the person's approval.

### Setting it up

1. Deploy the `agent` service and run migrations (`alembic upgrade head`, revision `0008`).
2. Choose a provider and model for the Daily planner (Agents → Team → Daily planner → Settings). The evening run uses it.
3. Switch the evening plan on (Agents → Team → Daily planner), pick the time.

Runs once per day per person from the service's own clock, held in memory like the other schedules: it starts again with the service,
and runs with a single replica.

### API

| Call | What |
| --- | --- |
| `GET /v1/agent/day-plans` | Settings, the schedule's state, and the plans for the latest days. |
| `PUT /v1/agent/day-plans/settings` | `{enabled, time: "21:00", timezone: "Asia/Kolkata", channels}`. |
| `POST /v1/agent/day-plans/generate` | `{day?, force?}`: plan a day now. |
| `POST /v1/agent/day-plans/{id}/apply` | Approve: book the blocks. 409 if the day has changed. |
| `POST /v1/agent/day-plans/{id}/dismiss` | Not this plan. |

## Asking the team to add or change things

Every agent in the team also has the **changes** pack (`app/application/toolpacks/changes.py`): `add_task`, `change_task`,
`add_follow_up`, `change_follow_up`, `add_hearing`, `add_case`, `add_action_plan`. Ask in plain words ("add a task to draft the
rejoinder for Alur, due Thursday", "move the call with Hemla to 3 pm", "list a hearing for SA-2026-001 on 12 Jan", "make an action
plan for the rejoinder with these steps").

**They only propose.** Each tool checks what it was given (dates, times, the case, the organization) and says what is wrong if
something is, so the agent can ask you or fix it. A good one is kept as a *proposal* and shown as a card with **Approve** and **Not
this**. On Telegram or WhatsApp the reply lists what was proposed; **YES** saves it, **NO** drops it (only what was proposed in that
chat in the last 30 minutes; with nothing waiting, "yes" is an ordinary message).

Approving is the only thing that writes (`POST /v1/agent/proposals/{id}/apply`, `/drop`). It saves as the owner, record by record
and by id, so retrying never doubles anything. A change to a task or follow-up is merged into it as it is at that moment. A proposal
older than a day is refused as stale. A new case gets its number when it is opened, as always.

Migration `0009` adds `agent_proposals`.

## Google calendar events

Events in the Google calendars connected through the calendar service ([CALENDAR_SETUP.md](CALENDAR_SETUP.md)) count as busy time: `day_agenda`
lists them (and the all-day ones), the free windows leave them clear, `propose_schedule` refuses a block placed over one, and approving a
plan checks again. They are read through `AGENT_CALENDAR_URL` (default `http://calendar:8000`); with no calendar service, or no account
connected, nothing changes. An event that runs over midnight counts on the day it starts.

## Conversations

The app's Assistant page keeps each chat as a **conversation** on the server (`agent_conversations`, migration `0010`), so a thread
continues on any device. A conversation belongs to one agent and one workspace (or "all"); what it said is kept for showing, and the
last 40 messages are kept for the model. Proposal cards in a thread show their current state when it is reopened.

| Call | What |
| --- | --- |
| `GET /v1/agent/conversations?workspace=` | Threads, newest first; a workspace's own, or every one for `all`. |
| `POST /v1/agent/conversations` | `{agentId, workspace}`: start one. |
| `GET /v1/agent/conversations/{id}` | One thread in full. |
| `POST /v1/agent/conversations/{id}/messages` | `{message}`: ask; returns the turn and the thread's summary. |
| `PATCH /v1/agent/conversations/{id}` | `{title}`: rename. |
| `DELETE /v1/agent/conversations/{id}` | Delete. |

## The assistant (picks the agent)

`assistant` is not an agent with tools of its own: it **routes**. For each message it chooses one of the team's agents (those shown in the
workspace), tells that agent what the conversation has established, and returns that agent's answer, signed with its name. The person
talks to one thing; whichever agent answers.

- **Choosing.** A short follow-up ("and the labour office?", "yes", "move it") stays with the agent that just answered, with no model call.
  Otherwise the decision model (**Jev**, when it is switched on under Providers) is asked one typed `choice` question whose options are the
  agents and what each is for. With Jev off or unreachable, plain rules choose by the words each agent is known for. The turn records how
  (`routedBy`: `jev`, `rules`, `continuity`, `only`) and why. Jev is a decision model: it chooses, it does not search the web.
- **Consistency.** Every agent is given the same account of the conversation ahead of the message: the case and day it has been about
  (the *focus*, kept current from the case references named and the cases and days the answers showed), a line for each older exchange,
  and the last six exchanges in full, including what cards said (a briefing's items, a proposal's title). The raw tool messages of one
  agent are never replayed to another, so any agent can pick up where another left off. This is stored with the conversation
  (`agent_conversations.context`, migration `0012`).
- **Phones.** A Telegram or WhatsApp channel set to the assistant (the default for new channels) keeps one thread per chat, which also
  shows in the web app. A chat quiet for 12 hours, or `/new`, starts a fresh one. YES and NO still save or drop what an agent proposed.
