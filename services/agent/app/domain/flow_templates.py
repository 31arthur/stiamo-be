"""Starting points for a new agent: workflows that already work, built from this app's own data and nodes. Each is a complete workflow a person can
create an agent from and then change node by node."""

from __future__ import annotations

from typing import Any


def n(i: str, t: str, name: str, x: int, y: int = 0, **parameters: Any) -> dict[str, Any]:
    return {"id": i, "type": t, "name": name, "position": [x, y], "parameters": parameters}


def link(*pairs: tuple[str, int, str]) -> dict[str, Any]:
    """connections from (source, output, target) triples, in output order."""
    out: dict[str, list[list[dict[str, Any]]]] = {}
    for src, output, target in pairs:
        lanes = out.setdefault(src, [])
        while len(lanes) <= output:
            lanes.append([])
        lanes[output].append({"node": target, "index": 0})
    return {k: {"main": v} for k, v in out.items()}


TEMPLATES: list[dict[str, Any]] = [
    {
        "id": "assistant",
        "name": "Chat assistant",
        "description": "A message comes in, the AI agent answers with the tools you give it. The simplest agent; start here and add nodes.",
        "packs": ["agenda", "changes"],
        "system": "You are a helpful assistant for {{user}} inside Stiamo. Answer briefly and use your tools to look things up; never guess a name, a date or a time.",
        "suggestions": ["What is on tomorrow?"],
        "flow": {
            "nodes": [n("trigger", "trigger.chat", "When a message comes in", 0), n("agent", "ai.agent", "AI agent", 280, prompt="{{ $json.asked }}"), n("answer", "respond", "Answer", 560, text="{{ $json.text }}")],
            "connections": link(("trigger", 0, "agent"), ("agent", 0, "answer")),
        },
    },
    {
        "id": "call-digest",
        "name": "Daily calls digest",
        "description": "Each morning: gets the follow-ups due or overdue, and if there are any, has the AI write a short list and sends it to your phone.",
        "packs": ["agenda"],
        "system": "You write short, friendly messages for {{user}}. Use plain words and a few emoji. Never invent anything that is not in what you are given.",
        "suggestions": [],
        "flow": {
            "nodes": [
                n("trigger", "trigger.schedule", "Every morning", 0),
                n("calls", "data.follow_ups", "Calls due or overdue", 260, overdue=True, status="open", limit=30),
                n("any", "core.if", "Any calls?", 520, conditions=[{"left": "{{ $input.length }}", "op": "gt", "right": "0"}]),
                n("list", "core.aggregate", "Put them together", 780, field="title"),
                n("write", "ai.agent", "Write the digest", 1040, prompt="Write a short message listing these calls to make, most important first: {{ join($json.title, '; ') }}", tools=[], history=False),
                n("send", "msg.send", "Send to my phone", 1300, text="📞 {{ $json.text }}"),
            ],
            "connections": link(("trigger", 0, "calls"), ("calls", 0, "any"), ("any", 0, "list"), ("list", 0, "write"), ("write", 0, "send")),
        },
    },
    {
        "id": "mail-digest",
        "name": "Mail digest",
        "description": "Reads the last day of this workspace's mail and has the AI pick out what needs you, then sends a short summary to your phone.",
        "packs": ["agenda"],
        "system": "You read email subjects and snippets for {{user}} and say what needs a reply or action, briefly. Never invent anything.",
        "suggestions": [],
        "flow": {
            "nodes": [
                n("trigger", "trigger.schedule", "Every evening", 0),
                n("mail", "data.mail", "Read recent mail", 260, days=1, limit=25),
                n("any", "core.if", "Any mail?", 520, conditions=[{"left": "{{ $input.length }}", "op": "gt", "right": "0"}]),
                n("list", "core.aggregate", "Put it together", 780, field="subject"),
                n("write", "ai.agent", "Pick out what needs me", 1040, prompt="These are today's email subjects. Say which need a reply or action and why, in a few lines: {{ join($json.subject, ' | ') }}", tools=[], history=False),
                n("send", "msg.send", "Send to my phone", 1300, text="✉️ {{ $json.text }}"),
            ],
            "connections": link(("trigger", 0, "mail"), ("mail", 0, "any"), ("any", 0, "list"), ("list", 0, "write"), ("write", 0, "send")),
        },
    },
    {
        "id": "contact-lookup",
        "name": "Contact lookup",
        "description": "Ask for a person by name: finds them in this workspace's Google contacts and answers with their number. No AI needed.",
        "packs": ["agenda"],
        "system": "You help {{user}} find people.",
        "suggestions": ["Find Hemla"],
        "flow": {
            "nodes": [
                n("trigger", "trigger.chat", "When a message comes in", 0),
                n("find", "data.contacts", "Find the contact", 260, query="{{ $json.message }}", limit=5),
                n("many", "core.aggregate", "Put them together", 520, field="name", runWhenEmpty=True),
                n("has", "core.if", "Found anyone?", 780, conditions=[{"left": "{{ $json.count }}", "op": "gt", "right": "0"}]),
                n("yes", "respond", "Answer", 1040, text="Found: {{ join($json.name, ', ') }}"),
                n("no", "respond", "Nobody found", 1040, 120, text="I couldn't find anyone with that name in your contacts."),
            ],
            "connections": link(("trigger", 0, "find"), ("find", 0, "many"), ("many", 0, "has"), ("has", 0, "yes"), ("has", 1, "no")),
        },
    },
    {
        "id": "overdue-tasks",
        "name": "Overdue tasks nudge",
        "description": "Gets overdue tasks, keeps the five oldest, and sends them to your phone. No AI: a plain list, which is faster and free.",
        "packs": ["agenda"],
        "system": "You help {{user}} with tasks.",
        "suggestions": [],
        "flow": {
            "nodes": [
                n("trigger", "trigger.schedule", "Every morning", 0),
                n("tasks", "data.tasks", "Overdue tasks", 260, overdue=True, status="open", limit=50),
                n("sort", "core.sort", "Oldest first", 520, field="dueDate", order="asc"),
                n("five", "core.limit", "Keep five", 780, max=5),
                n("list", "core.aggregate", "Put them together", 1040, field="title"),
                n("send", "msg.send", "Send to my phone", 1300, text="⏰ Overdue: {{ join($json.title, ' · ') }}"),
            ],
            "connections": link(("trigger", 0, "tasks"), ("tasks", 0, "sort"), ("sort", 0, "five"), ("five", 0, "list"), ("list", 0, "send")),
        },
    },
    {
        "id": "blank",
        "name": "Blank workflow",
        "description": "Just a trigger and an answer that repeats what was said. Build everything else yourself, node by node.",
        "packs": ["agenda"],
        "system": None,
        "suggestions": [],
        "flow": {
            "nodes": [n("trigger", "trigger.chat", "When a message comes in", 0), n("answer", "respond", "Answer", 300, text="You said: {{ $json.message }}")],
            "connections": link(("trigger", 0, "answer")),
        },
    },
    {
        "id": "mail-sorter",
        "name": "Mail sorter",
        "description": "Every morning it reads new mail, keeps the people you trust out of it, and sorts the rest: junk is proposed for Trash (you approve), what matters goes to your Attention queue with a summary, and anything it is unsure about comes to you too.",
        "packs": ["agenda"],
        "system": None,
        "suggestions": [],
        "flow": {
            "nodes": [
                n("morning", "trigger.schedule", "Every morning", 0, 0, time="07:30", timezone="Asia/Kolkata", days=[0, 1, 2, 3, 4, 5, 6]),
                n("byhand", "trigger.manual", "Run it by hand", 0, 160),
                n("mail", "data.mail", "New mail", 260, 80, days=2, limit=50),
                n("new", "core.only_new", "Only what is new", 500, 80),
                n("safe", "core.safe_senders", "Keep people I trust", 740, 80, correspondents=True, domains=[], addresses=[], phrases=[]),
                n("sort", "ai.classify", "Sort the mail", 980, 80, prompt="This is my work inbox. Sort each email: is it junk, does it need me, or is it fine to leave?", threshold=0.7, decider="model", batchSize=5, labels=[
                    {"name": "junk", "description": "Unwanted sales mail, newsletters, mass mailings and promotions that nobody asked for."},
                    {"name": "important", "description": "Needs me: a person waiting for an answer, a deadline, a hearing or court matter, a client, an invoice or money."},
                    {"name": "fine", "description": "Everything else that can stay in the inbox without a look."},
                ]),
                n("trash", "msg.trash", "Move junk to Trash", 1260, 0, folder="INBOX", auto=False, tell=True),
                n("read", "ai.read", "Read what matters", 1260, 200, prompt="Mail that needs my attention: what is it, how urgent, what should I do.", route="event"),
                n("attention", "msg.attention", "Add to Attention", 1540, 200),
            ],
            "connections": link(("morning", 0, "mail"), ("byhand", 0, "mail"), ("mail", 0, "new"), ("new", 0, "safe"), ("safe", 0, "sort"), ("sort", 0, "trash"), ("sort", 1, "read"), ("sort", 3, "read"), ("read", 0, "attention")),
        },
    },
]


def template(template_id: str) -> dict[str, Any] | None:
    return next((t for t in TEMPLATES if t["id"] == template_id), None)
