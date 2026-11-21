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
ALI = {"id": "p1", "name": "Ali Hassan", "organisation": "Gulf Workers Union", "title": "Secretary", "workspaces": ["sb"], "emails": [{"value": "ali@gwu.org"}], "phones": [{"value": "+974 5555 1212", "label": "mobile"}], "tags": ["union", "migration"], "profile": {"sector": "Trade union"}, "isNew": True}
RAMESH = {"id": "p2", "name": "Ramesh Kumar", "organisation": "Labour Dept", "workspaces": ["sb", "personal"], "emails": [{"value": "ramesh@labour.gov.in"}], "phones": [{"value": "98765 43210"}], "tags": [], "profile": {}, "isNew": True}
MEENA = {"id": "p3", "name": "Meena Rao", "workspaces": ["fsa"], "emails": [{"value": "meena@client.io"}], "phones": [{"value": "+55 11 91234 5678"}], "isNew": True}
SKIP = {"id": "p4", "name": "Private Person", "workspaces": ["personal"], "emails": [{"value": "p@x.org"}], "phones": [], "isNew": True, "rapportExcluded": True}


class Bench:
    def __init__(self, rig, wa_fails=None):
        self.rig, self.said, self.sent, self.wa_fails = rig, [], [], wa_fails

    async def open(self):
        from app.infrastructure.db.models import Base  # noqa: F401

        self.cm = self.rig.app.state.session_factory()
        self.s = await self.cm.__aenter__()

        async def wa(who, number, text):
            if self.wa_fails:
                raise ValidationFailed(self.wa_fails)
            self.sent.append((number, text))

        async def notify(who, text, buttons=None):
            self.said.append((text, buttons))
            return 1

        actions = ActionService(SqlActionRepository(self.s), self.rig.app.state.data_factory)
        self.svc = RapportService(SqlRapportRepository(self.s), actions, self.rig.app.state.data_factory, wa, notify, None, None, lambda: MONDAY_1030_IST)
        self.actions = actions
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
    assert ali["zone"] == "Asia/Qatar" and ali["tier"] == "B" and "Add tags on the web" not in next(t for t, _ in b.said if "Ali Hassan" in t)  # tagged already: no nudge
    assert "Add tags on the web" in next(t for t, _ in b.said if "Ramesh" in t)
    assert next(p for p in made if p["name"] == "Meena Rao")["zone"] == "America/Sao_Paulo"  # from the phone's country code
    assert await b.svc.scan(WHO, "") == []  # nothing twice
    buttons = [x["text"] for row in b.said[0][1] for x in row]
    assert buttons == ["⭐ Tier A", "Tier C", "🚫 No plan for them"]
    await b.close()


async def test_a_due_message_is_written_and_sent_inside_the_window_and_nobody_gets_more_than_three_in_a_day(rig):
    dan = {**RAMESH, "id": "p5", "name": "Dan", "emails": [{"value": "dan@x.org"}], "phones": [{"value": "98000 00001"}]}
    eve = {**RAMESH, "id": "p6", "name": "Eve", "emails": [{"value": "eve@x.org"}], "phones": [{"value": "98000 00002"}]}
    rig.data.people = [RAMESH, dan, eve, {**RAMESH, "id": "p7", "name": "Fay", "emails": [{"value": "fay@x.org"}], "phones": [{"value": "98000 00003"}]}]
    b = await bench(rig)
    await b.svc.scan(WHO, "")
    out = await b.svc.tick(WHO, "")
    assert out["sent"] == 3 and len(b.sent) == 3  # the cap for one day across everyone: the fourth was planned for tomorrow
    assert all(t.startswith("Hello ") for _, t in b.sent) and all("how are you" not in t.lower() for _, t in b.sent)
    fay = await b.svc._repo.by_key(WHO.user_id, contact_key({"emails": [{"value": "fay@x.org"}]}))
    assert fay["touches"][0]["day"] == "2026-11-24" and fay["touches"][0]["state"] == "planned"
    ramesh = await b.svc._repo.by_key(WHO.user_id, contact_key(RAMESH))
    assert ramesh["touches"][0]["state"] == "sent" and ramesh["unanswered"] == 1 and ramesh["touches"][1]["day"] > "2026-11-23"
    assert (await b.svc.tick(WHO, ""))["sent"] == 0  # nothing more is due
    await b.close()


async def test_what_whatsapp_will_not_take_comes_to_the_owner_with_the_text_written_and_a_button_that_opens_whatsapp(rig):
    rig.data.people = [ALI]
    b = await bench(rig, wa_fails="Twilio couldn't send the message (400). Template required.")
    await b.svc.scan(WHO, "")
    b.said.clear()
    out = await b.svc.tick(WHO, "")
    assert out == {"sent": 0, "queued": 1, "waiting": 0}
    text, buttons = b.said[0]
    assert text.startswith("✉️ Ali Hassan · intro") and "WhatsApp wouldn't send it by itself" in text and "Template required" in text
    flat = [x for row in buttons for x in row]
    assert flat[0]["text"] == "💬 Open WhatsApp" and flat[0]["url"].startswith("https://wa.me/97455551212?text=Hello%20Ali")
    assert [x["text"] for x in flat[1:]] == ["✅ Sent", "🗣 They replied", "✏️ Reword", "⏭ Skip", "⏸ Pause"]
    plan = await b.svc._repo.by_key(WHO.user_id, contact_key(ALI))
    item = [i async for i in _items(b)][0]
    assert item["kind"] == "message" and item["source"] == "rapport" and item["links"]["rapport"]["draft"].startswith("Hello Ali")  # an action item too, with the text
    sent = next(x["callback_data"] for x in flat if x["text"] == "✅ Sent")
    said = await b.svc.press(WHO, "", "s", sent.split(":", 2)[2])
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
