"""How a reply reads in a phone's chat: Telegram (HTML), WhatsApp (*bold*, _italic_) or plain text. Pure: no network, no framework.

A phone can't show the cards the web app does, and a phone's chat is read at a glance, so a briefing is kept short: a heading and a
one-line summary, then one line per thing (an icon for what it is, the time and the title, and at most one quiet line under it), a few
things per section, each thing once, and the free time at the end. Mostly words, with an icon where it saves one.
"""

from __future__ import annotations

import re
from datetime import date
from difflib import SequenceMatcher
from html import escape
from typing import Any, Literal

from app.domain.dayplan import SECTION_TITLES

Style = Literal["telegram", "whatsapp", "plain"]

SECTION_ICONS = {"fixed": "📌", "prepare": "📂", "calls": "📞", "work": "✅", "push": "🎯", "attention": "⚠️", "later": "🔭"}
#: How many things a section shows on a phone; the rest are counted, and are in the app.
SECTION_MAX = {"fixed": 4, "prepare": 3, "calls": 4, "work": 5, "push": 2, "attention": 2, "later": 2}
#: Free time shorter than this isn't worth listing, and only this many windows are.
FREE_MIN, FREE_MAX = 30, 2
TYPE_ICONS = {"hearing": "⚖️", "meeting": "🤝", "task": "☑️", "calendar": "🗓️"}
CHANNEL_ICONS = {"call": "📞", "phone": "📞", "whatsapp": "💬", "sms": "💬", "message": "💬", "text": "💬", "email": "✉️", "mail": "✉️", "visit": "🚶", "in person": "🚶"}
#: What is left out first, when a message is too long for the chat: more of what the first sections already say.
MORE = "…and more in Stiamo."

_BOLD = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")
_CODE = re.compile(r"`([^`\n]+)`")
_LINK = re.compile(r"\[([^\]\n]+)\]\((https?://[^)\s]+)\)")
_HEADING = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]+(.+?)[ \t]*#*[ \t]*$", re.M)
_BULLET = re.compile(r"^([ \t]*)[-*+][ \t]+", re.M)
_RULE = re.compile(r"^[ \t]*([-*_][ \t]*){3,}$", re.M)
_TAG = re.compile(r"<[^>]+>")


class Writer:
    """Writes emphasis the way one chat app reads it. Everything that goes in is plain text; everything that comes out is safe to send."""

    def __init__(self, style: Style = "plain") -> None:
        self.style = style

    def raw(self, s: str) -> str:
        """Plain text, escaped where the chat reads markup."""
        return escape(s, quote=False) if self.style == "telegram" else s

    def bold(self, s: str) -> str:
        return s if not s.strip() else {"telegram": f"<b>{s}</b>", "whatsapp": f"*{s}*"}.get(self.style, s)

    def italic(self, s: str) -> str:
        return s if not s.strip() else {"telegram": f"<i>{s}</i>", "whatsapp": f"_{s}_"}.get(self.style, s)

    def code(self, s: str) -> str:
        return f"<code>{s}</code>" if self.style == "telegram" else s

    def link(self, label: str, url: str) -> str:
        if self.style == "telegram":
            return f'<a href="{url.replace(chr(34), "%22")}">{label}</a>'
        return url if label == url else f"{label} ({url})"

    def text(self, s: str) -> str:
        """A model's answer, which may carry markdown, written for this chat: bold stays bold, headings become bold lines, `-` lists become bullets."""
        s = _RULE.sub("", s)
        s = _HEADING.sub(r"**\1**", s)
        s = _BULLET.sub(r"\1• ", s)
        s = self.raw(s)
        s = _BOLD.sub(lambda m: self.bold(m.group(1) or m.group(2)), s)
        s = _CODE.sub(lambda m: self.code(m.group(1)), s)
        s = _LINK.sub(lambda m: self.link(m.group(1), m.group(2)), s)
        return re.sub(r"\n{3,}", "\n\n", s).strip()


def strip_markup(s: str) -> str:
    """What a message says without Telegram's tags, for when Telegram won't take the markup."""
    from html import unescape

    return unescape(_TAG.sub("", s))


def _day(d: str | None) -> str:
    try:
        x = date.fromisoformat(d or "")
    except ValueError:
        return d or ""
    return f"{x.strftime('%a')} {x.day} {x.strftime('%b')}"


def _short(d: str | None) -> str:
    try:
        x = date.fromisoformat(d or "")
    except ValueError:
        return d or ""
    return f"{x.day} {x.strftime('%b')}"


def _similar(a: str, b: str) -> bool:
    a, b = a.strip().lower(), b.strip().lower()
    return bool(a and b) and (a == b or b in a or a in b or SequenceMatcher(None, a, b).ratio() > 0.6)


_CASE_NOTE = re.compile(r"\s*\([^()]*[A-Z]{2,6}-\d{4}-\d{3}[^()]*\)")
_NUMBER = re.compile(r"\+?\d[\d ()-]{7,}\d")
_AT_TIME = re.compile(r"at \d{1,2}:\d{2}")
_BECAUSE = re.compile(r"\s+(?:so|because)\s+", re.I)
#: A headline that begins with the day ("Fri 13 Nov (S&B): …") when the heading already says it.
_LEAD_DAY = re.compile(r"^\s*(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\w*,?\s+\d{1,2}\s+[A-Za-z]{3,9}\s*(?:\([^)]*\))?\s*[:—–-]\s*(?=\S)")


def clip(text: str | None, n: int) -> str:
    """At most n characters, cut at a word and marked with an ellipsis."""
    t = " ".join((text or "").split())
    if len(t) <= n:
        return t
    cut = t[: n - 1].rsplit(" ", 1)[0] or t[: n - 1]
    return cut.rstrip(" ,;:—-") + "…"


def _why(note: str | None, title: str) -> str:
    """The model's note often begins by saying the title again ("Call him — last try 29 Oct"): keep only what it adds, short."""
    note = _CASE_NOTE.sub("", (note or "").strip())
    if not note:
        return ""
    head, sep, tail = note.partition(" — ")
    if sep and (_similar(head, title) or len(head) > 40 or _AT_TIME.search(head)):
        note = tail.strip()  # "Call him at 10:30 — he asked for a callback": the reason is what the line adds
    elif _similar(note, title):
        return ""
    note = _BECAUSE.split(note, maxsplit=1)[0].strip()  # "Review the file so everything is in hand…": the instruction is enough
    return clip(note[:1].upper() + note[1:], 60) if note else ""


def _number(contact: Any) -> str | None:
    """The phone number in a contact line ("Jaipal Pawar Pune Work · +91 99706 60979"): that is what is worth showing under a call."""
    m = _NUMBER.search(str(contact or ""))
    return re.sub(r"\s+", " ", m.group(0)).strip() if m else None


def _detail(it: dict[str, Any], day: str) -> list[str]:
    """The one or two facts that matter for this kind of thing: the number for a call, where for a hearing or a meeting."""
    kind, out = it.get("type"), []
    if kind == "followUp":
        if n := _number(it.get("contact")):
            out.append(n)
        if (it.get("tries") or 0) >= 2:
            out.append(f"tried {it['tries']}×")
    elif kind == "hearing":
        out += [str(x) for x in (it.get("caseRef"), it.get("place")) if x]
    elif kind in ("meeting", "calendar"):
        if it.get("place"):
            out.append(clip(str(it["place"]), 30))
    elif it.get("due") and it["due"] != day:
        out.append(f"due {_short(it['due'])}")
    return out


def _icon(it: dict[str, Any]) -> str:
    if it.get("type") == "followUp":
        return CHANNEL_ICONS.get(str(it.get("channel") or "").lower(), "📞")
    return TYPE_ICONS.get(str(it.get("type")), "▫️")


def _start(time: Any) -> str:
    """A span ("10:30–10:45") as when it starts: the end of a short call or a hearing isn't what a glance is for."""
    return re.split(r"[–—-]", str(time))[0].strip()


def _item(it: dict[str, Any], w: Writer, day: str) -> list[str]:
    if it.get("type") == "note":
        return [f"💡 {w.italic(w.raw(clip(str(it.get('note') or ''), 90)))}"]
    title = clip(str(it.get("title") or ""), 60)
    flag = "❗" if it.get("priority") in ("high", "urgent") else ""
    time = it.get("time")
    first = f"{_icon(it)} {w.bold(w.raw(_start(time)))} {flag}{w.raw(title)}" if time else f"{_icon(it)} {flag}{w.bold(w.raw(title))}"
    under = [x for x in (_why(it.get("_prep") or it.get("note"), title), *_detail(it, day)) if x]
    return [first, f"   ↳ {w.italic(w.raw(' · '.join(under)))}"] if under else [first]


def _merge(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each thing once. The planner puts a hearing under Fixed and again under Prepare; on a phone it is one line, with what to prepare under it."""
    seen: dict[tuple[Any, Any], dict[str, Any]] = {}
    out: list[dict[str, Any]] = []
    for s in sections:
        kept: list[dict[str, Any]] = []
        for it in s.get("items", []):
            if it.get("type") == "note":
                kept.append(it)
                continue
            key = (it.get("type"), it.get("id") or (it.get("title"), it.get("time")))
            if key in seen:
                if it.get("note") and not seen[key].get("_prep"):
                    seen[key]["_prep"] = it["note"]
                continue
            seen[key] = dict(it)
            kept.append(seen[key])
        out.append({**s, "items": kept})
    return out


def _section(s: dict[str, Any], w: Writer, day: str) -> str:
    kind = str(s.get("kind"))
    title = clip(str(s.get("title") or SECTION_TITLES.get(kind, "")), 28)
    items = s.get("items", [])
    room = SECTION_MAX.get(kind, 4)
    lines = [f"{SECTION_ICONS.get(kind, '▫️')} {w.bold(w.raw(title))}"]
    for it in items[:room]:
        lines += _item(it, w, day)
    if len(items) > room:
        lines.append(w.italic(w.raw(f"+{len(items) - room} more in Stiamo")))
    return "\n".join(lines)


def _free(card: dict[str, Any], w: Writer) -> str:
    windows = [x for x in (card.get("free") or []) if (x.get("minutes") or 0) >= FREE_MIN]
    if not windows:
        return ""
    best = sorted(sorted(windows, key=lambda x: x.get("minutes") or 0, reverse=True)[:FREE_MAX], key=lambda x: x["from"])
    shown = ", ".join(x["from"] + "–" + x["to"] for x in best)
    return f"🟢 {w.bold('Free')} {w.raw(shown)}"


def render(answer: str, found: list[dict[str, Any]], limit: int, agent: str | None = None, style: Style = "plain", title: str | None = None) -> str:
    """An answer and the cards it found, written for a chat and signed with the agent that gave it. `title` heads a briefing ("Plan for")."""
    w = Writer(style)
    say = w.text(answer) if answer and answer.strip() else ""
    blocks: list[tuple[str, bool]] = []  # (text, may be left out when the message is too long)
    briefing = next((f for f in found if f.get("kind") == "briefing"), None)
    reminder = next((f for f in found if f.get("kind") == "reminder"), None)
    if briefing:
        day = str(briefing.get("date") or "")
        head = [f"🗓️ {w.bold(w.raw(((title + ' ') if title else '') + _day(day)))}"]
        headline = _LEAD_DAY.sub("", str(briefing.get("headline") or "")).strip()
        headline = headline[:1].upper() + headline[1:]
        if headline:
            head.append(w.raw(clip(headline, 110)))
        blocks.append(("\n".join(head), False))
        # The model's own paragraph usually says again what the sections show; it stays only when there is no headline, or it adds a short line.
        lead = say or (w.text(str(briefing["summary"])) if briefing.get("summary") else "")
        if lead and (not headline or (len(lead) <= 140 and not _similar(lead, headline))):
            blocks.append((lead, False))
        for s in _merge(briefing.get("sections", [])):
            if s.get("items"):
                blocks.append((_section(s, w, day), True))
        if _free(briefing, w):
            blocks.append((_free(briefing, w), False))
    elif reminder:
        day = str(reminder.get("date") or "")
        blocks.append((f"🔔 {w.bold(w.raw(str(reminder.get('headline') or 'Pending')))}", False))
        blocks.append(("\n".join(line for it in reminder.get("items", []) for line in _item(it, w, day)), False))
        if reminder.get("footer"):
            blocks.append((w.italic(w.raw(str(reminder["footer"]))), False))
    else:
        blocks.append((say or w.raw("I found nothing to say about that."), False))
    cases = [f for f in found if f.get("kind") == "case"]
    if cases:
        lines = [f"📁 {w.bold('Cases')}"]
        for c in cases[:8]:
            nxt = c.get("nextHearing")
            name = f"{c.get('reference', '')} {c.get('title', '')}".strip()
            lines.append(f"• {w.bold(w.raw(name))}" + (f" · {w.raw('next hearing ' + _short(nxt.get('date')))}" if nxt else ""))
        blocks.append(("\n".join(lines), True))
    docs = [f for f in found if f.get("kind") == "document"]
    if docs:
        lines = [f"📄 {w.bold('Documents')}"]
        for d in docs[:8]:
            lines.append(f"• {w.raw(str(d.get('name', '')))}" + (f"\n  {w.raw(str(d['webViewLink']))}" if d.get("webViewLink") else ""))
        blocks.append(("\n".join(lines), True))
    proposals = [f for f in found if f.get("kind") == "proposal"]
    if proposals:
        cards = ["\n".join([f"📝 {w.bold(w.raw(clip(str(x['title']), 80)))}", *(f"   ↳ {w.raw(clip(str(ln), 90))}" for ln in x.get("lines", [])[:3])]) for x in proposals[:6]]
        blocks.append(("\n\n".join(cards) + f"\n\n👉 {w.bold('YES')} to save · {w.bold('NO')} to drop", False))
    for s in [f for f in found if f.get("kind") == "schedule"][:1]:
        lines = [f"🗓️ {w.bold('Proposed times')}"] + [f"• {w.bold(w.raw(str(b['from'])))} {w.raw(clip(str(b['title']), 60))}" + (f" · 📞 {w.raw(str(b['phone']))}" if b.get("phone") else "") for b in s.get("blocks", [])]
        if s.get("buttons"):
            lines.append(f"👉 Tap {w.bold('Approve')} to book them.")
        else:
            lines.append(f"👉 {w.bold('YES')} to book them · {w.bold('NO')} to drop" if s.get("proposalId") else "👉 Approve it in Stiamo (Needs me).")
        blocks.append(("\n".join(lines), False))
    # A plan comes on the planner's own bot, so it needs no signature; an answer from the master assistant says which agent gave it.
    tail = [w.italic(w.raw(f"— {agent}"))] if agent and not title else []
    return _fit(blocks, tail, limit, w)


def _fit(blocks: list[tuple[str, bool]], tail: list[str], limit: int, w: Writer) -> str:
    """The message as long as the chat allows: whole sections are left out from the end, never cut through a tag."""
    more = ""
    kept = list(blocks)
    while True:
        text = "\n\n".join([t for t, _ in kept] + ([more] if more else []) + tail)
        if len(text) <= limit:
            return text
        last = next((i for i in range(len(kept) - 1, -1, -1) if kept[i][1]), None)
        if last is None:
            break
        kept.pop(last)
        more = w.italic(w.raw(MORE))
    lines = text.split("\n")
    while len("\n".join(lines)) > limit and len(lines) > 1:
        lines.pop()
    cut = "\n".join(lines)
    if len(cut) > limit:
        cut = (strip_markup(cut) if w.style == "telegram" else cut)[: limit - 1].rstrip() + "…"
    return cut
