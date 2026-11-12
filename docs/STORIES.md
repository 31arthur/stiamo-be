# Stories

Every running thing in your life and work (an S&B case, an FSA project, a home loan) can be a **story** you write in **chapters**. Each story has its own **checkpoints** (its plot points, never a fixed list of stages), the **ending** you're writing towards, and the **work** each chapter leaves behind as ordinary tasks and calls. The full design, including the parts not built yet, is on the plan page: <https://claude.ai/artifact/Fy2G3B4RQnVc633Vkwxgrw>.

All four phases are built: **1** stories, checkpoints, chapters and margin notes; **2** the cast, people learned once and used in every story; **3** the editor that comes looking when a story goes quiet, the planner's "push this", and the chance after each chapter; **4** gauging a person: lenses, the interviewer, and "how will they take it?".

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

## The cast

A person is learned once and used in every story they're part of, in any workspace.

| Record | What | Workspace |
| --- | --- | --- |
| `Character` | Who they are: name, numbers, emails, organisation, title, the Google contact they are | Shared. Seen in their first workspace and every workspace where they have a part |
| `Part` | The part they play in one story ("agent", "the other side's lawyer"), what the story's chapters call them (`knownAs`), their stance (for us, neutral, wary, against) and sway (decides, shapes, informs) | The story's |
| `Scene` | What they did, from a chapter (with its quote) or as you put it | The story's |

- **Found before made.** `POST /v1/characters/join` looks for the person by id, then by **phone number**, then by Google contact, and only then makes a new character. Numbers are normalised (`098450 12345` = `+91 98450-12345` = `+919845012345`), and two characters can't share one. What's learned about someone already known is added, never overwritten.
- **From a chapter.** The people a chapter names are matched by code: someone in the story's cast (by name, or what the story calls them: "the agent"), else the one character anywhere with exactly that name, else someone new. Each gets a part in the story if they lack one, and a scene. The card says which: "Cast: The agent (Ravi Kumar)" or "New in your cast: the bank manager".
- **A call carries the number.** A task or call a chapter leaves for someone in the cast points at them (`characterId`), and shows "Ravi Kumar · +919845012345", so the call button works on the phone.
- **On the phone:** "Add Priya from my contacts as the client in the website story" (`add_to_cast` with `from_contact`), "Who is in the Gulbarga story?" (`story_cast`), "Tell me about Ravi" (`read_character`).
- **Two records, one person:** `POST /v1/characters/{id}/merge {into}` moves the parts, scenes, tasks and calls across and keeps every number.
- **Workspaces:** a workspace's assistant sees who someone is if they're in one of its stories, but only the parts and scenes of its own stories. `tests/test_cast.py` puts a canary in a personal scene and checks the S&B bot never reads it.

## Gauging a person

The model never decides what someone is like. Small jobs label **one scene on one lens** against a written codebook (`app/domain/lenses.py`), quoting the words they relied on; your answers to short questions are labels too. Each label is a piece of **evidence** (practice service, in its story's workspace). The **profile** is plain arithmetic over the evidence (`practice/app/domain/insight.py`), kept on the character as labels and counts only, so it travels with them while the quotes stay home.

| Lens | Built on | Labels |
| --- | --- | --- |
| track record | your call log, counted, never labelled | answer rate by channel, best hours, promised dates kept |
| talks | communication style | straight · diplomatic · evasive |
| decides | stakeholder map, buying-centre roles | decider · influencer · gatekeeper · executor · champion · blocker · bystander |
| wants | interests, not positions | money · speed · certainty · face · relationship · control · fairness · avoiding blame |
| under pressure | conflict modes, stress responses | confronts · bargains · solves · gives in · goes quiet · agrees, doesn't deliver |
| trust | the trust equation | ally · neutral · wary · against |
| moved by | principles of influence | authority · others did it · a favour returned · their own word · rapport · a deadline · facts · unmoved |
| yours | "compare two" | two ends in your own words |

- **Weights:** a clear scene 1.0, an implied one 0.5, your answer about a specific moment 0.9, your general view 0.6, a check 1.0, a prediction 0 (until reality arrives, which becomes evidence of its own). Evidence fades with a half-life of six months.
- **Confidence:** high with weight 3+ and 70% on one label; medium with 1.5+ and 55%; else low. A **conflict** is two labels each holding 30%+ of 2.5+ weight: shown, never averaged away, and asked about.
- **Each evening** (with the editor), new scenes are read: `lens_router` says which lenses a scene speaks to, then one `lens_<lens>` coder per lens labels it or says `not_shown`. A scene is marked read only when the router answered. `POST /v1/agent/cast/code` does it now.
- **The interviewer** (`app/domain/interviewer.py`, plain code) asks up to two questions a day per workspace, one per person, on top of the editor's one: priority = unsure × matters (stakes × sway) × now (×2 when you were just in touch) × lens weight. It picks the way of asking: settle a prediction · debrief after contact · check a conflict · character sheet for someone new · why, and why again · the moment that surprised you · what would they do (a guess, scored later) · compare two (weekly) · a quick tap, about a specific scene when there is one. Taps become evidence; free answers ("Ravi: …") become scenes through the story editor. **Skip** rests a person for a week.
- **"How will they take it?"** (`how_will_they_take_it`): gathers the profile and the five most relevant pieces of evidence (code), asks `reaction` three times (agreement is the confidence; the decision model's probabilities when it's on), picks the approach from a **playbook** in code (reaction × what they want × what moves them, and the channel they answer), and has `wording` draft two or three sentences (no numbers that weren't in the news). The prediction is kept; when you say how it went (`how_they_took_it`, or the debrief tap) it's scored, and the profile keeps "reads: 7 of 9 right" for you and for the model.
- **People from a news item or a mail:** `POST /v1/agent/cast/from-text {text, story?}` (or `people_in_text` in a chat) proposes everyone named, with only the numbers and addresses that are really in the text.
- **API:** `/v1/evidence`, `/v1/lenses`, `GET /v1/characters/{id}/profile`, `POST /v1/evidence/{id}/settle {verdict}`.

## The editor: when a story goes quiet

Each story has a pace: brisk (7 days), steady (14) or slow (30). With the evening plan, the editor looks at every story against its own pace since its last chapter, and climbs a ladder, one step per pace of quiet:

| Quiet for | It sends | A tap does |
| --- | --- | --- |
| 1× its pace | "Previously…" (the last chapter in a line, where it is, what it waits on) and one question: a hunch whose moment has come, else an open step ("Did this happen?"), else "What's happened since?" | settles the hunch · marks the step done or drops it · asks for the chapter |
| 2× | Three ways the next chapter could start, each a real move | **books its step** at once (a call to someone in the cast carries their number) |
| 3× | "Imagine it's a month from now and the ending didn't happen. What went wrong?" | they didn't deliver → books a chase · I didn't push → books the next push · the answer was no → plan B as a twist |
| 4× | "Is this still the ending you want?" | keep it · rewrite it · rest the story · The End |

- **Kept polite.** At most one ladder message a day per workspace, on that workspace's planner bot (or your general bot). Each step is sent once; a new chapter starts the ladder again. **Not now** leaves a story for its pace. A story **waiting** on someone until a date has its clock paused until then. A resting or ended story never asks.
- **The writers' room.** Once a week (Sunday by default), one message per workspace: what moved, what went quiet, hunches waiting, and the story that deserves your next hour, with a button for ways to start it.
- **Rules in code, words from you.** How quiet a story is, which step is due, the question asked and the push score are plain code (`app/domain/editor.py`). "Previously…" is built from your own last chapter. The only AI job here is `next_openings`, and if it fails the editor uses its own three openings (ask the last person in the story, list what the next checkpoint needs, set a day to look again).
- **Settings:** `GET /v1/agent/editor` (each story's pace, quiet days, step and push score), `PUT /v1/agent/editor/settings {enabled, roomDay}`, `POST /v1/agent/editor/run` to run it now. It runs with the evening plan, so the evening plan must be on.

## Which story to push, and the chance

- **Push score** = chance × stakes × ready (0.2 while waiting) × soon (1.5 with a checkpoint date or step due within 7 days) × quiet (1.3 once behind its pace). The evening plan gets a **Push this** section with the top story, why, and its next step. Ask any time: "Which story should I push?"
- **The chance after a chapter.** When a story has a chance and a chapter moves it, the `suggest_chance` job proposes the new figure on the chapter's card ("60% → 52%, another document. Say if you see it differently."). It never moves it more than 20 points. The timeline keeps the trend (`chanceTrend`).
- **Changing a story** (its ending, chance, stakes, pace, rest it, end it, what it waits on) is a card too: "Home loan: the new ending is disbursed by April".

## Writing a chapter

- **On Telegram or in a chat:** tell the assistant what happened, e.g. "Home loan: the agent called, the bank wants Form 16 for two years. I'll write to HR on Monday." The **Story editor** agent (or the daily planner, which has the same tools) passes your words untouched to `write_chapter`. You get a card with the margin notes; **YES** writes it all. A voice note works the same way.
- **In the app:** `POST /v1/agent/stories/chapters/draft` with `{prose, story?, title?, date?}` gives the same card (approve it at `/v1/agent/proposals/{id}/apply`). If the story isn't named and it could be several, you get `{ask: [...]}`; if none fits, `{none: true}`.
- **Directly:** `POST /v1/stories/{id}/chapters/write` takes the prose and everything it moves, adds and leaves, in one request.

Your words are never rewritten. The AI writes margin notes, and only notes it can pin to your words: each one carries the sentence it came from, and code drops any note whose quote isn't really in the chapter.

## The AI jobs (built to outlast the model)

Small jobs, each with a written contract: in `app/domain/story_jobs.py`, `find_story` and `margin_notes` below, `next_openings` (the editor's three ways to start) and `suggest_chance` (checked to 0–100, at most 20 points from the last figure); in `app/domain/insight_jobs.py`, `lens_router`, the six `lens_<lens>` coders, `reaction`, `wording`, `people_in_text` and `construct`. `app/domain/jobs.py` lists them all.

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

The test sets are `services/agent/evals/<job>.jsonl`, one per job (the six coders share `lens_coding.jsonl`, which holds the loan agent's scenes among others: scoring 85% there is how you know his profile rebuilds on that model): chapters and messages with what an editor who agrees with you would note. Score any model on them:

```bash
cd services/agent
python -m app.scripts.eval_jobs --provider openrouter --model <model id> --key "$OPENROUTER_API_KEY" -v
```

On the server: `docker compose -f deploy/docker-compose.prod.yml exec agent python -m app.scripts.eval_jobs --provider … --model … --key …`

A job passes at 85% agreement; the script exits 1 otherwise. Grow the sets from real chapters: when you'd have noted a chapter differently, add it with the notes you'd want.

## Workspaces

A story belongs to one workspace. A workspace's assistant sees only its own stories and their chapters, and can't write a chapter into another workspace's story (`ScopedData.chapters` / `write_chapter`).

## Deploying this version

The new practice paths (`/v1/stories`, `/v1/chapters`, `/v1/arc-templates`, `/v1/characters`, `/v1/parts`, `/v1/scenes`, `/v1/evidence`, `/v1/lenses`) must be in the practice router of the VPS's `docker-compose.yml` (Traefik only forwards the paths it lists; anything else gets a 404 with no CORS headers, which the browser reports as a CORS error). Copy the new `deploy/docker-compose.prod.yml`, or add them to that rule by hand. Then:

`./deploy.sh` runs migrations `practice 0007` (the `stories`, `chapters` and `arc_templates` tables) and `practice 0008` (the cast: `characters`, `parts`, `scenes`, and `characterId` on tasks and follow-ups), `agent 0023` (the editor's memory), and `practice 0009` (evidence, lenses, the profile on each character, which scenes are read). Nothing else to set up: the jobs use the model you already chose for your agents until you give them their own.
