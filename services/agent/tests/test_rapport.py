"""Rapport plans: every new contact gets one, the messages go out by themselves inside the owner's window and the contact's working hours, inside the caps, and what
WhatsApp will not take comes to the owner with the text written and a button that opens WhatsApp."""

from datetime import UTC, datetime

import pytest
from stiamo_common.errors import ValidationFailed

from app.application.actions import ActionService
from app.application.rapport import RapportService, contact_key
from app.application.services import Who
from app.domain.llm import ChatResult, ToolCall
from app.infrastructure.db.actions import SqlActionRepository
from app.infrastructure.db.rapport import SqlRapportRepository

from .conftest import headers
from .test_actions import PLANNER, WHO
from .test_agent_api import choose, connect
from .test_alerts import press
from .test_channels import send, telegram

MONDAY_1030_IST = datetime(2026, 11, 23, 6, 30, tzinfo=UTC)  # 12:00 IST on a Monday
ALI = {"id": "p1", "name": "Ali Hassan", "organisation": "Gulf Workers Union", "title": "Secretary", "workspaces": ["sb"], "emails": [{"value": "ali@gwu.org"}], "phones": [{"value": "+974 5555 1212", "label": "mobile"}], "tags": ["union", "migration"], "profile": {"sector": "Trade union"}, "location": "Doha, Qatar", "isNew": True}
RAMESH = {"id": "p2", "name": "Ramesh Kumar", "organisation": "Labour Dept", "workspaces": ["sb", "personal"], "emails": [{"value": "ramesh@labour.gov.in"}], "phones": [{"value": "98765 43210"}], "tags": ["labour"], "profile": {}, "location": "Hyderabad, India", "isNew": True}
MEENA = {"id": "p3", "name": "Meena Rao", "workspaces": ["fsa"], "emails": [{"value": "meena@client.io"}], "phones": [{"value": "+55 11 91234 5678"}], "tags": ["client"], "location": "Sao Paulo, Brazil", "isNew": True}
SKIP = {"id": "p4", "name": "Private Person", "workspaces": ["personal"], "emails": [{"value": "p@x.org"}], "phones": [], "isNew": True, "rapportExcluded": True}


class Bench:
    def __init__(self, rig):
        self.rig, self.said = rig, []

    async def open(self):
        from app.infrastructure.db.models import Base  # noqa: F401

        self.cm = self.rig.app.state.session_factory()
        self.s = await self.cm.__aenter__()

        async def notify(who, text, buttons=None):
            self.said.append((text, buttons))
            return 1

        actions = ActionService(SqlActionRepository(self.s), self.rig.app.state.data_factory)
        self.svc = RapportService(SqlRapportRepository(self.s), actions, self.rig.app.state.data_factory, notify, None, None, lambda: MONDAY_1030_IST)
        self.actions = actions
        self.brought = []

        class Queue:
            async def current(_, who):
                return None

            async def set(_, who, token, spec, source="ai", channel_id=None):
                self.brought.append([e["ref"] for e in spec.entries])
                assert spec.title == "Rapport messages" and spec.mode == "replace" and source == "rapport"

        self.svc.queue = Queue()
        return self

    async def close(self):
        await self.s.commit()
        await self.cm.__aexit__(None, None, None)


async def bench(rig, **kw):
    return await Bench(rig, **kw).open()


async def test_every_new_contact_in_any_workspace_gets_a_plan_and_the_owner_is_told_and_excluded_ones_are_left_alone(rig):
    rig.data.people = [ALI, RAMESH, MEENA, SKIP]
    b = await bench(rig)
    made = await b.svc.scan(WHO, "")
    assert sorted(p["name"] for p in made) == ["Ali Hassan", "Meena Rao", "Ramesh Kumar"]  # the one marked "no plan" is not given one
    assert len(b.said) == 3 and b.said[0][0].startswith("🌱 New contact: ") and "get-to-know plan" in b.said[0][0]
    ali = next(p for p in made if p["name"] == "Ali Hassan")
    assert ali["zone"] == "Asia/Qatar" and ali["tier"] == "B"
    assert next(p for p in made if p["name"] == "Meena Rao")["zone"] == "America/Sao_Paulo"  # from the phone's country code
    assert await b.svc.scan(WHO, "") == []  # nothing twice
    buttons = [x["text"] for row in b.said[0][1] for x in row]
    assert buttons == ["⭐ Tier A", "Tier C", "🚫 No plan for them"]
    await b.close()


async def test_a_due_message_is_written_and_sent_inside_the_window_and_nobody_gets_more_than_three_in_a_day(rig):
    dan = {**RAMESH, "id": "p5", "name": "Dan", "emails": [{"value": "dan@x.org"}], "phones": [{"value": "98000 00001"}]}
    eve = {**RAMESH, "id": "p6", "name": "Eve", "emails": [{"value": "eve@x.org"}], "phones": [{"value": "98000 00002"}]}
    rig.data.people = [RAMESH, dan, eve, {**RAMESH, "id": "p7", "name": "Fay", "emails": [{"value": "fay@x.org"}], "phones": [{"value": "98000 00003"}]}]  # (all tagged, with a place)
    b = await bench(rig)
    await b.svc.scan(WHO, "")
    out = await b.svc.tick(WHO, "")
    items = [i async for i in _items(b)]
    texts = [i["notes"] for i in items]
    assert out["queued"] == 3 and len(items) == 3 and [t for t, _ in b.said if t.startswith("✉️")] == []  # the cap for one day across everyone; nothing is sent on its own
    assert all(i["priority"] == "urgent" and i["source"] == "rapport" and i["kind"] == "message" for i in items)  # class 1, and ordinary action items
    assert b.brought == [[i["kind"] + ":" + i["id"] for i in items]]  # handed to Chittu's list together, so it walks them 1/3, 2/3, 3/3
    assert all("Hello " in t for t in texts) and all("how are you" not in t.lower() for t in texts)
    fay = await b.svc._repo.by_key(WHO.user_id, contact_key({"emails": [{"value": "fay@x.org"}]}))
    assert fay["touches"][0]["day"] == "2026-11-24" and fay["touches"][0]["state"] == "planned"
    ramesh = await b.svc._repo.by_key(WHO.user_id, contact_key(RAMESH))
    assert ramesh["touches"][0]["state"] == "queued"  # waiting for the owner to send it on WhatsApp
    assert (await b.svc.tick(WHO, ""))["queued"] == 0  # nothing more is due
    await b.close()


async def test_a_due_message_comes_to_the_owner_with_the_text_written_and_a_button_that_opens_the_contacts_whatsapp(rig):
    rig.data.people = [ALI]
    b = await bench(rig)
    await b.svc.scan(WHO, "")
    b.said.clear()
    out = await b.svc.tick(WHO, "")
    assert out == {"sent": 0, "queued": 1, "waiting": 0}  # nothing is sent by Stiamo
    assert b.said == [] and len(b.brought) == 1  # no message of its own: it goes into Chittu's list
    plan = await b.svc._repo.by_key(WHO.user_id, contact_key(ALI))
    item = [i async for i in _items(b)][0]
    assert item["kind"] == "message" and item["source"] == "rapport" and item["links"]["rapport"]["draft"].startswith("Hello Ali") and item["links"]["rapport"]["wa"].startswith("https://wa.me/97455551212")  # an action item too, with the text
    said = await b.svc.press(WHO, "", "s", f"{plan['id'][:8]}:{plan['touches'][0]['n']}")
    assert said == "✅ Noted: sent to Ali Hassan."
    plan = await b.svc._repo.by_key(WHO.user_id, contact_key(ALI))
    assert plan["touches"][0]["state"] == "sent" and plan["unanswered"] == 1
    await b.close()


async def _items(b):
    for i in await b.actions._repo.items("u1"):
        yield i


async def test_two_unanswered_messages_widen_the_gaps_three_pause_the_plan_and_a_reply_warms_it_and_the_owner_can_pause_or_exclude(rig):
    rig.data.people = [ALI]
    b = await bench(rig)
    await b.svc.scan(WHO, "")
    plan = await b.svc._repo.by_key(WHO.user_id, contact_key(ALI))
    pid = plan["id"][:8]
    reply = await b.svc.on_inbound(WHO, "whatsapp:+97455551212", "Thanks, good to hear from you")
    assert reply is True and "Ali Hassan replied" in b.said[-1][0]
    assert await b.svc.on_inbound(WHO, "whatsapp:+10000000000", "hi") is False  # a stranger's message is not a plan's
    assert await b.svc.press(WHO, "", "p", f"{pid}:0") == "⏸ Ali Hassan is paused for 60 days."
    assert (await b.svc._repo.by_key(WHO.user_id, contact_key(ALI)))["pausedUntil"] > "2027-01-01"
    assert (await b.svc.tick(WHO, ""))["sent"] == 0  # paused: nothing goes
    said = await b.svc.press(WHO, "", "x", f"{pid}:0")
    assert said.startswith("🚫 No plan for Ali Hassan") and (await b.svc._repo.by_key(WHO.user_id, contact_key(ALI)))["status"] == "excluded"
    assert [p for p in rig.data.people if p["id"] == "p1"][0]["rapportExcluded"] is True  # and the contact is marked, so no plan is built again
    assert await b.svc.scan(WHO, "") == []
    b2 = await b.svc.start(WHO, "", {**ALI, "rapportExcluded": False}, tier="A")  # attach an established contact: brought back, at tier A
    assert b2["status"] == "active" and b2["tier"] == "A" and len(b2["touches"]) >= 9
    await b.close()


async def test_a_contact_in_brazil_is_written_to_in_their_working_hours_even_outside_the_owners_window(rig):
    from app.domain.rapport import slot_for

    at, outside = slot_for("2026-11-23", "America/Sao_Paulo")
    assert outside is True and at == 17 * 60 + 30


async def test_on_telegram_new_contacts_are_walked_one_at_a_time_with_buttons_to_start_a_plan_or_leave_them_out(rig):
    rig.data.people = [ALI, RAMESH]
    await connect(rig)
    await choose(rig, agent=PLANNER)
    c = await telegram(rig, agent="daily-planner")
    await send(rig, c, f"/start {c['pairingCode']}", chat=555, update=1)
    rig.telegram.sent.clear()
    rig.telegram.keyboards.clear()
    rig.script += [ChatResult("", [ToolCall("w1", "walk_contacts", {"recent_days": 14})]), ChatResult("One at a time.")]
    await send(rig, c, "Who did I add this week?", chat=555, update=2)
    texts = [t for _, t in rig.telegram.sent]
    assert texts[0].startswith("New contacts: 2 things. One at a time.") and texts[1].startswith("1/2 · 👤 Ali Hassan\nGulf Workers Union") and "🏷 union, migration" in texts[1]
    board = [b for kb in rig.telegram.keyboards if kb for row in kb for b in row]
    assert [b["text"] for b in board][:5] == ["🌱 Start plan", "⭐ Tier A", "🚫 No plan", "⏭ Next", "⏹ Stop"]
    await press(rig, c, next(b for b in board if b["text"] == "⭐ Tier A")["callback_data"], update=3)
    assert rig.telegram.edits[-1][2].startswith("🌱 Plan for Ali Hassan, tier A.") and "2/2 · 👤 Ramesh Kumar" in rig.telegram.edits[-1][2]
    nxt = [b for row in rig.telegram.edit_buttons_shown[-1] for b in row]
    await press(rig, c, next(b for b in nxt if b["text"] == "🚫 No plan")["callback_data"], update=4)
    assert "🚫 No plan for Ramesh Kumar." in rig.telegram.edits[-1][2] and [p for p in rig.data.people if p["id"] == "p2"][0]["rapportExcluded"] is True


async def test_a_place_in_the_address_sets_their_zone_and_a_place_added_later_moves_the_messages(rig):
    from app.application.rapport import zone_of
    from app.domain.zones import zone_from_location

    assert zone_from_location("Doha, Qatar") == "Asia/Qatar" and zone_from_location("Somewhere, Brazil") == "America/Sao_Paulo" and zone_from_location("Nowhere") is None
    assert zone_of({"location": "Doha, Qatar", "phones": [{"value": "+55 11 91234 5678"}]}) == "Asia/Qatar"  # the address beats the phone's country
    assert zone_of({"timezone": "Asia/Dubai", "location": "Doha, Qatar"}) == "Asia/Dubai"  # what the owner set beats both

    # a place written later (in the app or in Google) moves what is still to come to their new local hours; what was sent stays
    rig.data.people = [{**MEENA, "location": "Doha, Qatar"}]  # their phone says Brazil; the address says Qatar
    b3 = await bench(rig)
    plan = await b3.svc.start(WHO, "", MEENA)
    assert plan["zone"] == "America/Sao_Paulo"
    before = [(t["day"], t["at"]) for t in plan["touches"]]
    assert await b3.svc.rezone(WHO, "") == 1
    after = await b3.svc._repo.by_key(WHO.user_id, contact_key(MEENA))
    assert after["zone"] == "Asia/Qatar" and [(t["day"], t["at"]) for t in after["touches"]] != before and all(t["purpose"] for t in after["touches"])
    assert await b3.svc.rezone(WHO, "") == 0  # nothing left to move
    await b3.close()


async def test_a_new_contact_waits_for_its_tags_and_place_then_the_plan_starts_by_itself(rig):
    bare = {"id": "p8", "name": "Chen Wei", "workspaces": ["sb"], "emails": [{"value": "chen@u.org"}], "phones": [{"value": "+213 555 12 34 56"}], "tags": [], "profile": {}, "isNew": True}
    rig.data.people = [bare]
    b = await bench(rig)
    held = await b.svc.scan(WHO, "")
    assert [p["status"] for p in held] == ["needs_info"] and held[0]["missing"] == ["tags", "where they are"] and held[0]["touches"] == []
    assert "I need their tags and where they are" in b.said[-1][0] and b.said[-1][1][0][0]["text"] == "🚫 No plan for them"
    assert await b.svc.scan(WHO, "") == []  # asked once, not every hour
    assert (await b.svc.tick(WHO, ""))["queued"] == 0  # and nothing is written to them
    rig.data.people = [{**bare, "tags": ["union"]}]
    await b.svc.scan(WHO, "")
    assert (await b.svc._repo.by_key(WHO.user_id, contact_key(bare)))["status"] == "needs_info"  # a place is still missing
    rig.data.people = [{**bare, "tags": ["union"], "location": "Algiers, Algeria"}]
    b.said.clear()
    started = await b.svc.scan(WHO, "")
    plan = await b.svc._repo.by_key(WHO.user_id, contact_key(bare))
    assert [p["name"] for p in started] == ["Chen Wei"] and plan["status"] == "active" and plan["touches"] and "get-to-know plan" in b.said[0][0]
    await b.close()


async def test_the_message_is_written_in_the_name_the_owner_set_and_a_rapport_message_is_class_one_in_the_order():
    from app.domain.actionitems import from_native, queue_order

    mk = lambda i, **o: from_native({"id": i, "kind": "task", "title": i, "workspace": "sb", "day": "2026-11-23", "priority": "normal", "status": "open", **o})  # noqa: E731
    items = [mk("late", day="2026-11-20", priority="urgent"), mk("hearing", kind="hearing", start=600), mk("calls", kind="call"), mk("wa", kind="message", source="rapport", priority="urgent")]
    assert queue_order(items, "2026-11-23")[0].ref == "message:wa"  # ahead of an urgent overdue item and a fixed hearing
    from app.application.rapport import RapportService

    assert "Everyone calls them Bhargav; their full name is Kanthi Kiran Bhargav." == RapportService._owner_line("Bhargav", "Kanthi Kiran Bhargav")
    assert "introduce them by the full name once (Kanthi Kiran Bhargav)" in RapportService._sign_off("Bhargav", "Kanthi Kiran Bhargav")
    assert RapportService._owner_line("", "") == "Their name is not known: do not invent one." and RapportService._sign_off("", "") == "Do not sign with a name."


class Adviser:
    """A model that answers like the adviser would, and keeps what it was asked."""

    def __init__(self, answer):
        self.answer, self.asked = answer, []

    async def chat(self, model, messages, tools, thinking):
        self.asked.append(messages)
        return type("R", (), {"text": self.answer})()


async def test_the_adviser_learns_what_the_owner_says_adjusts_the_persona_and_turns_the_introduction_into_a_follow_up(rig):
    import json

    from app.domain.rapport_advisor import ADVISOR_SYSTEM, parse_advice

    persona = {"summary": "Union secretary in Doha. Wrote to on 3 Nov; read, no reply.", "stage": "in_touch", "history": ["3 Nov: WhatsApp about wages; read, no reply"], "common": ["migrant workers"], "style": "brief, English", "next": {"purpose": "follow_up", "idea": "short note on 10 Nov"}}
    answer = json.dumps({"reply": "You already wrote on 3 Nov, so this is a follow-up. Wait until 10 Nov.", "stage": "in_touch", "persona": persona, "suggestions": [{"purpose": "follow_up", "message": "Hi Ali, following up on my 3 Nov note about wages. Bhargav"}, {"purpose": "value", "message": ""}], "tier": "A"})
    rig.data.people = [{**ALI, "rapportExcluded": False}]
    b = await bench(rig)
    client = Adviser(answer)

    async def model(who):
        return client, "m", "off"

    b.svc._model = model
    await b.svc.scan(WHO, "")
    plan = await b.svc._repo.by_key(WHO.user_id, contact_key(ALI))
    assert plan["touches"][0]["purpose"] == "intro"
    out = await b.svc.advise(WHO, plan["id"], "I messaged Ali on 3 Nov about wages. He read it and did not answer. We share the migrant workers cause.")
    assert out["reply"].startswith("You already wrote") and out["persona"]["stage"] == "in_touch" and out["suggestedTier"] == "A"
    assert [s["message"] for s in out["suggestions"]] == ["Hi Ali, following up on my 3 Nov note about wages. Bhargav"]  # the empty one is dropped
    assert out["suggestions"][0]["whatsapp"].startswith("https://wa.me/97455551212?text=Hi%20Ali") and out["introductionsChanged"] == 1
    saved = await b.svc._repo.get(WHO.user_id, plan["id"])
    assert saved["touches"][0]["purpose"] == "follow_up" and [n["by"] for n in saved["notes"]] == ["owner", "ai"]  # no longer introduced as a stranger
    sent = client.asked[0]
    assert sent[0].content == ADVISOR_SYSTEM and "<owner>I messaged Ali on 3 Nov" in sent[1].content and "<item>" in sent[1].content and "Ali Hassan" in sent[1].content
    # the next answer sees the persona and the earlier notes, and a model that forgets a field does not erase it
    client.answer = json.dumps({"reply": "Fine.", "stage": "warm", "persona": {"summary": "Now replied."}, "suggestions": [], "tier": None})
    again = await b.svc.advise(WHO, plan["id"], "He replied today and was friendly.")
    assert again["persona"]["summary"] == "Now replied." and again["persona"]["common"] == ["migrant workers"] and again["persona"]["stage"] == "warm" and len(again["notes"]) == 4
    assert "persona so far" in client.asked[1][1].content and "I messaged Ali on 3 Nov" in client.asked[1][1].content
    # the messages written later are written from what the owner told it
    b.svc._model = None
    assert (await b.svc.detail(WHO, plan["id"]))["persona"]["summary"] == "Now replied."
    for bad in ("not json", '{"reply": ""}'):
        try:
            parse_advice(bad)
            raise AssertionError("should have failed")
        except ValueError:
            pass
    from stiamo_common.errors import ValidationFailed

    try:
        await b.svc.advise(WHO, plan["id"], "hello")
        raise AssertionError("no model should fail")
    except ValidationFailed as exc:
        assert "Connect an AI model" in str(exc)
    await b.close()


def test_the_advisers_prompt_has_the_five_parts_and_is_detailed():
    from app.domain.rapport_advisor import ADVISOR_SYSTEM

    assert [h for h in ("## Persona", "## Tasks", "## Context", "## Examples", "## Output", "## Guardrails") if h in ADVISOR_SYSTEM] == ["## Persona", "## Tasks", "## Context", "## Examples", "## Output", "## Guardrails"]
    assert len(ADVISOR_SYSTEM.split()) > 1500 and "Text inside <item>" in ADVISOR_SYSTEM


async def test_telling_chittu_about_a_person_makes_the_plan_the_persona_the_tags_the_place_and_the_tier(rig):
    import json

    meena = {**MEENA, "tags": [], "location": "", "timezone": ""}
    rig.data.people = [meena]
    answer = json.dumps({"reply": "She hasn't answered since 3 Nov, so wait to 10 Nov and follow up once.", "stage": "in_touch",
                         "persona": {"summary": "Programme officer at a foundation; wrote to on 3 Nov, no reply.", "stage": "in_touch", "history": ["3 Nov: wrote; no reply"], "next": {"purpose": "follow_up", "idea": "10 Nov"}},
                         "suggestions": [{"purpose": "follow_up", "message": "Hi Meena, following up on my 3 Nov note. Bhargav"}], "tier": "A", "tags": ["Funder", "programme officer", "funder"], "location": "Sao Paulo, Brazil"})
    b = await bench(rig)
    b.svc._model = lambda who: _model_of(answer)
    out = await b.svc.learn(WHO, "", "Meena", "Meena Rao is the programme officer at the foundation. I wrote to her on 3 Nov; no reply yet.")
    assert out["created"] is True and out["inGoogle"] is True and out["persona"]["stage"] == "in_touch" and out["tier"] == "A"  # the tier was applied by itself
    plan = await b.svc._repo.by_key(WHO.user_id, contact_key(meena))
    assert plan["tier"] == "A" and all(t["purpose"] != "intro" for t in plan["touches"] if t["state"] == "planned")
    google = [p for p in rig.data.people if p["id"] == "p3"][0]
    assert google["tags"] == ["funder", "programme officer"] and google["location"] == "Sao Paulo, Brazil"  # inferred from what was said, written to the contact
    assert out["contactUpdated"] == {"tags": ["funder", "programme officer"], "location": "Sao Paulo, Brazil"}
    # someone who is not in Google contacts gets a plan from the name and number given
    rig.data.people = []
    stranger = await b.svc.learn(WHO, "", "Anil Reddy", "Union leader in Hyderabad.", "+91 98765 11111")
    assert stranger["created"] is True and stranger["inGoogle"] is False and stranger["contactUpdated"] == {}
    plan2 = [p for p in await b.svc._repo.all(WHO.user_id) if p["name"] == "Anil Reddy"][0]
    assert plan2["phone"] == "+919876511111" and plan2["contactId"].startswith("manual:")
    await b.close()


async def _model_of(answer):
    return Adviser(answer), "m", "off"
