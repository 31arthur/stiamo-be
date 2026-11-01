# Workflow agents: agents that watch a source and act on a schedule

> **Status: phase 2 is built** (see *Build in phases*). Chat agents, the run log and the agent hub exist
> ([AI_AGENTS.md](AI_AGENTS.md)). This page is the design for agents a person builds in the app ("every 3 hours, sort my
> inbox") from a **source**, **filters**, a **prompt**, **actions** and a **schedule**. What exists today: build an agent from a
> pasted-text or Stiamo-collection source, filters, labels and a prompt; **test run** it; **run it now**; **review** what it
> proposes. Not yet: schedules, IMAP, and actions that change the source.

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
| `workflow_ledger` | agent id, item id, run id, version, label, confidence, reason, status (`proposed`, `needs_review`, `approved`, `dismissed`; later `applied`), before/after for undo. Unique on (agent id, item id) |
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

## API (under `/v1/agent/workflows`)

Built in phase 2 (full request/response shapes in [API.md](API.md)):

| Call | Meaning |
| --- | --- |
| `GET` · `POST` | list the agents a person built · build one (the setup is validated and explained) |
| `GET/PUT/DELETE /{id}` | read · change (a changed setup is a new version; renaming isn't) · delete with its ledger and history |
| `POST /test` | try an **unsaved** setup on a sample: nothing is kept |
| `POST /{id}/test` | the test run of the saved setup: up to 20 items, one line in the history, no ledger |
| `POST /{id}/run` | run now: decide the new items, keep each decision for review |
| `GET /{id}/review` · `POST /{id}/review` | what was proposed or unsure · approve (optionally correcting the label) or dismiss |

Planned for the later phases: `POST/GET/DELETE /sources` (connected accounts, sealed), `…/enable`, `…/disable` (the
schedule), `…/undo/{runId}` (reverse what a run applied).

Existing history, stats and the agent hub work unchanged: they read `agent_runs`.

## Build in phases

| Phase | Delivers | Not yet |
| --- | --- | --- |
| **1** (done) | Registry, packs, per-agent prompt/model/channels, **run log, stats, history, hub** | |
| **2** (done) | `workflow_agents` + versions, the pipeline (filters → decide → policy), the ledger, **test run**, run now, a review queue, a *manual* (pasted) and a *Stiamo-collection* source, the builder in the app | schedules, IMAP, actions on the source: an approval is recorded, nothing is changed anywhere yet |
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

## Mailbox sources (implemented, a first slice of phase 4)

`source.kind = "mail"` reads a folder of a mailbox connected in the Mail section (`accountId`, `folder`), newest first, a page
at a time. An item's id is `UIDVALIDITY:UID`. Mail is hostile input: the sender, subject and the start of the text are what
the model sees, inside `<item>` tags, and it is told never to follow anything in them.

- `rules`: `protectedDomains`, `protectedPhrases`, `protectCorrespondents`. Applied in code before the model; protected mail
  is counted, not decided, and not put in the ledger.
- `action`: `{trashLabel, auto}`. Mail labelled `trashLabel` with enough confidence is moved to Trash (`auto: true`), or
  waits in Review as a proposal and is moved when approved. Everything else judged fine is `kept`. Test runs never move.
- Ledger statuses added: `kept` and `applied`. Nothing is ever deleted: the only mail action is "move to Trash".
- Not yet: a schedule (runs are started by a person), undo from the app (the mail is in Trash), labels and other folders.

### Several mailboxes, batches, the decision model, the prompt

- `source.accountIds`: one or several mailboxes, each read newest first, protected by its own Sent folder, and moved from on its
  own. Item ids are `mailbox:UIDVALIDITY:UID`.
- `batchSize` (default 1, up to 50): how many items go to the model in one request. A batch sends the instructions once and
  the items numbered `1…n`; the model answers with a JSON array. Fewer requests and less repeated text cost less. An item the
  model leaves out is asked about alone; an unreadable batch is retried once. With the decision model the same idea is one
  typed question per item in one request. Token use is shared out across the items, so totals stay exact.
- `decisionModel`: which Jev model decides (through OpenRouter); empty uses the person's default.
- A run reports `stats`: messages scanned, protected, judged, requests, batch size, model, mailboxes and cost.
- `POST /v1/agent/workflows/prompt-preview {config}` returns exactly what would be sent (system text, example items, or the
  decision model's state and questions) without asking any model, so the prompt can be read before anything runs.

### Working through a whole inbox, and the safe-senders shield

- **Which mail:** `filters.after` / `filters.before` (dates; start inclusive, end exclusive) or `filters.sinceDays` (up to 10 years);
  with none of them the whole folder is in range. The mail service lists the uids in range first (`GET .../uids`, no mail
  read), the agent drops those already in its ledger, and reads the rest a hundred at a time (`POST .../messages/summaries`)
  until it has `filters.limit` (up to 500) to decide about. So a run costs one listing and one read of what it will use,
  however large the inbox.
- **A few at a time:** a run decides at most `limit` new messages and reports `stats.remaining`; running again carries on
  with the next ones. Messages kept by a rule are written to the ledger with status `protected`, so they are never read
  again and never stand in the way. Test runs remember nothing.
- **Safe senders** (`rules.protectCorrespondents`, on by default): anyone you have written to from the mailbox, read from its Sent
  folder (up to the last 5,000 sent messages), is never judged. `rules.protectedAddresses` adds addresses by hand.
  `POST /v1/agent/workflows/safe-senders {accountIds}` shows who it would protect.

### Executions

Every real run (not a test run) is kept as an *execution* (`workflow_executions`, migration 0005): when it ran, how long it took,
what it read and decided (`counts`, `stats`), and its summary. The items are the ledger rows carrying the run's id, so an
execution always shows each item as it stands now (a person may have approved or dismissed some since); `now` gives the live
counts next to `counts`, which is what the run itself did.

- `GET /v1/agent/workflows/{id}/executions`: newest first.
- `GET /v1/agent/workflows/{id}/executions/{runId}`: `{execution, items[]}`, least sure first. Each item keeps the start of the
  email (sender, subject, text) so it can still be read after the mail was moved to Trash.

### Learning from corrections

When an email is judged spam and the owner says it is not, the agent asks why and learns from the reason without touching what
the owner wrote:

1. `POST .../feedback {itemId, reason}`: the agent's own chat model reads the whole setup (purpose, labels, points already
   added), the email and the reason, and suggests **one general point** (at most 30 words) to add, or says an existing point
   already covers it. It can also name a domain to always keep. Nothing is changed by asking. With no model to ask, the owner's
   own words are the suggestion.
2. `POST .../refinements {point?, protectDomain?}`: the owner confirms (and may edit) the suggestion. The point is added to
   `config.refinements` (up to 30; duplicates ignored) and the setup becomes a new version.
3. Refinements are added to every form of the prompt: a block "Extra guidance" between Purpose and Labels for a chat model, and
   after the purpose in the decision model's state or instructions. `prompt`, `labels` and `guide` are never edited by it.
   `DELETE .../refinements/{index}` forgets a point.

The decision also records the reason (`You said it isn't spam: …`) on the item in its execution.

### One run of an agent at a time

`POST .../run` is refused with `409` ("This agent is already running") while that agent is running. Without it, two runs (two
tabs, or a manual run during "Run through the whole inbox") would judge the same emails twice, move them twice, and collide
writing the same ledger rows, losing the record of the run that finished second. Different agents run side by side, each with its
own ledger, and test runs are not limited. The lock is held in the agent service's memory, so run **one replica** of `agent`.

### Running through a whole inbox in the background

`POST .../sweep` runs the agent again and again (real runs, a batch of `filters.limit` each) until nothing is left in range, it is
stopped, or something fails. It runs inside the agent service, so closing or refreshing the page doesn't stop it, and a fresh
token is minted for the owner for every run (the browser's lasts minutes). Each run is an ordinary execution.

- `GET .../sweep`: `{sweep: {state: running|done|stopped|failed, runs, judged, moved, kept, toReview, protected, failedItems,
  inRange, remaining, lastRunId, error, stopRequested, startedAt, finishedAt}}` (null if there never was one).
- `POST .../sweep/stop`: stops after the run in progress, never half way through one.
- One sweep per agent. While it runs, a run started by hand is refused (`409`), and a sweep waits (up to two minutes) if a hand-started
  run is finishing. It ends if every item of a run fails (a provider refusing everything), instead of retrying forever.
- The state is held in memory. If the service restarts the sweep ends (what its runs did is kept) and is started again by hand.
  Run **one replica** of `agent`.

### Reading for what matters: the Attention queue

A second agent can go on from the first. `source.kind = "agent"` with `source.upstreamId` reads the mail another agent has already
judged and kept (its `kept` and `protected` items, and ones you said weren't spam). With `source.runUpstream` (on by default) a
run first runs that agent, so new mail has been through its rules, its safe senders and its decision model, and what it trashes never
reaches this one. A test run doesn't run it (that would move mail). The items keep the upstream ids, so they are the same emails.

Labels have a `route`:
- `none`: kept, nothing more (informational mail).
- `attention`: the email is read in full, and a chat model writes a summary, a priority (high, normal, low), a suggested action
  and a due date. It waits in the **Attention** queue (`status: queued`).
- `event`: the same, and if the email names a specific date, a calendar event is proposed (`status: event_proposed`).

Reading the email needs a **chat model** (the agent's own, chosen in its Settings): the decision model only chooses between labels.
The model is told never to invent a date; a date it can't make into a real one leaves the email as a plain queue item.

- `GET /v1/agent/attention?workspace=`: `{items[], count}` across the owner's agents, filtered by each agent's workspaces.
- `POST /v1/agent/attention/act {agentId, itemId, action, event?}`: `done`, `dismiss`, `skip_event` (keep the email, drop the
  event) or `approve_event` (the owner may have changed the event; it is checked again, then a meeting is created in the
  calendar through the practice service). An item is dealt with once (`409` after).
- Ledger entries carry `data` (migration 0007): the summary, priority, action, due date, event and, once approved, `meetingId`.

### Listening to a mailbox

`config.listen: true` makes an agent that reads a mailbox (or goes on from one that does) run by itself when new mail arrives, instead of
only when someone presses Run. The agent service keeps a watcher on each mailbox: it reads the mail service's live feed (IMAP IDLE,
`GET /v1/mail/accounts/{id}/events`) with a token minted for the owner, so the server says when mail arrives and nothing is polled.

- A change wakes the agent once after a short pause (5 seconds), so a burst of mail is one run. The run is a background sweep
  (see above), so it carries on until nothing new is left; if one is already going, it picks the new mail up itself.
- When it starts listening, and after any dropped connection, it runs once to catch up on what came meanwhile.
- A dropped feed reconnects with a growing pause (2 seconds up to 2 minutes). A refused mailbox password ends the feed with a reason.
- `GET /v1/agent/workflows/{id}/listen`: `{listening, state: off|connecting|listening|reconnecting, mailboxes, lastEventAt, lastRunAt, runs, error}`.
- It follows the setup: saving an agent with `listen` on starts it, off (or deleting the agent) stops it, and when the service
  starts the agents that were listening listen again. It is held in the agent service's memory: run **one replica** of `agent`.
- Listening runs are real runs. For a spam filter with "move it by itself" on, mail is moved as it arrives. Test runs never listen.

### News monitor

`source.kind = "feed"` reads RSS feeds (`source.feeds[]`: `key`, `url`, `lang`), for example Google News searches, and keeps the stories
that matter. Every run:

1. **Fetch** the feeds (a feed that is down is skipped), parse them, drop what is older than `filters.sinceDays` and what is already
   known (by id), newest first.
2. **Read only what is new**, up to `filters.limit` per run: the publisher's real address is decoded from the Google link (older
   links carry it inside them; newer ones go through Google's batchexecute call), then the publisher's page gives its description
   and picture (the page's own tags, and if it has none, its first real paragraphs; a failure leaves the story with its headline).
   How many new stories there are in all is `stats.remaining`, so a backlog is worked through in runs.
3. **First question (relevance):** the labels, as for any agent (for S&B: `relevant` and `irrelevant`). A label with `route: "publish"`
   puts its stories on the news page (`status: published`); the others are `irrelevant`.
4. **Second question (importance), only for the relevant:** `config.importance` is `{prompt, levels[{name, description, weight 1-4}]}`,
   asked of the same model, in batches, so the cost follows what was kept. Without it every story has weight 2.
5. **Rank** (when the page is read, because age changes): `weight/4 x 0.5^(hours/36) x (0.5 + relevance/2)`: how much it matters counts for
   most, it halves every 36 hours, and how sure the first question was breaks ties. So a critical story from today beats a major one
   from yesterday, and last week's critical story sinks below today's notable one.

- `GET /v1/agent/news?workspace=&limit=20&days=7`: `{items[], total, generatedAt}`, each `{agentId, itemId, title, source, link, description,
  imageUrl, publishedAt, importance, weight, lang, feed, relevance, rank}`.
- `POST /v1/agent/news/hide {agentId, itemId}`: takes a story off the page (`204`).

### Running every so often

`config.scheduleMinutes` (10 to 1,440) runs the agent when it is saved and then every that many minutes, as a background sweep (see
above); if one is still going, the new one is skipped. For sources that can't say when something is new, like feeds. It follows the
setup like listening does and starts again when the service starts. `GET .../listen` also returns `schedule: {scheduled, everyMinutes,
nextRunAt, lastRunAt, runs, error}`. In memory: run **one replica** of `agent`.
