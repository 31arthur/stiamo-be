"""Insights: what the agents' mail and news decisions and the practice's own records say when read together. Pure: no network, no framework.

It only counts and connects what is already kept (the ledger of every mail and news agent, the tasks, follow-ups, meetings, hearings and
cases): no model is asked anything here. The written summary that sits on top of these numbers is made separately (application/insights.py).
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.domain.workflows import rank_news

NEEDS = ("queued", "event_proposed")
_FROM = re.compile(r"^From:\s*(.*?)\s*<?([\w.+'-]+@[\w-]+(?:\.[\w-]+)+)>?\s*$", re.MULTILINE)
_WORD = re.compile(r"[A-Za-z][A-Za-z'’-]{3,}")
STOP = set(
    "about after again against also amid among around because been before being between both could does during each from have into just like made make many more most much over said says should since some still such than that their them then there these they this those through under until very were what when where which while will with within without would your news report reports india indian indians says say new "
    "year years week today after over first last more than".split()
)


def _when(iso: str | None, tz: ZoneInfo) -> datetime | None:
    try:
        d = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return d.astimezone(tz) if d.tzinfo else d.replace(tzinfo=tz)


def sender_of(excerpt: str) -> tuple[str, str]:
    """(name, address) of the sender, from the "From:" line the agents keep with each email."""
    m = _FROM.search(excerpt or "")
    return ((m.group(1) or "").strip(' "'), m.group(2).lower()) if m else ("", "")


def days_back(today: str, days: int) -> list[str]:
    end = date.fromisoformat(today)
    return [(end - timedelta(days=i)).isoformat() for i in range(days - 1, -1, -1)]


def topics(titles: list[str], limit: int = 8) -> list[dict[str, Any]]:
    """The words that come up in more than one story: what the news has been about, in plain counts."""
    seen: Counter[str] = Counter()
    for t in titles:
        seen.update({w.lower().strip("'’-") for w in _WORD.findall(t)} - STOP)
    return [{"term": w, "count": n} for w, n in seen.most_common(limit * 2) if n >= 2][:limit]


# --- mail ---------------------------------------------------------------------------------------------------


def mail_block(entries: list[dict[str, Any]], today: str, days: int, tz: ZoneInfo) -> dict[str, Any]:
    per = {d: {"date": d, "urgent": 0, "attention": 0, "other": 0} for d in days_back(today, days)}
    senders: dict[str, dict[str, Any]] = {}
    domains: Counter[str] = Counter()
    needs = urgent = cleared = events = 0
    due_soon: list[dict[str, Any]] = []
    horizon = (date.fromisoformat(today) + timedelta(days=7)).isoformat()
    for e in entries:
        when = _when(e["created_at"], tz)
        day = when.date().isoformat() if when else None
        data = e.get("data") or {}
        is_needs = e["status"] in NEEDS
        high = is_needs and data.get("priority") == "high"
        bucket = "urgent" if high else "attention" if is_needs else "other"
        if day in per:
            per[day][bucket] += 1
        needs += is_needs
        urgent += high
        cleared += e["status"] == "applied"
        events += e["status"] == "event_proposed"
        name, addr = sender_of(e.get("excerpt", ""))
        if addr and e["status"] != "applied":
            s = senders.setdefault(addr, {"name": name or addr, "address": addr, "count": 0, "urgent": 0})
            s["count"] += 1
            s["urgent"] += high
            domains[addr.rsplit("@", 1)[-1]] += 1
        due = str(data.get("due") or "")
        if is_needs and due and today <= due <= horizon:
            due_soon.append({"title": e["title"], "due": due, "priority": data.get("priority") or "normal", "summary": data.get("summary") or "", "action": data.get("action"), "agentId": e["agent_id"], "itemId": e["item_id"]})
    top = sorted(senders.values(), key=lambda s: (-s["urgent"], -s["count"], s["name"].lower()))[:6]
    return {
        "total": len(entries), "needsYou": needs, "urgent": urgent, "cleared": cleared, "events": events,
        "perDay": list(per.values()), "topSenders": top, "topDomains": [{"domain": d, "count": n} for d, n in domains.most_common(5)],
        "dueSoon": sorted(due_soon, key=lambda x: (x["due"], x["priority"] != "high"))[:8],
    }


# --- news ---------------------------------------------------------------------------------------------------


def news_block(entries: list[dict[str, Any]], today: str, days: int, tz: ZoneInfo, now: datetime) -> dict[str, Any]:
    per = {d: {"date": d, "count": 0} for d in days_back(today, days)}
    levels: Counter[str] = Counter()
    sources: Counter[str] = Counter()
    stories = []
    for e in entries:
        data = e.get("data") or {}
        when = _when(e["created_at"], tz)
        if when and when.date().isoformat() in per:
            per[when.date().isoformat()]["count"] += 1
        levels[str(data.get("importance") or "unrated")] += 1
        if data.get("source"):
            sources[str(data["source"])] += 1
        stories.append((rank_news(data, now), e, data))
    stories.sort(key=lambda s: -s[0])
    return {
        "total": len(entries), "perDay": list(per.values()), "byImportance": [{"level": k, "count": v} for k, v in levels.most_common()],
        "topSources": [{"source": k, "count": v} for k, v in sources.most_common(5)], "topics": topics([e["title"] for e in entries]),
        "top": [{"title": e["title"], "source": d.get("source"), "link": d.get("link"), "importance": d.get("importance"), "publishedAt": d.get("publishedAt")} for _, e, d in stories[:5]],
    }


# --- the practice's own work --------------------------------------------------------------------------------


def _open(r: dict[str, Any]) -> bool:
    return (r.get("outcome") or {}).get("status", "open") == "open"


def work_block(tasks: list[dict[str, Any]], follow_ups: list[dict[str, Any]], meetings: list[dict[str, Any]], hearings: list[dict[str, Any]], today: str, days: int, tz: ZoneInfo) -> dict[str, Any]:
    since = (date.fromisoformat(today) - timedelta(days=days - 1)).isoformat()
    week = (date.fromisoformat(today) + timedelta(days=6)).isoformat()
    t_open = [t for t in tasks if _open(t)]
    overdue_t = [t for t in t_open if t.get("dueDate") and t["dueDate"] < today and not ((t.get("slot") or {}).get("date", "") >= today)]
    done_t = [t for t in tasks if (t.get("outcome") or {}).get("status") == "done" and str((t.get("outcome") or {}).get("recordedAt") or "")[:10] >= since]
    due_t = [t for t in t_open if t.get("dueDate") and today <= t["dueDate"] <= week]
    f_open = [f for f in follow_ups if _open(f)]
    overdue_f = [f for f in f_open if (f.get("due") or {}).get("date") and f["due"]["date"] < today]
    tries = [a for f in follow_ups for a in (f.get("attempts") or []) if str(a.get("date") or "") >= since]
    reached = sum(1 for a in tries if a.get("result") == "reached")
    stuck = [f for f in f_open if len(f.get("attempts") or []) >= 3]
    load = {d: 0 for d in days_back(today, 1)}
    for i in range(7):
        load[(date.fromisoformat(today) + timedelta(days=i)).isoformat()] = 0
    for t in t_open:
        d = (t.get("slot") or {}).get("date") or t.get("dueDate")
        if d in load:
            load[d] += int(t.get("estimate") or 30)
    for m in meetings:
        slot = m.get("slot") or {}
        if _open(m) and slot.get("date") in load:
            load[slot["date"]] += int(slot.get("duration") or 30)
    for h in hearings:
        if _open(h) and h.get("date") in load:
            load[h["date"]] += int(h.get("duration") or 60)
    upcoming_h = sorted((h for h in hearings if _open(h) and today <= str(h.get("date") or "") <= (date.fromisoformat(today) + timedelta(days=30)).isoformat()), key=lambda h: (h["date"], h.get("time") or 0))
    done_n, open_n = len(done_t), len(t_open)
    return {
        "tasks": {"open": open_n, "overdue": len(overdue_t), "dueThisWeek": len(due_t), "done": done_n, "doneRate": round(done_n / (done_n + open_n), 2) if done_n + open_n else None, "overdueList": [{"id": t["id"], "title": t.get("title"), "dueDate": t.get("dueDate")} for t in sorted(overdue_t, key=lambda t: t["dueDate"])[:5]]},
        "followUps": {"open": len(f_open), "overdue": len(overdue_f), "tries": len(tries), "reached": reached, "reachRate": round(reached / len(tries), 2) if tries else None, "stuck": [{"id": f["id"], "title": f.get("title"), "tries": len(f.get("attempts") or [])} for f in stuck[:5]]},
        "meetings": {"upcoming": sum(1 for m in meetings if _open(m) and today <= str((m.get("slot") or {}).get("date") or "") <= week)},
        "hearings": {"upcoming": len(upcoming_h), "next": [{"id": h["id"], "date": h["date"], "caseId": h.get("caseId"), "purpose": h.get("purpose")} for h in upcoming_h[:3]]},
        "load": [{"date": d, "minutes": m} for d, m in sorted(load.items()) if d >= today][:7],
    }


# --- connecting them ----------------------------------------------------------------------------------------


def connections(mail: list[dict[str, Any]], news: list[dict[str, Any]], cases: list[dict[str, Any]], hearings: list[dict[str, Any]], today: str) -> list[dict[str, Any]]:
    """Where mail, news and the practice's cases touch: mail that names a case (and when it is next in court), and news that names someone a case is about."""
    next_hearing: dict[str, str] = {}
    for h in sorted((h for h in hearings if _open(h) and str(h.get("date") or "") >= today), key=lambda h: h["date"]):
        next_hearing.setdefault(str(h.get("caseId")), h["date"])
    out: list[dict[str, Any]] = []
    for c in cases:
        ref = str(c.get("reference") or "")
        title = str(c.get("title") or "")
        hearing = next_hearing.get(c["id"])
        if len(ref) >= 5:
            for e in mail:
                text = f"{e['title']} {(e.get('data') or {}).get('summary', '')} {e.get('excerpt', '')}"
                if ref.lower() in text.lower():
                    out.append({"kind": "mail-case", "text": f"Mail “{e['title']}” is about {ref}" + (f", next in court {hearing}" if hearing else ""), "ref": ref, "caseId": c["id"], "caseTitle": title, "agentId": e["agent_id"], "itemId": e["item_id"], "hearing": hearing})
        for who in {str(c.get(k) or "").strip() for k in ("opposingParty", "client")}:
            if len(who) < 4:
                continue
            pat = re.compile(r"(?<!\w)" + re.escape(who) + r"(?!\w)", re.IGNORECASE)
            for e in news:
                if pat.search(e["title"]):
                    out.append({"kind": "news-party", "text": f"News names {who}, in {ref or title}: “{e['title']}”", "ref": ref, "caseId": c["id"], "caseTitle": title, "link": (e.get("data") or {}).get("link"), "hearing": hearing})
    seen: set[str] = set()
    unique = []
    for c in out:
        if c["text"] not in seen:
            seen.add(c["text"])
            unique.append(c)
    return unique[:8]
