# Stories

Every running thing in your life and work (an S&B case, an FSA project, a home loan) can be a **story** you write in **chapters**. Each story has its own **checkpoints** (its plot points, never a fixed list of stages), the **ending** you're writing towards, and the **work** each chapter leaves behind as ordinary tasks and calls. The full design, including the parts not built yet, is on the plan page: <https://claude.ai/artifact/Fy2G3B4RQnVc633Vkwxgrw>.

This is **phase 1**: stories, checkpoints, chapters and margin notes. The cast (characters learned once and reused everywhere), the editor that comes looking when a story goes quiet, and the planner's "push this" are later phases.

```
 you write ──► find the story ──► margin notes ──► code checks ──► one card ──► YES ──► chapter + twists + tasks + calls + hunches
 (app, a line      (code first,      (a small AI job,   (every note quotes        (Telegram         (one request to the practice
  on Telegram,      then a small      on its own model)  your words, ids exist,    or the app)        service, by ids, so a retry
  a voice note)     AI job)                              dates are real)                              never doubles anything)
```

## What a story holds

| Part | What | Where |
| --- | --- | --- |
| Title, workspace | "Home loan" · personal | `Story` |
| Ending | The outcome you're writing towards | `Story.ending` |
| Checkpoints | Plot points in order: `ahead`, `reached` or `skipped`. "Where you are" is the first one still ahead, worked out, never stored. A **twist** adds one, just before where you are unless placed elsewhere | `Story.checkpoints` |
| Stakes, chance | 1–5 (and money in words if you like) · 0–100%, which each chapter can move | `Story.stakes`, `stakesAmount`, `chance` |
| Pacing | brisk (7 days), steady (14), slow (30): how quiet is too quiet, for the editor later | `Story.pacing` |
| Waiting on | Who or what, and until when | `Story.waitingOn` |
| Story bible | Free facts: amounts, file numbers, names | `Story.bible` |
| Links | The cases, projects and clients it covers | `Story.links` |
| Chapters | Your prose exactly as written, its beat (progress, setback, twist, lull, breakthrough, note, epilogue), the margin notes read from it (each with its quote), hunches, a cliffhanger, the people in it, the chance it left | `Chapter` |
| Arc templates | Checkpoint lists saved per workspace ("Loan", "Court order") | `ArcTemplate` |

A recorded hearing of a linked case shows on the story's timeline as a chapter by itself, so nothing is written twice. The work a chapter leaves is ordinary tasks (`related` → the story) and follow-ups (`subject` → the story), in the story's workspace: the planner, reminders and Google Calendar handle them as usual. Deleting a story deletes its chapters; its tasks and calls stay.

## Writing a chapter

- **On Telegram or in a chat:** tell the assistant what happened, e.g. "Home loan: the agent called, the bank wants Form 16 for two years. I'll write to HR on Monday." The **Story editor** agent (or the daily planner, which has the same tools) passes your words untouched to `write_chapter`. You get a card with the margin notes; **YES** writes it all. A voice note works the same way.
- **In the app:** `POST /v1/agent/stories/chapters/draft` with `{prose, story?, title?, date?}` gives the same card (approve it at `/v1/agent/proposals/{id}/apply`). If the story isn't named and it could be several, you get `{ask: [...]}`; if none fits, `{none: true}`.
- **Directly:** `POST /v1/stories/{id}/chapters/write` takes the prose and everything it moves, adds and leaves, in one request.

Your words are never rewritten. The AI writes margin notes, and only notes it can pin to your words: each one carries the sentence it came from, and code drops any note whose quote isn't really in the chapter.

## The AI jobs (built to outlast the model)

Two small jobs, each with a written contract in `services/agent/app/domain/story_jobs.py`:

| Job | Sees | Returns | Checked by code |
| --- | --- | --- | --- |
| `find_story` | your message, your stories (ids, titles, checkpoints) | one story id, or the ones it could be | ids must be in the list. A story you name is found by code before any model is asked |
| `margin_notes` | the chapter, the arc (with ids), the people already in the story, a day-by-day calendar | beat, checkpoints reached/skipped, twists, steps, people, hunches, cliffhanger, waiting on, a title | every note's quote is in the chapter; ids exist; dates parse; twists aren't already in the arc; lists are capped |

How they stay good on cheap models:

- **One narrow decision per call**, with plain-word definitions and worked examples sent as earlier turns (small models follow examples far better than rules). A test makes sure every example passes its own job's checks.
- **Quote first.** A note without a real quote is dropped. If a reply has notes but none of them quote the chapter, the model is asked once more and told why.
- **Look up, don't count.** The prompt carries a day table ("Mon 16 Nov = 2026-11-16"), so "on Monday" becomes a date without the model counting days.
- **Fail into your hands.** An unusable reply gets one retry with the reason; after that the chapter is still offered, as written, with no notes. A weak model gives fewer notes, never wrong records.
- **Each job has its own model.** `GET /v1/agent/jobs` lists them; `PUT /v1/agent/jobs/{name}/model` `{provider, model, thinking}` puts one on its own (cheaper) model, and `{}` puts it back on the model chosen last.

### Testing a model before switching to it

The test sets are `services/agent/evals/margin_notes.jsonl` and `find_story.jsonl`: chapters and messages with what an editor who agrees with you would note. Score any model on them:

```bash
cd services/agent
python -m app.scripts.eval_jobs --provider openrouter --model <model id> --key "$OPENROUTER_API_KEY" -v
```

On the server: `docker compose -f deploy/docker-compose.prod.yml exec agent python -m app.scripts.eval_jobs --provider … --model … --key …`

A job passes at 85% agreement; the script exits 1 otherwise. Grow the sets from real chapters: when you'd have noted a chapter differently, add it with the notes you'd want.

## Workspaces

A story belongs to one workspace. A workspace's assistant sees only its own stories and their chapters, and can't write a chapter into another workspace's story (`ScopedData.chapters` / `write_chapter`).

## Deploying this version

`./deploy.sh` runs migration `practice 0007` (the `stories`, `chapters` and `arc_templates` tables). Nothing else to set up: the jobs use the model you already chose for your agents until you give them their own.
