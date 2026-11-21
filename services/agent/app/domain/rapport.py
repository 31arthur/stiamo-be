"""Rapport plans: staying in touch with business contacts, well within limits. Pure: no network, no framework, no model.

A plan is a list of *touches*, each with a purpose (a reason to get in touch: never a bare "how are you"), a day and a time. The gaps are dense at first and widen; the
tier (A key, B usual, C light) sets them. What the code decides, so the model never has to: the dates, the time of day, the daily cap across every contact, the
backoff when someone does not answer, the pause, and the rules about when a message may go. The model only writes each message, at the moment it goes, from the
contact's tags and the purpose.

Times are on the home clock (India's). A message goes inside the owner's window (10:00-15:00 IST) and inside the contact's own working hours; when those never meet
(a contact in Brazil), the contact's hours win and the message goes outside the window.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.domain.zones import HOME, convert

#: Days after the plan starts at which each touch is due, then how often it repeats once the list runs out.
TIERS: dict[str, tuple[list[int], int]] = {
    "A": ([0, 2, 5, 10, 18, 30, 45, 60, 90], 75),
    "B": ([0, 3, 10, 25, 50, 90], 105),
    "C": ([0, 7, 30, 90], 182),
}
DEFAULT_TIER = "B"
#: The reason for each early touch, in order; after them the plan keeps a rhythm of reasons that are never "just checking in".
LADDER = ["intro", "value", "ask", "connect", "invite"]
MAINTAIN = ["sector_news", "milestone", "resource", "event", "annual_review", "festival"]
PURPOSES = {
    "follow_up": "Follow up an earlier exchange: refer to it and move it on by one small, easy step.",
    "reconnect": "Write again after a long silence, without blame, with a real reason to write now.",
    "thanks": "Thank them for something specific they did or said.",
    "intro": "Thank them or introduce yourself: who you are, what you work on, in one line, and that you would be glad to be useful.",
    "value": "Share something useful to them: a document, a circular, a report or a development in their field. One line on why it matters to them.",
    "ask": "Ask one light, specific question about their priorities or upcoming work, so a reply is easy.",
    "connect": "Offer an introduction or a resource that fits their work, with the name and one line on why.",
    "invite": "Invite them to a short call, a meeting or an event, with a concrete suggestion of when.",
    "sector_news": "Send one relevant development in their sector with a line on what it means for them.",
    "milestone": "Congratulate a milestone you know of (a launch, an appointment, an award, a campaign result).",
    "resource": "Send something they can use: a template, a briefing, a contact, a date to watch.",
    "event": "Tell them about an event or deadline that matters to their work, and offer to help.",
    "annual_review": "Look back over the year together: what worked, what is next, where you can help.",
    "festival": "A short, warm greeting for a festival that is theirs, once a year, with no ask.",
}
#: The owner's window for rapport (IST minutes), the contact's working hours (their own clock), the days they do not work, by zone.
OWNER_WINDOW = (10 * 60, 15 * 60)
CONTACT_HOURS = (9 * 60, 18 * 60)
GULF = ("Asia/Qatar", "Asia/Dubai", "Asia/Riyadh", "Asia/Kuwait", "Asia/Bahrain", "Asia/Muscat")
DAILY_CAP = 3
MONTHLY_CAP = 4  # touches to one contact in any 30 days
MIN_GAP_DAYS = 2


def weekend_of(zone: str) -> tuple[int, ...]:
    """The weekdays (0 Monday) nobody is messaged on in that zone: Sunday for most, Friday and Saturday in the Gulf."""
    return (4, 5) if zone in GULF else (6,)


def slot_for(day: str, contact_zone: str | None) -> tuple[int, bool] | None:
    """The minute of the home-clock `day` to send at, and whether it is outside the owner's window. The first quarter hour inside both the owner's window and the
    contact's working hours; if they never overlap, the contact's own working hours (the one nearest the window); None when the day is a day they do not work."""
    zone = contact_zone or HOME
    candidates = range(OWNER_WINDOW[0], OWNER_WINDOW[1] + 1, 15)

    def ok(m: int) -> bool:
        d, local = convert(day, m, HOME, zone)
        return CONTACT_HOURS[0] <= local < CONTACT_HOURS[1] and date.fromisoformat(d).weekday() not in weekend_of(zone)

    for m in candidates:
        if ok(m):
            return m, False
    # no overlap: the earliest the contact's morning allows, on the home clock (06:00-22:00 so it is a sane hour for the owner too)
    for m in range(6 * 60, 22 * 60, 15):
        if ok(m):
            return m, True
    return None


def schedule(tier: str, start: str, zone: str | None, taken: dict[str, int] | None = None, cap: int = DAILY_CAP) -> list[dict[str, Any]]:
    """The whole opening plan: a touch for each offset of the tier (and a few repeats), each on a real day with a real time. `taken` is how many touches are already on
    each day across every contact; a day at the cap is skipped, so the owner never has more than `cap` a day."""
    offsets, repeat = TIERS.get(tier, TIERS[DEFAULT_TIER])
    days = offsets + [offsets[-1] + repeat * k for k in (1, 2, 3)]
    taken = dict(taken or {})
    out: list[dict[str, Any]] = []
    base = date.fromisoformat(start)
    for i, off in enumerate(days):
        day = base + timedelta(days=off)
        last = date.fromisoformat(out[-1]["day"]) if out else None
        if last is not None and day <= last + timedelta(days=MIN_GAP_DAYS - 1):
            day = last + timedelta(days=MIN_GAP_DAYS)
        for _ in range(60):  # look forward for a day with room and a sane time
            iso = day.isoformat()
            at = slot_for(iso, zone)
            if at is not None and taken.get(iso, 0) < cap:
                taken[iso] = taken.get(iso, 0) + 1
                out.append({"n": i, "purpose": purpose_for(i), "day": iso, "at": at[0], "outside": at[1], "state": "planned"})
                break
            day += timedelta(days=1)
    return out


def purpose_for(index: int) -> str:
    return LADDER[index] if index < len(LADDER) else MAINTAIN[(index - len(LADDER)) % len(MAINTAIN)]


def due_now(plan: dict[str, Any], now: datetime) -> dict[str, Any] | None:
    """The next touch if its time has come on the home clock and the plan is live; None otherwise (paused, replied-to and waiting, nothing due)."""
    if plan.get("status") != "active":
        return None
    paused = plan.get("pausedUntil")
    local = now.astimezone(ZoneInfo(HOME))
    if paused and local.date().isoformat() < paused:
        return None
    for t in plan.get("touches", []):
        if t["state"] == "planned":
            moment = datetime.combine(date.fromisoformat(t["day"]), datetime.min.time(), tzinfo=ZoneInfo(HOME)) + timedelta(minutes=t["at"])
            return t if moment <= now else None
    return None


def after_sent(plan: dict[str, Any], touch_n: int, today: str) -> dict[str, Any]:
    """A touch went. Two sent in a row with no answer double the next gap; three pause the plan for 60 days (a person who does not answer is not pestered)."""
    p = {**plan, "touches": [dict(t) for t in plan["touches"]]}
    for t in p["touches"]:
        if t["n"] == touch_n:
            t["state"], t["sentOn"] = "sent", today
    p["unanswered"] = int(plan.get("unanswered", 0)) + 1
    if p["unanswered"] >= 3:
        p["pausedUntil"] = (date.fromisoformat(today) + timedelta(days=60)).isoformat()
        p["pauseReason"] = "no answer to three messages"
        p["unanswered"] = 0
    elif p["unanswered"] == 2:
        p = _widen(p, today, 2.0)
    return p


def after_reply(plan: dict[str, Any], today: str) -> dict[str, Any]:
    """They answered: the count of unanswered goes to zero, a pause ends, and the next touch is no later than a week away (the relationship is warm: keep it so)."""
    p = {**plan, "touches": [dict(t) for t in plan["touches"]], "unanswered": 0, "repliedOn": today}
    p.pop("pausedUntil", None)
    p.pop("pauseReason", None)
    nxt = next((t for t in p["touches"] if t["state"] == "planned"), None)
    if nxt is not None and date.fromisoformat(nxt["day"]) > date.fromisoformat(today) + timedelta(days=7):
        nxt["day"] = (date.fromisoformat(today) + timedelta(days=7)).isoformat()
    return p


def after_skip(plan: dict[str, Any], touch_n: int) -> dict[str, Any]:
    p = {**plan, "touches": [dict(t) for t in plan["touches"]]}
    for t in p["touches"]:
        if t["n"] == touch_n:
            t["state"] = "skipped"
    return p


def _widen(plan: dict[str, Any], today: str, factor: float) -> dict[str, Any]:
    """The planned touches after today are spread out by `factor`: someone who is slow to answer hears from us less often."""
    base = date.fromisoformat(today)
    for t in plan["touches"]:
        if t["state"] == "planned":
            gap = (date.fromisoformat(t["day"]) - base).days
            t["day"] = (base + timedelta(days=max(MIN_GAP_DAYS, int(round(gap * factor))))).isoformat()
    return plan


def may_send(plan: dict[str, Any], sent_last_30: int, today_count: int, cap: int = DAILY_CAP) -> str | None:
    """Why a touch must wait (in words), or None when it may go: the cap for the day across everyone, and the monthly cap for this person."""
    if today_count >= cap:
        return "that is already the most messages for one day"
    if sent_last_30 >= MONTHLY_CAP:
        return "this person has already had the most messages for a month"
    return None


def missing_details(contact: dict[str, Any]) -> list[str]:
    """What the owner still has to fill in before a plan starts: tags (or at least a sector), and where the contact is."""
    out = []
    if not (contact.get("tags") or (contact.get("profile") or {}).get("sector")):
        out.append("tags")
    if not (contact.get("location") or contact.get("timezone")):
        out.append("where they are")
    return out


def summary(plan: dict[str, Any]) -> dict[str, Any]:
    touches = plan.get("touches", [])
    nxt = next((t for t in touches if t["state"] == "planned"), None)
    return {"tier": plan.get("tier"), "status": plan.get("status"), "sent": sum(1 for t in touches if t["state"] == "sent"), "planned": sum(1 for t in touches if t["state"] == "planned"),
            "next": {"day": nxt["day"], "purpose": nxt["purpose"]} if nxt else None, "pausedUntil": plan.get("pausedUntil"), "unanswered": plan.get("unanswered", 0)}
