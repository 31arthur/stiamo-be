"""Tool cards: every tool's description in the same parts, so a model can find what it needs without guessing.

A card says what the tool is for, when to use it, when not to, what each argument means, what comes back, what to do if it fails, and what it changes. The one-line
description a tool has in its pack becomes the first line (the purpose); the rest is added here, by tool name, and the arguments are read from the tool's own
schema. The prompt lists only the first line of each tool, so the whole card travels once, with the tool.
"""

from __future__ import annotations

from typing import Any

from app.domain.llm import ToolSpec


def card(purpose: str, *, use: list[str], avoid: list[str] | None = None, args: dict[str, str] | None = None, returns: str, fails: list[str] | None = None, effect: str) -> str:
    parts = [purpose, "USE IT WHEN: " + " ".join(use)]
    if avoid:
        parts.append("DO NOT USE IT WHEN: " + " ".join(avoid))
    if args:
        parts.append("ARGUMENTS: " + " ".join(f"{k} — {v}" for k, v in args.items()))
    parts.append("RETURNS: " + returns)
    if fails:
        parts.append("IF IT FAILS: " + " ".join(fails))
    parts.append("EFFECT: " + effect)
    return "\n".join(parts)


PROPOSES = "Proposes only. The owner sees exactly what would be saved and approves it (in the app, or by replying YES on their phone). Nothing is saved by this call, so never say it is saved: say you have proposed it."
READS = "Read only. Changes nothing."

#: name -> the parts of its card that its one-line description does not carry.
CARDS: dict[str, dict[str, Any]] = {
    "day_agenda": dict(
        use=["Planning or describing a whole day in detail: the hearings, meetings, calls, tasks, Google events, what is overdue and what is free.", "Before propose_schedule, so you use real ids and real free windows."],
        avoid=["To act on items or find a ref: use find_items (it speaks in refs and includes labels).", "For a quick look at free time only: use free_time."],
        returns="{date, hearings, meetings, calendarEvents, allDayEvents, followUps, tasks, overdueTasks, overdueFollowUps, freeWindows, callWindows}. When the day is after today, items still open from today are in stillOpenFromToday / stillOpenCallsFromToday (carried over, not overdue). callWindows are stretches inside an event where calls are fine but heavy work is not.",
        fails=["A day with nothing returns empty lists: say the day is clear."],
        effect=READS,
    ),
    "free_time": dict(
        use=["The owner asks how full a day is or when they could do something, or you need room for a call or a task."],
        avoid=["When you also need the items: find_items returns freeTime for a single day already."],
        returns="{date, workingDay (06:00–22:00), freeWindows: [{from, to, minutes}], callOnlyWindows}.",
        fails=["If freeWindows is empty the day is full: say so and offer another day."],
        effect=READS,
    ),
    "upcoming": dict(
        use=["The owner asks what is coming up, what is due this week, or whether anything is close that they should not be surprised by."],
        avoid=["For one day in detail: use find_items or day_agenda."],
        returns="{from, days, byDay: [{date, hearings, meetings, tasks, followUps, plans}]}. Only days with something are listed, in order.",
        fails=["An empty list means nothing is coming: say so."],
        effect=READS,
    ),
    "present_plan": dict(
        use=["Showing a whole day to the owner: a headline and sections whose items point at real hearings, meetings, tasks, calls and events by type and id (a task `task:ab12` is type task, id ab12). On Telegram this becomes a neat list with times and emoji, far easier to read than a paragraph.", "After you have read the day and, for a plan, after propose_schedule passed."],
        avoid=["For a single change or a short answer: just reply in a sentence.", "Do not repeat the card's items in your text afterwards."],
        args={"sections": "kinds are fixed (hearings, meetings, events), prepare, calls, work, push, attention, later. Put a mail that needs the owner in attention as a note item."},
        returns="{ok, shown} and the card is shown to the owner. After it, write at most two short sentences: what you left out and why.",
        fails=["If an id is not found the item is left out and the result says which: look it up again."],
        effect="Shows a card. Changes nothing.",
    ),
    "propose_schedule": dict(
        use=["Planning a day: putting open tasks and calls into free time. The tool checks the blocks against what is fixed and against each other."],
        avoid=["To move items to another day: use preview_move. Do not use it to move a single item: use change_item."],
        returns="{ok, blocks} when it fits, or {problems: [...]} in words you can act on (an overlap, a time outside 06:00–22:00, a task inside a calls-only window). Fix the blocks and call again.",
        fails=["Repeat until it passes or tell the owner what does not fit and what you left out."],
        effect="Keeps a proposed schedule the owner can approve. Nothing is booked until they approve it.",
    ),
    "search_cases": dict(
        use=["The owner names a case, a client, an opposing party or a court, or asks which cases are active, stayed or closed."],
        avoid=["When you already have the case id: use get_case."],
        returns="{count, cases: [{id, reference, title, status, client, opposingParty, court, nextHearing}]}.",
        fails=["No matches: say so and offer to search other words. Do not guess a case."],
        effect=READS,
    ),
    "get_case": dict(
        use=["Preparing for a hearing, or the owner asks about one case: its summary, hearings with dates and purposes, and the documents filed under it."],
        avoid=["To find a case: use search_cases first for its id."],
        returns="{id, reference, title, status, summary, hearings, documents, …}.",
        fails=["Not found: the id is wrong; search again."],
        effect=READS,
    ),
    "case_documents": dict(
        use=["The owner wants the papers for a case: what is filed under it and what letters in the Drive mention its number, court number, title or parties."],
        avoid=["For the case's facts or hearings: use get_case."],
        returns="{filed: [...], inDrive: [...]}, each a document with name, where it is and a link.",
        fails=["Nothing found: say there are no documents, never invent one."],
        effect=READS,
    ),
    "waiting_in_attention": dict(
        use=["The owner asks what mail needs them, or you are planning a day and want what is waiting."],
        avoid=["To act on those mails: they are also action items. Use find_items (kind mail) to get a ref, then change_item."],
        returns="{count, items: [{subject, sender, summary, priority, action, due, event}]} newest and most important first, up to 25.",
        fails=["An empty queue means nothing is waiting: say so."],
        effect=READS,
    ),
    "add_task": dict(
        use=["The owner asks for a new task: “add a task to draft the rejoinder, due Thursday”. Turn a day in words into YYYY-MM-DD with which_day. Look the case up only when the owner names one; otherwise leave the case out."],
        avoid=["To change a task that exists: use change_task (or change_item for done, cancel, move)."],
        returns="{proposed: true, proposalId} and a card the owner can approve.",
        fails=["{error} naming what is missing or invalid (a date, a priority): fix it or ask the owner."],
        effect=PROPOSES,
    ),
    "change_task": dict(
        use=["Changing what a task says: its title, due date, priority, length, notes, case or planner time. For done, cancelled or reopened you may also set status."],
        avoid=["For a quick done, cancel or move of one task the owner asked for: change_item applies it at once and can be undone."],
        returns="{proposed: true, proposalId}.",
        fails=["{error} when the id is unknown: find it with find_items or day_agenda."],
        effect=PROPOSES,
    ),
    "add_follow_up": dict(
        use=["The owner asks to remember a call, a message, an email or a visit: “remind me to call Ramesh tomorrow at 11”. Turn a day in words into YYYY-MM-DD with which_day."],
        avoid=["To change one that exists: use change_follow_up or change_item."],
        returns="{proposed: true, proposalId}.",
        fails=["{error} for a time without a day, or an unreadable date: fix or ask."],
        effect=PROPOSES,
    ),
    "change_follow_up": dict(
        use=["Changing who, how or when a call or message is, or its notes."],
        avoid=["For done, cancel or move of one: change_item applies it at once."],
        returns="{proposed: true, proposalId}.",
        fails=["{error} when the id is unknown: look it up first."],
        effect=PROPOSES,
    ),
    "add_hearing": dict(
        use=["The owner says a case has been listed: “the Alur matter is listed on 3 December at 11 before Court 4”."],
        avoid=["A hearing's date cannot be changed by a move request: courts set them. To record an adjournment, add the new listing."],
        returns="{proposed: true, proposalId}.",
        fails=["{error} for an unknown case: search_cases first and use its reference or id."],
        effect=PROPOSES,
    ),
    "add_case": dict(
        use=["The owner asks to open a new case. Ask for the organization and anything else essential that is missing instead of guessing."],
        avoid=["If the case exists: search_cases first so you do not duplicate it."],
        returns="{proposed: true, proposalId}. The case number is made when it is opened.",
        fails=["{error} for an organization that is not one of NUMW, TUMWU, PMLU, MRC, TYP, SA."],
        effect=PROPOSES,
    ),
    "add_action_plan": dict(
        use=["The owner wants a plan for an objective: an ordered list of steps, each a task or a call, for one or more cases (for example for a hearing)."],
        avoid=["For one task: use add_task."],
        returns="{proposed: true, proposalId} with each step listed.",
        fails=["{error} naming a step with no title or an unknown case."],
        effect=PROPOSES,
    ),
    "stories_to_push": dict(
        use=["Planning the evening: to see which story most deserves the owner's time now, so the plan gets a “push” section."],
        avoid=["To read a story: use read_story."],
        returns="{stories: [{id, title, why, openSteps: [...]}]}, best first. May be empty.",
        fails=["Empty means nothing needs pushing: leave the push section out."],
        effect=READS,
    ),
    "change_story": dict(
        use=["The owner changes a story's ending, chance, stakes, pace or state, or what it waits on."],
        avoid=["To record what happened: use write_chapter."],
        returns="{proposed: true, proposalId}.",
        fails=["{error} for an unknown story or a value out of range (chance 0–100, stakes 1–5)."],
        effect=PROPOSES,
    ),
    "story_cast": dict(
        use=["The owner asks who is in a story and what part each person plays."],
        avoid=["For one person: use read_character."],
        returns="{story, cast: [{name, part, aliases, numbers}]}.",
        fails=["Unknown story: list_stories first."],
        effect=READS,
    ),
    "read_character": dict(
        use=["The owner asks about one person in their cast: their numbers, organisation, the part they play in each story and their latest scenes."],
        avoid=["For everyone in a story: use story_cast."],
        returns="{name, numbers, organisation, parts, scenes}.",
        fails=["Not found: try their name as a story calls them, or ask."],
        effect=READS,
    ),
    "add_to_cast": dict(
        use=["The owner introduces someone to a story or asks to remember a person. Give a phone number, or from_contact to take their numbers from a Google contact."],
        avoid=["If the person is already in the cast: the tool says so and adds only their part."],
        returns="{proposed: true, proposalId}.",
        fails=["{error} when there is neither a phone number nor a contact: ask for one."],
        effect=PROPOSES,
    ),
    "list_stories": dict(
        use=["The owner asks what stories they have, or you need a story's id before reading or changing it."],
        avoid=["To read one story's chapters: use read_story."],
        returns="{stories: [{id, title, state, ending, stakes, chance}]}. Ended stories only with include_ended.",
        fails=["Empty: they have none yet."],
        effect=READS,
    ),
    "read_story": dict(
        use=["The owner asks where a story stands: its arc of checkpoints, its facts and its latest chapters."],
        avoid=["To record an update: use write_chapter."],
        returns="{id, title, ending, checkpoints, facts, chapters}.",
        fails=["Unknown story: list_stories."],
        effect=READS,
    ),
    "write_chapter": dict(
        use=["The owner tells you what happened in one of their stories (an update on a case, a project, a loan). Pass their words exactly as written in text."],
        avoid=["Never rewrite, shorten or correct their words. Not for a question about a story."],
        returns="{proposed: true, proposalId}: the chapter and the tasks it leaves are proposed together.",
        fails=["{error} when no story fits: ask which story, or offer add_story."],
        effect=PROPOSES,
    ),
    "add_story": dict(
        use=["The owner wants to start a new story: its title, the ending they are writing towards, and its checkpoints in order."],
        avoid=["If it exists: list_stories first."],
        returns="{proposed: true, proposalId}.",
        fails=["{error} for a missing title or ending: ask."],
        effect=PROPOSES,
    ),
}


def _args_from(params: dict[str, Any]) -> dict[str, str]:
    props = (params or {}).get("properties") or {}
    return {k: str(v.get("description")).strip() for k, v in props.items() if isinstance(v, dict) and v.get("description")}


def enrich(spec: ToolSpec) -> ToolSpec:
    """The same tool with its full card as the description. A tool that already has one (the actions pack writes its own), or has none here, is left as it is."""
    if "USE IT WHEN:" in spec.description or spec.name not in CARDS:
        return spec
    c = CARDS[spec.name]
    purpose = spec.description.strip()
    args = _args_from(spec.parameters)
    return ToolSpec(spec.name, card(purpose, use=c["use"], avoid=c.get("avoid"), args=args or None, returns=c["returns"], fails=c.get("fails"), effect=c["effect"]), spec.parameters)
