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
