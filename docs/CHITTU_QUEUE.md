# Chittu's worklist: one action item at a time

The owner's rule: whatever they ask, Chittu gives **one action item at a time, numbered (3/10)**, never a list. The list is a **queue that keeps its meaning over time**
until the owner makes a new request. The model makes the list **once**, as JSON; **the code walks it**. No model call per item.

## Who does what

| | Model (Chittu) | Code |
|---|---|---|
| Understand the request | yes | |
| Choose the items, their order, why each is there | yes (`set_queue` JSON) or no (`walk_through` filters: the code picks) | |
| Check every ref is a real, open item | | yes (`parse_spec`) |
| Send the opening line and item 1/N with buttons | | yes |
| Move on after Done / Tomorrow / Skip / Stop / Continue / Undo | | yes (buttons) |
| Move on after the words *next, done, tomorrow, stop, continue, how many left, undo* | | yes (`QueueService.command`, before the model is asked) |
| Drop what was settled elsewhere or deleted | | yes (`_revalidate`, every time) |
| Carry the list over a night and a restart | | yes (database) |
| Take a new mail that needs the owner into the list | | yes (`on_created` → `add`) |
| Reply after making the list | one short line | |

## The JSON a model gives

```json
{ "title": "Your calls", "mode": "replace",
  "items": [ {"ref": "followUp:f1", "note": "promised for today"}, {"ref": "task:t1"} ] }
```

`mode`: `replace` (default: this list takes over), `append` (at the end of the list in force), `next` (right after the item in front of the owner). Refs come from `find_items`.
A ref that does not exist, is already settled, or is repeated is dropped and named back to the model. A list holds at most 60 entries; a note at most 140 characters.

## Lifecycle

```
request ──► waiting ──Start──► active ◄──Continue── paused
(morning)                         │  ▲ Stop / Later / night   ▲
                                  │  └────────────────────────┘
          new request ──► replaced        all settled ──► done
```

* **waiting**: the good morning was sent; nothing is brought until the owner says Start.
* **active**: one entry is *shown*; settling it brings the next.
* **paused**: Stop, Later or the night. The place is kept exactly. Nothing expires.
* **done**: every entry is settled (done, moved, skipped, snoozed) or gone.
* **replaced**: a new request took over; the old list's place is kept in the database (`shown.leftWhenClosed`).

Entry states: `pending → shown → done | moved | skipped | snoozed | gone`.

## Scenarios and what happens

| Scenario | What happens |
|---|---|
| "What's on today?" | `walk_through` (filters) → code picks and orders by the usual rules → opening line, then `1/N` |
| "Who should I call first?" | model orders the calls, `set_queue` with notes → `1/N` with the note under it |
| "Put the RTI mail next" | `set_queue` mode `next` → "Added 1 to your list", no second item (one is already in front) |
| Owner presses Done | item completed (history, undo kept), next item `2/N` in the same message |
| Owner types "done" / "next" / "tomorrow" | same, handled by the code in the channel layer, no model |
| "How many left?" | "7 left of 10 on Your calls: 2 done, 1 moved, 0 skipped" |
| "Stop" / Later | list paused, place kept, "Say continue whenever you like" |
| "Continue" a minute or a week later | the item that was in front is shown again, not the next one |
| A button from an old list is pressed | "That was from an earlier list" and nothing changes |
| A new request while a list is in force | old list aside ("I've put the earlier list aside: 3 were left"), new list starts |
| Item finished in the app meanwhile | entry becomes `gone`; the numbering shrinks; it is never shown |
| Owner presses Undo / says "undo" | the last item's change set is undone, the entry is in front again |
| Night (22:00) | good night summary; list paused, not closed |
| Morning (06:00) | list carried over and checked, today's new items added once each, "N still on the list from before" |
| A mail judged *now* arrives | joins the end of the list in force (note "new"); the alert goes out on its own |
| After 10 items | "That's 10 so far. N more to go. Keep going?" |
| A bare word with no list in force, or "done" while paused | not for the list: Chittu answers |
| "Done with the call to Ramesh" | more than a bare word: Chittu (one item, by name) |
| Service restarts | nothing lost: the list is in `action_queues` |

## Infrastructure

* `domain/worklist.py`: pure model, JSON validation, merge modes, numbering, the words.
* `application/queue.py`: `QueueService` (`set`, `set_from_json`, `begin`, `morning`, `remind`, `night`, `press`, `command`, `add`, `view`).
* `infrastructure/db/actions.py` and tables `action_queues` (entries, cursor, status, title, source, shown) and `action_events` (every step is in the item's history).
* Telegram: callbacks `aq:<action>:<tag>:<index>` (under 64 bytes); `ChannelService` hands plain words to `QueueService.command` before asking a model.
* Tools (Telegram only): `walk_through` (filters), `set_queue` (JSON).
* `GET /v1/agent/queue`: the list in force, for the app.
