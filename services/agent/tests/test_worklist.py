"""The worklist: a list is made once by a request (the morning, a question, or JSON a model gave), and then the code walks it, one item at a time, numbered, with no model
call per item. It keeps its place until a new request replaces it, survives pauses, nights and restarts, and never shows what is no longer real."""

import json

from app.domain.actionitems import from_follow_up, from_task
from app.domain.llm import ChatResult, ToolCall
from app.domain.worklist import classify_words, counts, entry, merge, next_index, parse_spec, position

from .conftest import headers
from .test_actions import PLANNER, T, TODAY, TOMORROW, fill
from .test_agent_api import choose, connect
from .test_alerts import press
from .test_channels import send, telegram


# --- the pure parts ---------------------------------------------------------------------------------------------------------------


def known():
    items = [from_task({"id": "a", "title": "A", "dueDate": "2026-11-20", "outcome": {"status": "open"}}), from_task({"id": "b", "title": "B", "dueDate": "2026-11-20", "outcome": {"status": "done"}}), from_follow_up({"id": "c", "title": "C", "due": {"date": "2026-11-20"}, "outcome": {"status": "open"}})]
    return {i.ref: i for i in items}


def test_a_list_from_a_model_keeps_its_order_and_notes_and_only_what_is_real():
    spec = parse_spec({"title": "  Your   calls ", "mode": "nonsense", "items": [{"ref": "followUp:c", "note": "promised for today " + "x" * 300}, "task:a", {"ref": "task:a"}, {"ref": "task:b"}, {"ref": "task:zz"}, {"ref": ""}, 7]}, known())
    assert spec.title == "Your calls" and spec.mode == "replace"  # a mode it does not know is a replace
    assert [e["ref"] for e in spec.entries] == ["followUp:c", "task:a"] and len(spec.entries[0]["note"]) == 140
    why = {d["ref"]: d["why"] for d in spec.dropped}
    assert "twice" in why["task:a"] and "already done" in why["task:b"] and "no such item" in why["task:zz"]
    assert parse_spec("not json", known()).entries == [] and parse_spec(None, known()).title == "Your list"
    assert len(parse_spec({"items": [{"ref": "task:a"}]}, {**known()}).entries) == 1


def test_a_new_request_meets_the_list_in_force_as_it_says_and_never_twice():
    old = [entry("a"), {**entry("b"), "state": "shown"}, entry("c")]
    assert [e["ref"] for e in merge(old, [entry("x")], "replace")] == ["x"]
    assert [e["ref"] for e in merge(old, [entry("x"), entry("a")], "append")] == ["a", "b", "c", "x"]  # "a" is already waiting: not added again
    assert [e["ref"] for e in merge(old, [entry("x")], "next", cursor=1)] == ["a", "b", "x", "c"]  # right after the one in front of the owner
    settled = [{**entry("a"), "state": "done"}]
    assert [e["ref"] for e in merge(settled, [entry("a")], "append")] == ["a", "a"]  # a settled one may come round again


def test_numbering_counts_only_what_is_real_and_the_next_one_skips_what_is_settled():
    entries = [{**entry("a"), "state": "done"}, {**entry("b"), "state": "gone"}, entry("c"), entry("d")]
    assert counts(entries)["total"] == 3 and counts(entries)["left"] == 2 and position(entries, 2) == (2, 3)
    assert next_index(entries, -1) == 2 and next_index(entries, 2) == 3 and next_index(entries, 3) == 2  # (it wraps to what is still pending)
    assert next_index([{**entry("a"), "state": "done"}], -1) is None


def test_plain_words_about_the_list_are_known_and_nothing_else_is():
    for said, word in {"Next": "skip", "done!": "done", "Not today.": "tomorrow", "stop": "stop", "Continue": "resume", "How many left?": "left", "undo that": "undo"}.items():
        assert classify_words(said) == word, said
    assert classify_words("Done with the call to Ramesh") is None and classify_words("what's on today?") is None and classify_words("") is None


# --- the list in the app: made once, walked by code ----------------------------------------------------------------------------------


async def ready(rig):
    fill(rig)
    await connect(rig)
    await choose(rig, agent=PLANNER)
    c = await telegram(rig, agent="daily-planner")
    await send(rig, c, f"/start {c['pairingCode']}", chat=555, update=1)
    rig.telegram.sent.clear()
    rig.telegram.keyboards.clear()
    return c


def ids(rig):
    return {r["id"]: r for t in rig.data.tables.values() for r in t}


async def queue(rig):
    return (await rig.client.get("/v1/agent/queue", headers=headers())).json()["queue"]


async def make_list_from_json(rig, c, items, mode="replace", title="Your calls", update=2):
    rig.script += [ChatResult("", [ToolCall("q1", "set_queue", {"title": title, "mode": mode, "items": items})]), ChatResult("Say stop whenever you like.")]
    await send(rig, c, "Who should I call first?", chat=555, update=update)


async def test_a_model_gives_a_list_once_and_the_code_walks_it_with_no_model_call_per_item(rig):
    c = await ready(rig)
    await make_list_from_json(rig, c, [{"ref": "followUp:f1", "note": "promised for today"}, {"ref": "task:t1"}, {"ref": "task:t2", "note": "small"}, {"ref": "task:ghost"}])
    texts = [t for _, t in rig.telegram.sent]
    assert texts[0].startswith("Your calls: 3 things. One at a time.")
    assert texts[1].startswith("1/3 · 📞 Call Ramesh")
    assert "💬 promised for today" in texts[1] and texts[2] == "Say stop whenever you like."
    before = len(rig.llm_log)  # from here on nothing is asked of a model
    q = await queue(rig)
    assert q["source"] == "ai" and q["title"] == "Your calls" and q["counts"]["total"] == 3 and q["status"] == "active"
    first = rig.telegram.keyboards[1][0][0]["callback_data"]
    await press(rig, c, first, update=3)
    assert rig.telegram.edits[-1][2].startswith("✅ ") and "2/3 · " in rig.telegram.edits[-1][2]
    second = [b["callback_data"] for row in rig.telegram.edit_buttons_shown[-1] for b in row if b["callback_data"].split(":")[1] == "k"][0]
    await press(rig, c, second, update=4)
    assert "3/3 · " in rig.telegram.edits[-1][2] and "💬 small" in rig.telegram.edits[-1][2]
    assert len(rig.llm_log) == before  # not one model call to walk three items


async def test_plain_words_move_the_list_on_without_a_model_and_the_list_keeps_its_place(rig):
    c = await ready(rig)
    await make_list_from_json(rig, c, [{"ref": "followUp:f1"}, {"ref": "task:t1"}, {"ref": "task:t2"}, {"ref": "task:t3"}])
    before, n = len(rig.llm_log), len(rig.telegram.sent)
    await send(rig, c, "done", chat=555, update=3)
    assert "Call Ramesh: done" in rig.telegram.sent[-1][1] and "2/4 · " in rig.telegram.sent[-1][1]
    assert ids(rig)["f1"]["outcome"]["status"] == "done"
    await send(rig, c, "next", chat=555, update=4)
    assert rig.telegram.sent[-1][1].startswith("⏭ Skipped.") and "3/4 · " in rig.telegram.sent[-1][1]
    await send(rig, c, "How many left?", chat=555, update=5)
    assert rig.telegram.sent[-1][1].startswith("2 left of 4 on Your calls: 1 done, 0 moved, 1 skipped.")
    await send(rig, c, "stop", chat=555, update=6)
    assert "2 still on your list" in rig.telegram.sent[-1][1] and (await queue(rig))["status"] == "paused"
    await send(rig, c, "tomorrow", chat=555, update=7)  # a list that is paused has nothing in front of the owner: the word is not for the list, so it goes to Chittu
    assert len(rig.llm_log) >= before  # (and nothing above needed a model)
    rig.llm_log.clear()
    await send(rig, c, "continue", chat=555, update=8)
    assert rig.telegram.sent[-1][1].startswith("3/4 · ") and rig.llm_log == []  # it picked up exactly where it stopped
    assert len(rig.telegram.sent) > n


async def test_a_new_request_replaces_the_list_and_the_old_buttons_say_so(rig):
    c = await ready(rig)
    await make_list_from_json(rig, c, [{"ref": "followUp:f1"}, {"ref": "task:t1"}, {"ref": "task:t2"}])
    old_button = rig.telegram.keyboards[1][0][0]["callback_data"]
    rig.telegram.sent.clear()
    await make_list_from_json(rig, c, [{"ref": "task:t3"}], title="Small things", update=3)
    assert "I've put the earlier list aside: 3 were left." in rig.telegram.sent[0][1] and rig.telegram.sent[1][1].startswith("1/1 · ")
    assert (await queue(rig))["title"] == "Small things"
    await press(rig, c, old_button, update=4)
    assert "earlier list" in rig.telegram.edits[-1][2]  # a button from the old list does nothing to the new one
    assert ids(rig)["f1"]["outcome"]["status"] == "open"


async def test_adding_to_the_list_in_force_puts_the_new_items_at_the_end_or_next(rig):
    c = await ready(rig)
    await make_list_from_json(rig, c, [{"ref": "followUp:f1"}, {"ref": "task:t1"}])
    rig.telegram.sent.clear()
    await make_list_from_json(rig, c, [{"ref": "task:t3"}], mode="append", title="ignored", update=3)
    assert rig.telegram.sent[0][1] == "Added 1 to your list: 3 left." and len(rig.telegram.sent) == 2  # no second item: one is already in front of the owner
    await make_list_from_json(rig, c, [{"ref": "task:t2"}], mode="next", update=4)
    q = await queue(rig)
    assert [e["ref"] for e in q["entries"]] == ["followUp:f1", "task:t2", "task:t1", "task:t3"] and q["title"] == "Your calls"


async def test_what_is_settled_somewhere_else_is_never_shown_and_the_numbering_shrinks(rig):
    c = await ready(rig)
    await make_list_from_json(rig, c, [{"ref": "followUp:f1"}, {"ref": "task:t1"}, {"ref": "task:t2"}])
    ids(rig)["t1"]["outcome"] = {"status": "done"}  # finished in the app meanwhile
    await send(rig, c, "done", chat=555, update=3)
    assert "2/2 · " in rig.telegram.sent[-1][1] and "File the receipts" not in rig.telegram.sent[-1][1] or "t2" in json.dumps(rig.data.tables)
    q = await queue(rig)
    assert {e["ref"]: e["state"] for e in q["entries"]}["task:t1"] == "gone" and q["counts"]["total"] == 2


async def test_the_last_one_can_be_undone_from_a_button_or_a_word(rig):
    c = await ready(rig)
    await make_list_from_json(rig, c, [{"ref": "followUp:f1"}, {"ref": "task:t1"}, {"ref": "task:t2"}])
    await send(rig, c, "done", chat=555, update=3)
    assert ids(rig)["f1"]["outcome"]["status"] == "done"
    board = [b for row in rig.telegram.keyboards[-1] for b in row]
    assert any("Undo" in b["text"] for b in board)
    await send(rig, c, "undo", chat=555, update=4)
    assert ids(rig)["f1"]["outcome"]["status"] == "open" and "↩️" in rig.telegram.sent[-1][1] and "1/3 · " in rig.telegram.sent[-1][1]  # it is back in front, as the first again


async def test_the_list_holds_through_the_night_and_the_morning_carries_it_over_and_adds_what_is_new(rig):
    from app.presentation.deps import chittu_run

    from .test_actions import WHO

    c = await ready(rig)
    await make_list_from_json(rig, c, [{"ref": "followUp:f1"}, {"ref": "task:t1"}, {"ref": "task:t2"}])
    await send(rig, c, "done", chat=555, update=3)
    await chittu_run(rig.app, WHO, "night")()
    night = rig.telegram.sent[-1][1]
    assert night.startswith("🌙 Good night") and "Your list is still here with 2 left" in night
    assert (await queue(rig))["status"] == "paused"  # not closed
    rig.data.tables["tasks"].append({"id": "new1", "title": "Brand new", "priority": "high", "dueDate": T, "outcome": {"status": "open"}})
    await chittu_run(rig.app, WHO, "morning")()
    hello = rig.telegram.sent[-1][1]
    assert hello.startswith("☀️ Good morning") and "still on the list from before" in hello
    q = await queue(rig)
    refs = [e["ref"] for e in q["entries"] if e["state"] in ("pending", "shown")]  # (the one that was in front when it paused is still in front)
    assert "task:t1" in refs and "task:new1" in refs and refs.count("task:t1") == 1  # what was left, and what is new, once each
    assert q["status"] == "waiting"


async def test_a_mail_that_needs_the_owner_joins_the_list_in_force(rig):
    from app.presentation.deps import build_services

    c = await ready(rig)
    await make_list_from_json(rig, c, [{"ref": "followUp:f1"}, {"ref": "task:t1"}])
    from .test_actions import WHO

    async with rig.app.state.session_factory() as session:
        agent, _ = build_services(rig.app, session)
        await agent.actions.record_mail(WHO, agent_name="Mail watch · S&B", workflow_id="watch-sb-u1", item_id="acc1:1:9", account="acc1", uid=9, folder="INBOX", subject="RTI reply", sender="pio@labour.gov.in", summary="Reply received", priority="high", label="now", due=T, workspace="sb")
        await session.commit()
    q = await queue(rig)
    assert q["counts"]["total"] == 3 and q["entries"][-1]["ref"].startswith("mail:") and q["entries"][-1]["note"] == "new"


async def test_a_word_that_is_not_for_the_list_goes_to_chittu_and_no_list_means_no_surprise(rig):
    c = await ready(rig)
    rig.script += [ChatResult("There is no list right now.")]
    await send(rig, c, "continue", chat=555, update=2)  # no list in force: not handled by the code, so Chittu answers
    assert rig.telegram.sent[-1][1].startswith("There is no list right now")
    await make_list_from_json(rig, c, [{"ref": "followUp:f1"}], update=3)
    rig.script += [ChatResult("Marked.")]
    await send(rig, c, "Done with the call to Ramesh", chat=555, update=4)  # more than the bare word: Chittu's
    assert rig.telegram.sent[-1][1] == "Marked."


async def test_the_list_survives_a_restart_because_it_lives_in_the_database(rig):
    c = await ready(rig)
    await make_list_from_json(rig, c, [{"ref": "followUp:f1"}, {"ref": "task:t1"}])
    from app.infrastructure.db.actions import SqlActionRepository

    async with rig.app.state.session_factory() as session:  # a new session, as after a restart
        q = await SqlActionRepository(session).queue("u1", statuses=["waiting", "active", "paused"])
    assert q is not None and q["title"] == "Your calls" and [e["ref"] for e in q["entries"]] == ["followUp:f1", "task:t1"] and q["source"] == "ai"
    assert TOMORROW and TODAY


async def test_which_ones_walks_the_items_of_a_previewed_change_one_by_one_and_changes_nothing(rig):
    c = await ready(rig)
    rig.script += [ChatResult("", [ToolCall("p1", "preview_move", {"from_day": "today", "to": "tomorrow"})]), ChatResult("That's 4 things to tomorrow. Go ahead?")]
    await send(rig, c, "Clear out today and move everything to tomorrow", chat=555, update=2)
    runs = (await rig.client.get(f"{PLANNER}/runs", headers=headers())).json()["items"]
    steps = (await rig.client.get(f"/v1/agent/runs/{runs[0]['id']}", headers=headers())).json()["steps"]
    cid = json.loads(next(s for s in steps if s["tool"] == "preview_move")["result"])["changesetId"]
    rig.telegram.sent.clear()
    rig.telegram.keyboards.clear()
    rig.script += [ChatResult("", [ToolCall("w1", "walk_through", {"changeset_id": cid})]), ChatResult("Say stop whenever you like.")]
    await send(rig, c, "which ones?", chat=555, update=3)
    first = rig.telegram.sent[1][1]
    assert first.startswith("1/4 · ") and "💬 → " in first  # each item with where it would go
    buttons = [b["text"] for row in rig.telegram.keyboards[1] for b in row]
    assert buttons == ["⏭ Next", "⏹ Stop"]  # looking only: no Done, no Tomorrow
    before = json.dumps(rig.data.tables, sort_keys=True)
    await send(rig, c, "next", chat=555, update=4)
    assert rig.telegram.sent[-1][1].startswith("⏭ Next.") and "2/4 · " in rig.telegram.sent[-1][1]
    assert json.dumps(rig.data.tables, sort_keys=True) == before  # nothing was changed by looking


async def test_overdue_and_also_urgent_is_one_list_and_a_word_finds_an_item(rig):
    from app.application.toolpacks import PackContext
    from app.application.toolpacks.actions import ActionsPack

    from .test_actions import WHO, finish, service

    c = await ready(rig)
    rig.data.tables["tasks"] += [{"id": "late", "title": "Pay the registry fee", "priority": "normal", "dueDate": (TODAY.replace(day=TODAY.day) - __import__("datetime").timedelta(days=3)).isoformat(), "outcome": {"status": "open"}},
                                 {"id": "urg", "title": "Urgent affidavit now", "priority": "urgent", "dueDate": T, "outcome": {"status": "open"}}]
    rig.script += [ChatResult("", [ToolCall("w1", "walk_through", {"at_least": "urgent", "or_overdue": True, "label": "late and urgent"})]), ChatResult("Say stop whenever you like.")]
    await send(rig, c, "Show me what's overdue and also what's urgent", chat=555, update=2)
    q = await queue(rig)
    assert q["title"] == "Late and urgent" and [e["ref"] for e in q["entries"]] == ["task:urg", "task:t3"][:0] + [e["ref"] for e in q["entries"]] and {"task:urg", "task:late"} <= {e["ref"] for e in q["entries"]}
    svc, s, session = await service(rig)
    try:
        tools = await ActionsPack().tools(PackContext(rig.data, T, actions=svc, who=WHO, token=""))
        find = next(t for t in tools if t.spec.name == "find_items")
        out = (await find.run({"day": "today", "query": "registry"})).result
        assert [i["title"] for i in out["items"]] == ["Pay the registry fee"]
    finally:
        await finish(s, session)


async def test_a_move_to_a_weekend_day_says_so(rig):
    from datetime import timedelta

    from .test_actions import WHO, finish, service

    fill(rig)
    svc, s, session = await service(rig)
    try:
        saturday = TODAY + timedelta(days=(5 - TODAY.weekday()) % 7 or 7)
        out = await svc.preview_move(WHO, "", to=saturday.isoformat(), day="today")
        assert any("is a Saturday" in w for w in out["warnings"])
    finally:
        await finish(s, session)
