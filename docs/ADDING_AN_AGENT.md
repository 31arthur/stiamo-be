# Adding an agent

Read [AI_AGENTS.md](AI_AGENTS.md) first for the vocabulary (agent, tool pack, tool) and the rules. This page is the
recipe. Everything is in `services/agent/`; nothing outside it changes for an agent over data the service can already read.

## 0. Decide what you are adding

| If the thing is… | Add a… | Touches |
| --- | --- | --- |
| A new job for the model, using abilities that exist (e.g. *hearing prep* using the cases and drive packs) | **agent** | `app/agents/` only |
| A new ability (read tasks, read follow-ups, summarise a hearing) | **tool**, in a **pack** | `app/application/toolpacks/` |
| A new kind of data | a method on `DataGateway` + its HTTP implementation, then a pack | `ports.py`, `infrastructure/data.py` |

Most requests are an agent plus (sometimes) one pack. Don't write an agent that only differs from another by one tool:
give the pack a tool and let the person switch it off in the prompt editor.

## 1. A tool pack (skip if the agent uses existing ones)

`app/application/toolpacks/tasks.py`:

```python
from typing import Any

from app.application.runner import Tool, ToolOutput
from app.application.toolpacks import PackContext, ToolPack
from app.application.toolpacks.common import limit, text
from app.domain.llm import ToolSpec


class TasksPack(ToolPack):
    name = "tasks"
    # Prompt variables this pack fills in. The editor offers them and the validator accepts them
    # only for agents that use the pack.
    variables = {"open_tasks": "How many tasks are open."}

    async def context(self, ctx: PackContext) -> dict[str, Any]:
        return {"open_tasks": len([t for t in await ctx.data.tasks() if t.get("outcome", {}).get("status") == "open"])}

    async def tools(self, ctx: PackContext) -> list[Tool]:
        tasks = await ctx.data.tasks()          # fetched once per question (Memo), with THIS person's token

        async def search_tasks(a: dict[str, Any]) -> ToolOutput:
            q = text(a.get("query")).lower()
            hits = [t for t in tasks if q in str(t.get("title", "")).lower()][: limit(a.get("limit"), 10)]
            cards = [{"kind": "task", "id": t["id"], "title": t.get("title", ""), "due": t.get("dueDate")} for t in hits]
            return ToolOutput({"count": len(cards), "tasks": cards}, cards)   # (what the model sees, what the person sees)

        return [Tool(
            ToolSpec("search_tasks", "Find tasks by words in the title.",
                     {"type": "object", "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}}}),
            lambda a: f"Searched tasks for “{text(a.get('query'))}”",       # the line shown in the trace
            search_tasks,
        )]
```

Rules for a tool:

- Build tools **inside** `tools(ctx)`, closing over `ctx.data`. No module-level data, clients or caches.
- Arguments come from a model: read them with `text()` / `limit()`; never trust types.
- Return `ToolOutput(result, found)`. `result` is what the model reads (keep it small); `found` is a list of cards for the
  person, each a dict with a `kind` and an `id`. Same `kind:id` twice in one run is shown once.
- Failure the model can recover from → return `ToolOutput({"error": "…"})` or raise; the runner reports it as data. Don't
  raise `AiError` (that ends the whole run).
- **Read-only.** A tool that would change something returns a proposal card instead (see the security notes in AI_AGENTS.md).
- Tool names are global within an agent: two packs may not both define `search`. The service refuses at question time.

A new data source first: add `async def tasks(self)` to the `DataGateway` port, implement it in `HttpDataGateway`
(`GET {practice}/v1/tasks`, paged by `_all`), and to `Memo` in `services.py`; the test `FakeData` gets it too.

## 2. The agent

`app/agents/planner.py`:

```python
from app.domain.agents import AgentDefinition

SYSTEM = """You help {{user}} plan their work through {{channel}}.
Use the tools; never guess. Name tasks and cases by their titles and reference numbers.

Tools:
{{tools}}

Today is {{today}}. There are {{open_tasks}} open tasks and {{cases}} cases. Answer in a few short sentences."""

PLANNER_AGENT = AgentDefinition(
    id="planner",                           # lower case, - or _; used in URLs and settings
    name="Planner",
    description="Finds tasks and relates them to cases.",
    system=SYSTEM,
    packs=("tasks", "cases"),               # every name must be a registered pack
    suggestions=("What is overdue?", "Which tasks belong to the Alur case?"),
)
```

Prompt-writing rules that have worked: say who it talks to and through what (`{{user}}`, `{{channel}}`); say what to do when a
tool finds nothing; tell it the cards are shown separately so it should not repeat lists; end with `{{today}}`; list tools with
`{{tools}}` (it follows the person's on/off switches) instead of hard-coding names.

## 3. Register

`app/agents/__init__.py`:

```python
from app.agents.planner import PLANNER_AGENT
from app.application.toolpacks.tasks import TasksPack

AGENTS = [CASE_AGENT, PLANNER_AGENT]          # the first one is the default for new channels

def default_registry() -> AgentRegistry:
    return AgentRegistry(AGENTS, PackRegistry([CasesPack(), DrivePack(), DecisionsPack(), TasksPack()]))
```

Start the service (or run the tests): the registry rejects duplicate ids, unknown pack names and empty pack lists immediately.

That is the whole backend change. The API (`/v1/agent/agents/planner/…`), the prompt editor, the model choice, the chat and the
channel picker all work for it without further code, because they are driven by the registry.

## 4. Tests

Add to `tests/` (the `rig` fixture gives you the app, a client, scripted `FakeLlm`, `FakeData`, and fake Telegram/Twilio):

```python
async def test_the_planner_gets_its_packs_tools_and_variables(rig):
    out = (await rig.client.post("/v1/agent/agents/planner/prompt/preview", json={"question": "x"}, headers=headers())).json()
    assert [t["name"] for t in out["tools"]] == ["search_tasks", "search_cases", "get_case", "case_documents"]
    assert "open_tasks" in (await rig.client.get("/v1/agent/agents/planner", headers=headers())).json()["variables"]

async def test_the_planner_answers_a_question_end_to_end(rig):
    await connect(rig); await choose(rig, agent="/v1/agent/agents/planner")
    rig.script += [ChatResult("", [ToolCall("t", "search_tasks", {"query": "wages"})]), ChatResult("Two tasks.")]
    out = (await rig.client.post("/v1/agent/agents/planner/chat", json={"message": "wage tasks?"}, headers=headers())).json()
    assert out["answer"] == "Two tasks." and out["found"][0]["kind"] == "task"
```

Register the agent in `tests/conftest.py`'s `test_registry()` only if a test needs it beside the others; otherwise the real
`default_registry()` is what production runs.

Checklist before merging: the agent's tool list is exactly what you intend; its prompt rejects a variable from a pack it
doesn't use; a scripted question returns the cards; `ruff check .` and `pytest -q` pass in `services/agent`.

## 5. Frontend

- **Nothing** for a new agent: the AI agents page lists whatever `GET /v1/agent/settings` returns, with its suggestions, model
  choice and prompt editor.
- A new **card kind** (`task` above) needs a card in `src/presentation/ai/FoundCards.tsx` and a member of the `Found` type in
  `src/domain/ai/AgentApi.ts`. Unknown kinds are ignored, not crashed on.

## 6. Done means

- [ ] Pack / agent / registry change, with tests, `ruff` clean.
- [ ] A line about it in [AI_AGENTS.md](AI_AGENTS.md) only if it adds a *concept*; otherwise the code and this recipe are the docs.
- [ ] If it reads a new service: `AGENT_…_URL` in `config.py`, `.env.example`, both compose files, and a line in
      [AGENT_SETUP.md](AGENT_SETUP.md).
- [ ] If it sends new kinds of data to a provider: say so where a person chooses the provider.

## Other extensions (short)

- **A provider:** implement `LlmClient` (`list_models`, `chat`) in `infrastructure/llm/`, translating `Message` ⇄ the wire
  format and reporting capabilities (thinking levels) from the provider's own model list; add the id to `PROVIDER_IDS` and a
  branch in the `llm_factory` in `main.py`; add recorded-JSON tests with `httpx.MockTransport`; add its card to the frontend's
  `PROVIDERS`.
- **A channel:** a connect/verify method and an inbound handler in `ChannelService` (verify the sender *first*, answer in a
  background task, never raise to the platform), a public webhook in `routers/webhooks.py`, a client behind a port in
  `ports.py`. It picks its agent with `public["agent"]`, so every agent works on it.
