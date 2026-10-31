# Workflow agents: agents that watch a source and act on a schedule

> **Status: design.** Chat agents, the run log and the agent hub exist ([AI_AGENTS.md](AI_AGENTS.md)). This page is the
> design for the next kind: agents a person builds in the app ("every 3 hours, sort my inbox") from a **source**,
> **filters**, a **prompt**, **actions** and a **schedule**. It is written to be built in the phases at the end.

## What makes an agent good, and not just working

These are the principles every decision below comes from. If a feature can't be explained with one of them, leave it out.

1. **Code does the plumbing, the model does the judgment.** Fetching, filtering, de-duplicating, scheduling, retrying and
   *acting* are ordinary, tested code. The model only judges (classify, extract, summarise, decide). A model never decides
   *whether it is allowed to act*, and never calls a "move" or "delete" function: it returns a label, and code maps that
   label to an action the person approved.
2. **Structured in, structured out.** Items go in as a fixed shape; the answer comes out as a schema (a label from a closed
   set, a score, a few fields), validated before anything uses it. Free text is for people, not for control flow. For
   decisions between options, prefer a decision model (Jev: probabilities, no prose to parse) or a JSON-schema response.
3. **Unsure means ask.** Every decision carries a confidence. Below a threshold, or when the output fails validation, the item
   becomes *needs review*, never a guess that acts.
4. **Idempotent and resumable.** Every source item has a stable id. A ledger of what was processed (and the outcome) means a
   re-run, a crash or an overlapping schedule never does the same thing twice. "New since last time" is a stored cursor, not a
   guess from the clock.
5. **Actions are reversible, bounded and audited.** Start with *propose* (the person approves in the app). Allow *auto* only
   for reversible actions (label, move to a folder), with a per-run cap, and record before/after so it can be undone. Nothing
   destructive (delete, send) in the first versions.
6. **Test before it matters.** A **test run** executes the whole pipeline on a sample of real items with all actions turned
   into proposals, and shows exactly what would happen. Prompt changes are re-tested on a saved set of labelled examples
   before they go live.
7. **Everything is visible.** Each run, and each item in it, is in the run log: input, prompt, output, decision, confidence,
   action, cost, time. If a person can't answer "why did it do that?" from the history, it is a bug.
8. **Budgets and kill switches.** Per-run item cap, token cap and daily budget; an enabled switch; automatic pause after N
   consecutive failed runs, with a notification. A bad prompt must cost at most a small, known amount.
9. **Least privilege, least data.** Credentials are read-only unless an action needs more, sealed at rest, and scoped to one
   source. Send the model what the decision needs (headers and a trimmed body), not the whole mailbox.
10. **Treat content as hostile.** Email text can contain instructions. Items are passed as clearly delimited *data*, the
    prompt says so, tools are read-only, and the allowed outputs are a closed set. Injection can at worst mislabel one item.
11. **Versioned.** A change to the prompt, filters or actions is a new version with who/when; each run records which version
    ran. Rolling back is one click.
12. **Degrade, don't dither.** A source that is down, a provider that errors or a model that returns junk fails that item
    or run cleanly, is retried with backoff, and is visible; it never blocks other agents.

## The shape of a workflow agent

```
  Trigger ──► Source ──► Filters ──► per item: Decide ──► Policy ──► Action ──► Ledger + Run log
 (schedule,   (email,    (time range,   (prompt → label    (confidence  (label, move,   (idempotency,
  manual,     news,       new only,      + confidence,      threshold,   notify,         what happened,
  test)       table…)     sender…)       validated)         propose/auto) write a row)    undo data)
```

| Stage | What it is | Notes |
| --- | --- | --- |
| **Trigger** | `schedule` (every N minutes/hours, or cron), `manual`, `test` | Durable in the database; see *Running them*. |
| **Source** | A connector that yields **items**: `{id, received_at, fields…}` | One interface, many connectors. Read-only by default. |
| **Filters** | Declarative, deterministic: time range, `new since last run`, folder, sender, flags, regex | Run in code *before* the model, so you pay for fewer items. |
| **Decide** | The model call: the person's prompt + one item (or a small batch) → a structured result | Schema = the labels the person defined. Jev or JSON-schema output. |
| **Policy** | Per label: confidence threshold, `propose` or `auto`, per-run cap | The only place that authorises an action. |
| **Action** | A connector operation: label, move to folder, mark read, write a row, notify | Reversible ones first. Records enough to undo. |
| **Ledger / log** | Per item: id, decision, confidence, action, status; per run: counts, cost, duration | Feeds History, de-duplication and the undo view. |

### Example: the email spam filter

| | |
| --- | --- |
| Source | Email (IMAP), folder *INBOX* |
| Filters | received in the last 24 h · only items not yet in the ledger · skip starred |
| Prompt | "Classify the message as `spam`, `promotion`, `newsletter`, `needs_reply` or `important`. Spam is unsolicited…" |
| Labels → policy | `spam`: auto-move to *Stiamo/Spam* if ≥ 0.9, else propose · `promotion`, `newsletter`: auto-label · `needs_reply`, `important`: never move, label and notify |
| Schedule | every 3 hours, max 200 messages per run |
| Test run | the last 30 messages, nothing applied; shows each message, label, confidence and the action it *would* take |

## Building blocks, and how they fit the code that exists

Everything attaches to what is in `services/agent/` today. The chat agent, the registry, packs and the run log do not change.

**Definitions.** Built-in agents stay code (`app/agents/`). A person's agents are rows in a new `custom_agents` table and are
turned into an `AgentDefinition`-compatible object at request time, so the hub, history, model and prompt editor treat them
alike. A definition stores *configuration only*: source, filters, labels, policy, schedule, prompt, `version`.

| Table | Holds |
| --- | --- |
| `workflow_agents` | id, user_id, name, `config` JSON (source, filters, labels, policy, schedule, prompt), version, enabled, cursor, next_run_at, consecutive_failures |
| `workflow_agent_versions` | agent id, version, config, who/when: the history of changes, for rollback |
| `source_accounts` | user_id, kind (`imap`, …), sealed credentials (Fernet, as for channels), a label. A source is connected once and reused. |
| `ledger` | agent id, item id, run id, decision, confidence, action, status (`applied`, `proposed`, `skipped`, `failed`), before/after for undo. Unique on (agent id, item id, version-independent key) |
| `agent_runs` *(exists)* | one row per run (`trigger = schedule/test/manual`); the items of a run are its `steps` (or a child table if runs get large) |

**Source connectors** are a port, next to `DataGateway`:

```python
class Source(ABC):
    kind: str
    async def items(self, flt: Filters, cursor: Cursor | None, limit: int) -> Page: ...   # read-only
    async def apply(self, item_id: str, action: Action) -> Undo: ...                        # only if the account allows
```

`ImapSource` (stdlib `imaplib` in a worker thread, or `aioimaplib`): `UID` as the item id, `UIDVALIDITY` + last UID as the
cursor, `SINCE` for time ranges, `BODY.PEEK[]` so reading never marks mail read, `UID MOVE`/`STORE +FLAGS` for actions.
Later: a `TableSource` (rows of a Stiamo or external table, with an updated-at cursor), `FeedSource` (RSS/news),
`DriveSource` (new files). Each is a small class plus a settings form.

**Decide** reuses what exists: the prompt template and variables (`{{item}}`, `{{labels}}`, `{{today}}`), the person's model
and thinking level, the decision pack/Jev for closed-set choices, and `run_agent` when the agent needs tools. An item's text is
trimmed to a budget and wrapped as data. The result is validated against the label set; anything else is `needs_review`.

**Policy + Actions** are plain code: a table `label → {threshold, mode, cap}`. `propose` writes a ledger row and a card in
the app ("12 messages to move: Approve all / review"); approving runs the same `apply` path as `auto`.

## Running them

- **Scheduler.** A loop inside the agent service (started in `lifespan`) wakes every ~30 s, claims due agents with a
  database **lease** (`UPDATE … SET leased_until = now()+X WHERE next_run_at <= now() AND (leased_until IS NULL OR leased_until < now())
  RETURNING …`, or `FOR UPDATE SKIP LOCKED` on PostgreSQL), runs them, then sets `next_run_at` (with jitter). Two replicas can't
  run the same agent; a crashed run is picked up after its lease expires. No new infrastructure until volume needs a queue.
- **No overlap.** A schedule that fires while the last run is still going is skipped and logged, not stacked.
- **Limits per run.** Max items, max tokens, max wall time, concurrency of 1–4 items; items beyond the cap wait for the next run
  (the cursor only advances over what was processed).
- **Retries.** Transient errors (network, 429, 5xx) retry with exponential backoff and a cap; the item stays unprocessed in the
  ledger. Permanent errors (auth, schema) fail the run and increment `consecutive_failures`; at 3, the agent pauses and says why.
- **Auth for scheduled runs.** As for channels today: a short-lived token minted for the owner (`AccessTokens.mint`) so the
  other services answer as they would to the browser. Source credentials come from `source_accounts`.

## Testing and evaluating

- **Test run** (`trigger = test`): the real pipeline on N recent items, actions forced to *propose*, nothing written to the
  ledger as applied. The result is a table: item · label · confidence · would-do. Fix the prompt, run again.
- **Examples.** From any test or past run a person can mark a decision *right* or *wrong* (or relabel). Those become a small
  labelled set stored with the agent. Before saving a new prompt version, the app replays the set and shows accuracy old vs.
  new. This is the single best defence against a prompt edit quietly making things worse.
- **Unit tests** for each connector with a fake server, for filters and policy as pure functions, and API tests with the existing
  `rig` (scripted model, fake source).

## The app

The sidebar already lists each agent. A **New agent** button will open a short wizard, and the result is a normal agent in the hub
(Overview · Try it · Settings · Channels · History).

1. **Start:** a template (Email filter, Digest, Blank) or describe it in a sentence.
2. **Source:** pick or connect an account; folder; **filters** (time range, new since last run, unread only, sender).
3. **Decide:** the prompt (with the variables panel and the whole-package preview, as today), the labels and their descriptions.
4. **Act:** a row per label: threshold and *propose / auto*; caps.
5. **Schedule:** every N hours / daily / manual; the per-run cap; notifications.
6. **Test run → review → enable.** Nothing runs on a schedule until a test run has been looked at.

History gains a per-item view for scheduled runs (what was in the inbox, what each message got and why), an **Undo**
for applied actions, and a **Review** queue for proposals and low-confidence items.

## API sketch (under `/v1/agent`)

| Call | Meaning |
| --- | --- |
| `POST /sources` · `GET /sources` · `DELETE /sources/{id}` | connect (credentials checked, then sealed), list, remove a source account |
| `POST /workflows` · `GET /workflows/{id}` · `PUT /workflows/{id}` | create / read / update (each update is a new version) |
| `POST /workflows/{id}/test` | the test run; returns what would happen, per item |
| `POST /workflows/{id}/enable` · `…/disable` · `…/run-now` | the schedule's switch, and a manual run |
| `GET /workflows/{id}/review` · `POST …/review/approve` | proposals and low-confidence items; approve applies them |
| `POST /workflows/{id}/undo/{runId}` | reverses what a run applied (where the connector can) |

Existing history, stats and the agent hub work unchanged: they read `agent_runs`.

## Build in phases

| Phase | Delivers | Not yet |
| --- | --- | --- |
| **1** (done) | Registry, packs, per-agent prompt/model/channels, **run log, stats, history, hub** | |
| **2** | `workflow_agents` + versions, the pipeline (filters → decide → policy), the ledger, **test run**, a *manual* and a *Stiamo-table* source, propose-only actions, the wizard | schedules, IMAP |
| **3** | The scheduler with leases and limits, enable/pause/failure handling, notifications, the review queue, undo | write actions on external systems |
| **4** | `ImapSource` (read-only first, then label/move with undo), source accounts with sealed credentials | |
| **5** | Examples and prompt regression checks, more sources (feeds, Drive), batch decisions for cost, agent-calls-agent | |

## Decisions worth making early (and the recommendation)

| Question | Recommendation |
| --- | --- |
| Where do workflow runs execute? | In the `agent` service (scheduler in `lifespan`) until volume or run length demands a queue and workers; the lease design makes that move mechanical. |
| One model call per item, or batches? | Per item at first (simple, easy to debug, per-item confidence). Batch of 10–20 once the cost matters and the evals show no loss. |
| How are results parsed? | Closed-set labels via Jev or a JSON-schema response, validated; anything outside the set is *needs review*. Never regex free text. |
| Auto-apply by default? | No. New agents start in *propose*; *auto* is a per-label switch offered after a clean test run. |
| Which email access? | IMAP with an app password first (works with Gmail/Outlook/most hosts, sealed, revocable). OAuth later if wanted. |
| Delete mail? | Not in v1. Move to a "Stiamo/Spam" folder, which the person empties. |
| Retention of item content | Keep ids, headers, decision and a short excerpt in the log; not whole bodies. Say so in the UI and allow clearing. |
