# AI agents: how they are built here

The `agent` service runs every AI agent in Stiamo. It follows the layers in [ARCHITECTURE.md](ARCHITECTURE.md) (domain →
application → infrastructure/presentation, each service its own database, tokens verified locally); this page covers only
what is specific to agents. To **add** one, go straight to [ADDING_AN_AGENT.md](ADDING_AN_AGENT.md). To **run** the
service (keys, Telegram, WhatsApp) see [AGENT_SETUP.md](AGENT_SETUP.md); every endpoint is in [API.md](API.md).

## The idea in one picture

```
              ┌──────────── written once, in code ────────────┐        ┌── stored per person ──┐
              │                                               │        │                        │
 AgentDefinition (id, name, default prompt, packs)            │        │ provider keys (sealed) │
        │ names                                               │        │ model + thinking, per  │
        ▼                                                     │        │   agent                │
   ToolPack ──► tools + prompt variables                      │        │ prompt, per agent      │
   (cases, drive, decisions, …)                               │        │ decision model on/off  │
                                                              │        │ channels (sealed)      │
              └───────────────────────────────────────────────┘        └────────────────────────┘
                                        │                                         │
                                        └───────────────► one question ◄──────────┘
                                                              │
        who asks (token) ─► registry.get(agent) ─► build tools from its packs, bound to THIS person's data
        ─► fill the prompt ─► pick the person's model ─► run_agent loop ─► answer + cards of what was found
```

**Code defines what an agent can do; data records what a person chose.** Nothing a person edits is ever written to code,
and nothing in the database describes a tool.

## Vocabulary

| Term | What it is | Where |
| --- | --- | --- |
| **Agent** | A name, a default prompt and the tool packs it draws on. A frozen dataclass; never stored. | `app/domain/agents.py`, definitions in `app/agents/` |
| **Tool pack** | A small group of tools about one thing, plus the prompt variables that go with them. **The unit of reuse.** | `app/application/toolpacks/` |
| **Tool** | One function the model may call: a JSON-schema spec, a one-line trace label, an async `run`. | `app/application/runner.py` |
| **Provider** | OpenAI, Gemini, OpenRouter, behind the `LlmClient` port. | `app/infrastructure/llm/` |
| **Decision client** | Jev (typed questions → probabilities), behind the `DecisionClient` port. Reused as the `decide` tool. | `app/infrastructure/llm/jev.py` |
| **Data gateway** | The practice's cases, hearings, attachments and Drive, read with the caller's own token. | `app/infrastructure/data.py` |
| **Prompt config** | What a person may change about one agent: system prompt, input wrapper, per-tool overrides. | `app/domain/prompt.py` |
| **Package** | Everything sent to the model for one question (system, user message, tools). Shown to people as a preview. | `app/domain/prompt.py` |
| **Run** | One question answered: the loop, the steps taken, what was found, the conversation so far. | `app/application/runner.py` |
| **Channel** | Telegram or WhatsApp, connected by the person, answered by one agent. | `app/application/channels.py` |

## Where things live

```
services/agent/app/
  domain/        agents.py (AgentDefinition)  llm.py (Message, ModelInfo, thinking)  prompt.py (templates, Package)
  application/   runner.py (the loop)  registry.py (agents + packs, validated)  services.py (AgentService)
                 channels.py (ChannelService)  ports.py  toolpacks/{__init__,common,cases,drive,decisions}.py
  agents/        one module per agent + the list that is registered     ◄── the only place a new agent touches
  infrastructure/ llm/{openai_compat,gemini,jev}.py  data.py  messaging.py (Telegram, Twilio)  security.py  db/
  presentation/  deps.py (composition root)  routers/{agent,channels,webhooks}.py
```

Dependency rule (same as everywhere): `domain` imports nothing of ours; `application` imports `domain` and ports;
`infrastructure` implements ports; `agents/` is configuration and may import `domain` and `application`;
`presentation/deps.py` wires everything. A tool pack never imports httpx, SQLAlchemy or FastAPI: it talks to a port.

## One question, step by step

`AgentService.chat(who, token, agent_id, message, history, channel)`:

1. `registry.get(agent_id)` → the definition (404 if unknown).
2. `_prepare`: for each pack the agent lists, `pack.tools(ctx)` and `pack.context(ctx)`. `ctx` carries a `Memo` around the
   data gateway (so three packs asking for cases read them once), today's date, and the decision client if the person
   switched it on. Tool names must be unique across packs.
3. The person's prompt for **this agent** (or the agent's default) is validated against the variables this agent really has
   (`BASE_VARIABLES` + its packs'), the person's tool overrides are applied, and `{{variables}}` are filled in → a `Package`.
4. The person's model for this agent (or the last one they chose anywhere) is resolved to a client.
5. `run_agent` loops: ask → run the tools asked for → hand results back → … until the model answers in words or the step
   limit is hit (then it is asked once more with no tools, so it must answer).
6. The reply is `{answer, steps, found, messages}`. `found` comes from what the **tools** returned, never from the model's
   prose, so the cards a person sees are real records.

Channels call the same method. With no browser there is no token to forward, so `AccessTokens.mint` signs a five-minute
token for the channel's owner with the shared secret; downstream services cannot tell the difference.

## Conventions that keep it scalable

**Agents are data in code, settings are data in the database.** `AgentDefinition` is frozen and registered at start-up.
`AgentRegistry` checks it then (duplicate ids, unknown packs, empty pack lists), so a typo fails the deploy, not a user.
Per-person state is keyed by agent id: `selections[agent_id]`, `prompts[agent_id]`. A new agent needs **no migration**.

**Reuse through packs, not copy-paste.** An agent never defines a tool; it names packs. Adding a tool to `cases` upgrades
every agent that uses it. A pack owns its prompt variables (`cases` adds `{{cases}}`), so the editor only offers, and the
validator only accepts, variables the agent can actually fill.

**Tools are bound per request, to the caller.** A pack builds its tools *inside* `tools(ctx)` from `ctx.data`, which holds
the caller's token. There is no module-level state and no service account that sees everything; ownership is enforced by
`practice`, `files` and `drive` exactly as for the browser.

**Two kinds of failure.** A tool that fails (bad argument, nothing found, Drive down) returns `{"error": …}` or raises: the
runner turns it into a tool message the model can work around. An `AiError` from the *provider* (bad key, no credit)
aborts the run and surfaces to the person in words. Never let a tool raise `AiError` for its own problem: catch it and
return the error (the `decide` tool does).

**Small, bounded results.** Tool results are capped (12 000 characters), lists are limited (default 10, at most 25), history
is capped (40 messages), steps are capped (`AGENT_MAX_STEPS`, 8). These are the cost and latency controls; keep them when
adding tools.

**Provider-neutral messages with a passthrough.** `Message` is our shape; adapters translate. `raw` carries the provider's
own assistant turn back untouched, because some models (Gemini 3) need their thought signatures returned. Never read
`raw` outside its adapter.

**Capabilities come from the provider, not a table.** Models and the thinking levels each accepts are listed live. A level a
model doesn't take is replaced by one it does when the choice is saved (`clamp_thinking`), not at question time.

**The prompt is a template, and the whole package is inspectable.** People edit the system prompt, the wrapper around their
question and each tool's description or on/off state, and can preview exactly what would be sent. Variables are
`{{lower_snake}}`, replaced once (a value is never scanned for more variables). `input` must stay in the wrapper.

**Changing a default prompt is safe.** A person who never edited keeps following the code; one who did keeps their text
(`customPrompt: true`) until they press *Reset*. Don't rely on a changed default reaching customised agents.

## Security checklist for anything agent-shaped

- **Secrets** (provider keys, bot and Twilio tokens) are sealed with Fernet (`AGENT_SECRET_KEY`) before they reach the
  database, are never returned (only the last four characters), and never appear in logs. A key sealed with another key
  reads as "not set".
- **Tool output is untrusted input to the model** (a letter in the Drive can say "ignore your instructions"). So tools are
  **read-only by default**. A tool that changes anything must not run on the model's say-so alone: have it return a
  *proposal* (a card the person confirms in the app) and do the write in a normal, authenticated endpoint.
- **Webhooks are public.** Telegram's secret header and Twilio's signature are checked before anything runs; only paired
  chats and listed numbers reach a model; strangers get no reply (WhatsApp) or a fixed "not linked" line (Telegram).
- **Least data to the model.** Cards and tool results carry what the question needs (front of the file), not whole records.
  The provider chosen receives the question and these results; say so wherever a person picks a provider.
- **No ambient authority.** Don't give a pack a database session or a service credential; give it a port.
- **The client is never trusted for the prompt.** A `system` message in a client's history is dropped; the system prompt is
  always built on the server.

## Testing

| Level | How | Example |
| --- | --- | --- |
| Pure rules | plain `pytest`, no fixtures | prompt rendering and validation, registry checks, `match_cases` (`tests/test_units.py`) |
| Packs | a `FakeData` gateway; call `pack.tools(ctx)` and run the tool | filed documents, status filters |
| API / use cases | the `rig` fixture: real app, in-memory SQLite, scripted `FakeLlm`, fake data, Telegram and Twilio | `tests/test_agent_api.py`, `tests/test_channels.py` |
| Adapters | `httpx.MockTransport` returning recorded provider JSON | model filters, tool-call parsing, Jev errors |

`tests/conftest.py` registers a second agent (`librarian`) so tests can prove agents don't share prompts, models or tools.
Every new agent should add at least: its tools are exactly its packs' tools; its prompt rejects variables it doesn't have;
one scripted question end to end.

## When to grow, and how

| You want… | Do this |
| --- | --- |
| A new question-answering agent over existing data | a new `AgentDefinition` using existing packs. No other change. |
| New abilities for several agents | a new pack (or a tool in an existing one). |
| A new data source | extend the `DataGateway` port (or add a second port), implement it in `infrastructure/`, then a pack on top. |
| A new model provider | an `LlmClient` in `infrastructure/llm/`, one line in the factory in `main.py`, the id in `PROVIDER_IDS`. |
| A new place people talk to agents (Slack, email) | a channel in `ChannelService` + its webhook; it already picks an agent by id. |
| Agents that call agents | a pack whose tool runs another agent through `AgentService.chat` with a depth limit (not built yet). |
| Runs longer than ~60 s, scheduled runs, or heavy fan-out | a job queue and a `runs` table; return a run id and poll. Until then keep the step and result limits. |
| One agent with its own data, secrets or deploy cadence | its own service, still using `stiamo_common`. Until then packs give the same separation inside one process. |

Known limits to revisit as use grows: chat memory for channels is in process memory (resets on restart, single replica);
per-person settings are JSON by agent id (fine for dozens of agents; move to rows when you need to query across people);
channel webhooks answer in a background task, so a crash loses that one reply.
