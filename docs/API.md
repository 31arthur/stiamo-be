# API reference (for wiring the frontend later)

Base URL: `https://stiamo-be.fullstackarthur.com`. Every route except `/health` and `/v1/files/{token}` needs
`Authorization: Bearer <access token>` from `identity`. JSON is **camelCase** and leaves out unset optional fields, so
records have the same shape as the frontend's domain types (`Task`, `Meeting`, `LegalCase`, …). Errors are always
`{"error": {"code", "message"}}`. Interactive docs: `/docs` on each service (not routed publicly).

Records are owned by the signed-in user: `userId` is set from the token, `createdAt`/`updatedAt` by the server,
and `id` is the client's UUID when given (`PUT`), otherwise generated (`POST`).

## Services

| Service | Owns | Public routes |
| --- | --- | --- |
| identity | accounts, sessions | `/v1/auth/*`, `/v1/users/*` |
| practice | the working domain | everything in the sections below except attachments |
| files | attachments + bytes | `/v1/attachments/*`, `/v1/files/*` |
| records | generic document store (legacy; retire once the frontend uses `practice`) | `/v1/collections/*` |
| drive | search and bytes of the owner's Google Drive letters | `/v1/drive/*` |
| agent | AI providers and models, agents and their prompts, chat channels | `/v1/agent/*` |
| mail | IMAP mailboxes per workspace, messages, live events | `/v1/mail/*` |

## Every record type (`practice`)

`/v1/tasks`, `/v1/follow-ups`, `/v1/meetings`, `/v1/cases`, `/v1/hearings`, `/v1/plans`, `/v1/goals`, `/v1/counsel`,
`/v1/clients`, `/v1/projects` all have the same five calls:

| Call | Meaning (frontend `Repository`) |
| --- | --- |
| `GET /v1/<type>?after=&limit=&updatedAfter=` | `list` → `{items, nextCursor}`; `updatedAfter` (ISO instant) for incremental sync |
| `GET /v1/<type>/{id}` | `get` |
| `PUT /v1/<type>/{id}` | `save` (create or replace; keeps `createdAt`) |
| `POST /v1/<type>` | create with a generated id |
| `DELETE /v1/<type>/{id}` | `remove` (idempotent) with the same side effects as the frontend's use case |

`DELETE`/`PUT` run the domain rules on the server: history lines are written, links are removed, a meeting keeps its
confirmation follow-ups in step, a client takes its projects with it, a counsel returns their cases to us, a case takes
its sole hearings, a plan frees its steps, and so on.

## Use cases (frontend class → endpoint)

| Frontend | Endpoint |
| --- | --- |
| `RecordOutcomeUseCase` | `POST /v1/outcomes` `{ref, outcome, spawn?}` (task, followUp, meeting, hearing) |
| `FollowUpUseCases.logAttempt / suggest` | `POST /v1/follow-ups/{id}/attempts`, `POST /v1/follow-ups/{id}/suggestion` (send `at: {date, minute}` — your local time) |
| `CaseUseCases.open` | `POST /v1/cases` (the case number is generated; any `reference` sent is ignored) |
| `CaseUseCases.assignCounsel` | `PUT /v1/cases/{id}/engagement` `{engagement \| null}` |
| `CaseUseCases.recordHearing` | `POST /v1/hearings/{id}/record` |
| `CaseUseCases.joinCases` / `saveHearing` | `PUT /v1/hearings/{id}/cases`, `PUT /v1/hearings/{id}` |
| `HearingActionUseCases.toTasks / toPlan` | `POST /v1/hearings/{id}/actions/tasks`, `POST /v1/hearings/{id}/actions/plan` |
| `diaryOf`, `searchDiary`, `unrecordedHearings`, `nextHearing`, `pendingActions` | `GET /v1/hearings/diary?search=&caseId=`, `/v1/hearings/unrecorded`, `/v1/cases/{id}/next-hearing`, `/v1/hearings/{id}/pending-actions`, `/v1/cases/{id}/hearings` |
| `PlanUseCases.createWithSteps` | `POST /v1/plans/with-steps` `{plan, steps, goalIds}` |
| `addStep`, `reorderSteps`, `setStepDate`, `convertStep` | `POST /v1/plans/{id}/steps`, `PUT /v1/plans/{id}/steps/order`, `PUT /v1/steps/{kind}/{id}/date`, `POST /v1/steps/{kind}/{id}/convert` |
| `setDueDate`, `cover`, `uncover`, `duplicate`, `saveDoc` | `PUT /v1/plans/{id}/due-date`, `POST /v1/plans/{id}/cover`, `/uncover`, `/duplicate`, `PUT /v1/plans/{id}/doc` |
| `planSteps`, `planProgress` | `GET /v1/plans/{id}/steps`, `GET /v1/plans/{id}/progress` |
| `PlanUseCases.link / unlink` (goal) | `PUT / DELETE /v1/plans/{id}/goals/{goalId}` |
| `goalProgress` | `GET /v1/goals/{id}/progress?today=` · check-ins `POST /v1/goals/{id}/check-ins` · key result `PUT /v1/goals/{id}/key-results/{krId}` |
| `LinkUseCases` | `POST /v1/links`, `DELETE /v1/links/{id}`, `GET /v1/links?kind=&id=`, notes `POST /v1/activity/notes` |
| record history | `GET /v1/activity?kind=&id=`, whole thread: `GET /v1/relations/thread/history?kind=&id=` |
| `RelationGraph.thread` | `GET /v1/relations/thread?kind=&id=` |
| `buildAgenda`, `AgendaUseCases` | `GET /v1/agenda?start=&end=`, `PUT /v1/agenda/schedule` (returns the prior state for undo) |
| `AttachmentUseCases` | `POST /v1/attachments` (multipart), `POST /v1/attachments/links`, `GET /v1/attachments?kind=&id=`, `PATCH`/`DELETE /v1/attachments/{id}` |
| `FileStore.url` | `POST /v1/attachments/{id}/url` → short-lived `/v1/files/{token}` for `<img>`/`<iframe>`; `GET /v1/attachments/{id}/content` with the header |

## Drive search (`drive`)

Searches the owner's Google Drive, names and contents, without copying anything. Only the emails listed in
`DRIVE_ALLOWED_EMAILS` may use it (see [DRIVE_SETUP.md](DRIVE_SETUP.md)).

| Call | Meaning |
| --- | --- |
| `GET /v1/drive/status` | `{available, folder?, reason?}`: whether the signed-in user can search, and the shared folder's name |
| `GET /v1/drive/search?q=&limit=&pageToken=` | `{items: [{id, name, mimeType, modifiedTime, webViewLink, size?}], nextPageToken?}`; every word must match, `"quoted phrases"` stay together; no `q` lists the newest files |
| `POST /v1/drive/suggest` `{terms: [...]}` | files mentioning any of the details (case number, court number, parties…), best first, each with the terms it `matched` |

Attach a result to a record with `POST /v1/attachments/links` (`url` = the file's `webViewLink`).

## What stays in the frontend

Pure presentation and device logic is deliberately not duplicated here: the day planner's reflow/packing
(`packDay`), month grids, activity phrasing (`describe`), labels and formatting, block-editor behaviour and the
offline mode. These need no database and no server round trip.

## Time zones

The server's clock is UTC. Anything that depends on "today" or "now" in the user's day accepts it from the client
(`today=`, `at: {date, minute}`); otherwise the UTC date is used.

## AI agents (`agent`)

How the service is built: [AI_AGENTS.md](AI_AGENTS.md). Every call needs the bearer token. Provider keys are write-only:
they are checked, sealed and never returned (only `last4`). `{agent}` is an agent id from `GET /v1/agent/settings`
(unknown ids are `404`).

**Shared by every agent**

| Call | Meaning |
| --- | --- |
| `GET /v1/agent/settings` | `{providers, decision, decisionModels, thinkingLevels, agents[], defaultAgent}`; each agent has `{id, kind, name, description, suggestions, selection, selectionInherited, customPrompt, workspaces}` |
| `PUT /v1/agent/providers/{openai\|gemini\|openrouter}` `{apiKey}` | checks the key with the provider, then keeps it; `{connected, last4, models[]}` (`422` if refused) |
| `DELETE /v1/agent/providers/{id}` | forgets the key and any model choice that used it |
| `GET /v1/agent/providers/{id}/models` | `{items: [{id, name, thinking[]}]}`, asked of the provider live |
| `PUT /v1/agent/decision` `{enabled, modelId}` | lets agents that use the decisions pack ask Jev (needs an OpenRouter key) |
| `POST /v1/agent/decide` `{state, questions, modelId?}` | asks Jev typed questions (`choice`, `noul`, `score`); `{answers, model, cost}` |

**One agent**

| Call | Meaning |
| --- | --- |
| `GET /v1/agent/agents/{agent}` | the summary plus `{prompt, defaults, variables, packs}` for the prompt editor |
| `PUT /v1/agent/agents/{agent}/selection` `{providerId, modelId, thinking}` | the model for this agent; a thinking level the model doesn't take is replaced |
| `PUT /v1/agent/agents/{agent}/workspaces` `{workspaces[]}` | the workspaces (`personal`, `sb`, `fsa`, `media`) an agent is shown in; it can belong to several, or none (then only "all workspaces" shows it). The case agent starts in `sb`. |
| `PUT /v1/agent/agents/{agent}/prompt` `{system, inputTemplate, tools}` | saves the person's prompt (`422` for unknown `{{variables}}` or a missing `{{input}}`) |
| `DELETE /v1/agent/agents/{agent}/prompt` | back to the agent's default |
| `POST /v1/agent/agents/{agent}/prompt/preview` `{question, prompt?}` | `{system, user, tools[]}`: exactly what would be sent, with unsaved edits if `prompt` is given |
| `POST /v1/agent/agents/{agent}/chat` `{message, messages?}` | `{answer, steps[], found[], messages[]}`; send `messages` back to continue the conversation |

**What an agent has done** (see "Run log" in [AI_AGENTS.md](AI_AGENTS.md))

| Call | Meaning |
| --- | --- |
| `GET /v1/agent/agents/{agent}/stats` | `{totalRuns, windowDays, runs, ok, errors, avgDurationMs, inputTokens, outputTokens, byTrigger, daily[14], lastRun}` |
| `GET /v1/agent/agents/{agent}/runs?limit=&before=` | newest first: `{items[{id, trigger, status, startedAt, durationMs, provider, model, input, output, found, steps, error?}], next}` |
| `GET /v1/agent/runs/{id}` | one run in full: adds `userMessage`, `systemPrompt`, `tools`, `steps[{tool, label, args, found, ms, result, error?}]`, `usage`, `thinking`, `channel` |
| `DELETE /v1/agent/agents/{agent}/runs` | clears that agent's history |

**Channels** (`/v1/agent/channels`)

| Call | Meaning |
| --- | --- |
| `GET /v1/agent/channels` | connected channels (never their tokens) |
| `POST /v1/agent/channels/telegram` `{botToken, agent?}` | validates the bot, sets its webhook, returns a pairing code and link |
| `POST /v1/agent/channels/whatsapp` `{accountSid, authToken, sender, allowed[], agent?}` | validates the Twilio account; returns the `webhookUrl` to paste into Twilio |
| `POST /v1/agent/channels/{id}/pairing` · `DELETE …/pairing/{chatId}` | new Telegram pairing code · unlink a chat |
| `PUT /v1/agent/channels/{id}/agent` `{agent}` | which agent answers on this channel |
| `PUT /v1/agent/channels/{id}/allowed` `{allowed[]}` | the WhatsApp numbers that may use it |
| `POST /v1/agent/channels/{id}/test` | sends a hello to the linked chat or numbers |
| `DELETE /v1/agent/channels/{id}` | disconnects (removes the Telegram webhook) |

`POST /v1/agent/webhooks/telegram/{id}` and `/whatsapp/{id}` are called by Telegram and Twilio, not by the app: they are
authenticated by Telegram's secret header and Twilio's signature, not a bearer token.

## Workflow agents (`agent`)

Agents a person builds, as designed in [WORKFLOW_AGENTS.md](WORKFLOW_AGENTS.md). They also appear in
`GET /v1/agent/settings` (`agents[]`, with `kind: "workflow"`), and `selection`, `stats`, `runs` and `runs/{id}` work for
their ids (`wf-…`) exactly as for the built-in agents.

The **setup** (`config`, camelCase):

```json
{
  "source":  {"kind": "manual | table", "table": "cases | tasks | follow-ups | meetings | clients | projects"},
  "filters": {"sinceDays": 30, "newOnly": true, "contains": "fee", "limit": 50},
  "prompt":  "What the sorting is for.",
  "labels":  [{"name": "urgent", "description": "Needs doing this week.", "threshold": 0.8}, {"name": "routine", "description": "Can wait."}],
  "decider": "model | jev",
  "defaultThreshold": 0.7
}
```

| Call | Meaning |
| --- | --- |
| `GET /v1/agent/workflows` · `POST` `{name, description, config, workspaces?}` | list · build (`422` explains what is wrong: fewer than 2 labels, a repeated or badly written label name, an unknown collection…) |
| `GET/PUT/DELETE /v1/agent/workflows/{id}` | read `{id, name, description, version, config, ledger{status:n}, pending, lastRunAt}` · change · delete |
| `POST /v1/agent/workflows/test` `{config, manual?}` | try an unsaved setup on a sample; nothing is kept |
| `POST /v1/agent/workflows/{id}/test` `{manual?}` | test run (max 20 items); one `trigger: "test"` line in the history; no ledger |
| `POST /v1/agent/workflows/{id}/run` `{manual?}` | run now; decisions go to the ledger (`trigger: "manual"`); items decided before are skipped while `newOnly` |
| `GET /v1/agent/workflows/{id}/review` | `{items[{id, title, excerpt, label, confidence, reason, status}]}`: proposed and needs-review |
| `POST /v1/agent/workflows/{id}/review` `{itemIds[], action: "approve" \| "dismiss", label?}` | `{changed}`; an item with no label can only be approved with one chosen |

A run's result: `{runId, mode, counts{items, proposed, needsReview, failed}, matched, usage, summary, durationMs, items[{id, title, excerpt, label, confidence, reason, status, error?, ms}]}`.
`manual` is pasted text, items separated by a line of `---`.

## Mail (`mail`)

Mailboxes belong to the signed-in person and to one workspace. Passwords are sealed and never returned. Mail itself is read
from the IMAP server on request. Setup and rules: [MAIL_SETUP.md](MAIL_SETUP.md).

| Endpoint | |
|---|---|
| `GET /v1/mail/accounts?workspace=` | `[{id, workspace, label, address, host, port, security, username, error, createdAt}]` |
| `POST /v1/mail/accounts` `{workspace, label?, address, host, port, security: ssl\|starttls, username?, password}` | logs in to check, then keeps it; `422 mail_auth_failed` / `502 mail_unavailable` carry the server's reason |
| `PATCH /v1/mail/accounts/{id}` | any of `workspace, label, host, port, security, username, password` (the password is kept unless a new one is sent) |
| `DELETE /v1/mail/accounts/{id}` · `POST .../test` | forget it · check the connection again |
| `GET .../folders` | `[{name, label, role, unseen, total}]` (`role`: inbox, drafts, sent, archive, junk, trash or empty) |
| `GET .../messages?folder=&before=&q=&limit=` | `{messages[], total, nextBefore, uidValidity}`, newest first; pass `nextBefore` as `before` for the next page; `q` searches the server |
| `GET .../messages/{uid}?folder=` | the message with `text`, cleaned `html`, `hasRemoteContent`, `attachments[]` |
| `PATCH .../messages/{uid}?folder=` `{seen?, flagged?}` | the only changes it can make |
| `GET .../messages/{uid}/attachments/{index}?folder=` | always a download |
| `GET .../events?folder=` | server-sent events: `state` (`connecting`, `live`, `down`, `auth`) and `changed` |
