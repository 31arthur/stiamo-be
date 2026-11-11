# An assistant for each workspace

Each workspace (S&B, FSA, Personal, Media) has its own assistant, and each assistant has its own Telegram bot.

```
                     ┌─ S&B bot       ─► S&B assistant       ─► only the agents placed in S&B, only S&B's data
 your Telegram chat ─┼─ FSA bot       ─► FSA assistant       ─► only the agents placed in FSA, only FSA's data
                     ├─ Personal bot  ─► Personal assistant  ─► ...
                     └─ general bot   ─► the Assistant       ─► every agent, every workspace
```

- **It answers about its workspace only.** Ask the FSA bot "what's pending?" and it reads FSA's tasks, follow-ups, meetings, plans and calendar, and FSA's mailboxes: nothing from S&B or Personal. Cases, hearings and counsel are the S&B practice's, so only the S&B assistant sees them; clients and projects are FSA's.
- **What it adds belongs to its workspace.** A task or follow-up it proposes (and you approve with YES) is saved in that workspace, whatever the model suggested.
- **It chooses among that workspace's agents.** The agents it can hand a question to are the ones placed in the workspace (Developer → the agent → workspaces). Place the daily planner in S&B and FSA, and the Personal assistant won't use it.
- **Its own thread.** Each bot keeps its own conversation (it also shows in the web Assistant, under that workspace).
- **Email alerts arrive on the same bot.** The priority mail watch of a workspace sends its alerts to that workspace's bot, so S&B's mail doesn't land in your personal chat.
- The **general bot** (Telegram under *On your phone*) stays as the assistant for everything at once, and it is where the evening plan, call reminders and anything that isn't about one workspace go.

## Setting it up

1. In Telegram, make one bot per workspace with @BotFather (`/newbot`), for example "S&B Assistant", "FSA Assistant", "Personal Assistant". Keep each token.
2. In Stiamo: Assistant → **On your phone** → **Workspace assistants**. Open a workspace, paste its token, then press **Start** in that bot (or send it the code).
3. Say hello to each bot: it answers "I'm the FSA assistant…" and from then on talks only about FSA.
4. Place your agents: Developer → the agent → which workspaces it is shown in. An assistant only uses agents placed in its workspace.

One bot per workspace. To change it, remove the bot (Disconnect) and connect another.

## What isn't per workspace

The evening plan and call reminders go to the general bot only. The Drive and news feeds are not split by workspace.
