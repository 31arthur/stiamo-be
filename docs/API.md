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

## What stays in the frontend

Pure presentation and device logic is deliberately not duplicated here: the day planner's reflow/packing
(`packDay`), month grids, activity phrasing (`describe`), labels and formatting, block-editor behaviour and the
offline mode. These need no database and no server round trip.

## Time zones

The server's clock is UTC. Anything that depends on "today" or "now" in the user's day accepts it from the client
(`today=`, `at: {date, minute}`); otherwise the UTC date is used.
