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
