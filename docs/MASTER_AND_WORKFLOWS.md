# One master bot, and agents as workflows

## The master bot

Connect **one** Telegram bot (Assistant → On your phone → Telegram · master assistant). It is the master assistant: for each message it works out which **workspace** it is about, picks the **agent** (the Decision model if it is on, otherwise rules) and answers, signed with the agent and the workspace ("— Daily planner · S&B"). The daily plan, mail alerts and call reminders come to the same bot.

- **Which workspace:** from the words ("what hearings does S&B have", "send the FSA invoice", "renew my passport"). Say "all workspaces" to look across everything. A message with no workspace carries on in the one you were just working in (a few hours). Otherwise it asks, with buttons (⚖️ S&B · 💻 FSA · 🏠 Personal · 🌐 All of them), and answers when you tap.
- **Kept apart:** each workspace has its own conversation under the same chat, and every agent it starts is limited to that one workspace. Nothing from another workspace reaches the model, so context never mixes. `/new` starts all of them afresh.
- **Per-workspace bots still work** (and stay leak-proof), but you don't need them any more; they are now labelled optional.

## Each agent as a workflow

Open any agent → **Workflow**. It is shown as nodes, the way a workflow editor would:

**Starts when → Workspace scope → Prompt → Model → Answer**, with the **tools** the model can call hanging under it.

The graph is the agent's real configuration, so editing a node changes how it runs, and applies the next time it runs:

| Node | Edit |
| --- | --- |
| Starts when | (read-only) how it can be started |
| Workspace scope | which workspaces it is shown in |
| Prompt | the instructions and how a message is wrapped (the full prompt editor) |
| Model | provider, model, how hard it thinks |
| Tool | switch it on or off, change what the model is told it does |
| Answer | (read-only) what is delivered |

**Executions:** the side list shows every run. Pick one and each node shows what it did in that run: what started it, the workspace it was limited to, what the model was given, its time and tokens, each tool call with what it asked and what came back, and the answer or the error. A tool the model never called shows as not used.

The master assistant is drawn at the top of Agents (All workspaces) as its own flow, with each agent underneath linking to its workflow.

**Not built yet:** adding new kinds of node, branches and loops, or wiring nodes in a different order. The node types are fixed, and each edits a real setting; free-form graphs would need a new engine under every agent. Task Force agents keep their existing builder.

## Deploying

`./deploy.sh` runs migration `agent 0019` (a run records the workspace it ran under).

## Deleting the old agents' traces (run once, by hand)

The old run history (every prompt, input, tool call and answer) and the saved chat threads are not needed by the new approach. They are **not** deleted automatically on startup, because that would be permanent and would repeat on every restart. Run this once on the VPS after the new image is up:

```bash
# shows how many rows it would delete, and deletes nothing
docker compose -f deploy/docker-compose.prod.yml exec agent python -m app.scripts.purge_traces

# deletes them
docker compose -f deploy/docker-compose.prod.yml exec agent python -m app.scripts.purge_traces --yes
```

It keeps what is in use: each agent's model, prompt and workspace settings, connected bots, API keys, the cost and usage ledger (the Usage page), proposals and plans waiting for your approval, the delivery log, and the Task Force agents with their runs. Add `--include-usage` to clear the cost ledger too. There is no undo, so take a database backup first if in doubt (`pg_dump` of `stiamo_agent`).

## Workflows the n8n way (the flow engine)

Agents can also run as workflows in the shape n8n uses: **nodes on a canvas, joined output to input, passing lists of items.** (Built from n8n's ideas, not its code: n8n's Sustainable Use License doesn't allow copying its code into a product, so the engine, nodes and expressions here are written for this app.)

- **Items:** every node takes the items that arrive and gives items on each of its outputs. An **If** has two outputs (true, false), a **Switch** one per rule plus a fallback. A node that gets nothing doesn't run, which is how a branch ends (set `runWhenEmpty` on a node to answer "found nobody").
- **Expressions:** any text can hold `{{ … }}`: `{{ $json.subject }}`, `{{ $input.length }}`, `{{ $node["Mail"].first.sender }}`, `{{ join($json.title, ", ") }}`, `{{ contains($json.subject, "invoice") && $json.n > 2 }}`. A whole `{{ }}` keeps its type (a list stays a list). Nothing is run as code.
- **Nodes:** triggers (chat message, schedule, by hand); If, Switch, Merge, Loop over items, Filter, Sort, Limit, Split out, Aggregate, Edit fields, Do nothing, Stop and fail, Sticky note; **this app's data**: read mail, read a message, find contacts, tasks, follow-ups, meetings, hearings, cases, calendar events, Drive; **Send a message** to your phone; **Telegram** (sends with one of your bots, chosen as the node's credential: to all of its linked chats or one; a bot limited to a workspace is only used by that workspace's workflows); **AI agent** (with the tools you give it); **Answer**.
- **Workspace limits still hold:** a workflow's data nodes read through the same workspace-limited data the agent's tools use, so a run about S&B can't read FSA's tasks, mail or contacts.
- **Trace:** every node run is kept with how long it took and the items that went in and came out, so an execution can be opened node by node.

### Create an agent from a workflow
`GET /v1/agent/flow/catalog` lists the nodes and the templates (Chat assistant, Daily calls digest, Mail digest, Contact lookup, Overdue tasks nudge). `POST /v1/agent/agents {name, templateId}` creates an agent from one (or send your own `flow`); it appears in the agent list with its own model, prompt, workflow and history. `DELETE /v1/agent/agents/{id}` removes one you created. `GET|PUT|DELETE /v1/agent/agents/{id}/flow` read, save (only a workflow that can run is kept) and reset a workflow; `POST …/flow/check` says what's wrong; `POST …/flow/run` runs it by hand.

### State of it
- **Every agent runs on the flow engine** (the default, `AGENT_ENGINE=flow`). Each starts as "message in → AI agent → answer" and shows as that on the canvas. `AGENT_ENGINE=classic` is a fallback to the original single loop (no node trace); the whole suite (367 tests) passes on both.
- **Canvas editor** (Agents → an agent → Workflow): drag nodes, join an output to an input, add nodes from the palette, set each node in the form below, Save (only a workflow that can run is kept), Run it with a message, and pick any execution to see the items that went into and out of each node. Agents → **From a workflow** creates an agent from a template.
- **Schedule triggers run by themselves**: a workflow with an "On a schedule" node runs at its time every day (set the time and time zone on the node). They start when you save, move or stop when you change the workflow, and come back after a restart. Each run is in the executions list with trigger "schedule".
- **Ask me node:** asks you a question on your phone with two to four buttons and waits. The workflow stops there (the question is stored, so it survives a restart and is good for two days); the button you press carries on from that answer's output, with `{{ $json.answer }}` holding what you chose. A second tap says it is out of date.
- **Weekdays:** a schedule node has weekday boxes (Monday to Sunday), by the clock of its time zone. With none ticked it never runs.
- **Pin data and run one node:** pin items on a node and a run **by hand** uses them instead of running the node (a scheduled or chat run never does), so you can try the rest of a workflow with fixed data. "Run this node" runs one node alone, with items you give or the data pinned on the nodes leading into it, and shows what came out, without making a run. A node that sends or drafts something outside the workflow only runs alone when you confirm.
- **Draft an email (you approve):** the node writes an email and keeps it as a proposal. Nothing is sent: approving it (in Needs me, or YES on the phone) sends it from this workspace's mailbox, once. A recipient that isn't an address, no text, or no mailbox fails with the reason.
- **Not built yet:** the planner's "free for calls?" question and the follow-up chaser are still their own code, not Ask me nodes (rebuilding them changes what the model is given, so it needs its own testing).

`./deploy.sh` runs migrations `agent 0021` (each agent's flow, and the agents you create) and `agent 0022` (questions waiting for an answer). The earlier linear graph form and its `/graph` endpoints are removed.
