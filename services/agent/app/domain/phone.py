"""How a reply reads in a phone's chat: Telegram (HTML), WhatsApp (*bold*, _italic_) or plain text. Pure: no network, no framework.

A phone can't show the cards the web app does, so a briefing is laid out as a short message: the day, one line for each thing (an icon for
what it is, the time or the title in bold, a quieter line under it for the why and the where), and the free time at the end.
"""

from __future__ import annotations

import re
from datetime import date
from difflib import SequenceMatcher
from html import escape
from typing import Any, Literal

from app.domain.dayplan import SECTION_TITLES

Style = Literal["telegram", "whatsapp", "plain"]

SECTION_ICONS = {"fixed": "📌", "prepare": "📂", "calls": "📞", "work": "✅", "attention": "⚠️", "later": "🔭"}
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


def _why(note: str | None, title: str) -> str:
    """The model's note often begins by saying the title again ("Call him — last try 29 Oct"): keep only what it adds."""
    note = (note or "").strip()
    if not note:
        return ""
    head, sep, tail = note.partition(" — ")
    if sep and _similar(head, title):
        note = tail.strip()
    elif _similar(note, title):
        return ""
    return note[:1].upper() + note[1:] if note else ""


def _detail(it: dict[str, Any], day: str) -> list[str]:
    out: list[str] = []
    if it.get("caseRef"):
        out.append(str(it["caseRef"]))
    if it.get("place"):
        out.append(str(it["place"]))
    if it.get("contact"):
        out.append(str(it["contact"]))  # how it is reached is the icon
    if it.get("tries"):
        out.append(f"tried {it['tries']}×")
    if it.get("minutes"):
        out.append(f"{it['minutes']} min")
    if it.get("due") and it["due"] != day:
        out.append(f"due {_short(it['due'])}")
    if it.get("priority") in ("high", "urgent"):
        out.append(f"{it['priority']} priority")
    return out


def _icon(it: dict[str, Any]) -> str:
    if it.get("type") == "followUp":
        return CHANNEL_ICONS.get(str(it.get("channel") or "").lower(), "📞")
    return TYPE_ICONS.get(str(it.get("type")), "▫️")


def _item(it: dict[str, Any], w: Writer, day: str) -> list[str]:
    if it.get("type") == "note":
        return [f"💡 {w.italic(w.raw(str(it.get('note') or '')))}"]
    title = str(it.get("title") or "")
    time = it.get("time")
    first = f"{_icon(it)} {w.bold(w.raw(str(time)))} {w.raw(title)}" if time else f"{_icon(it)} {w.bold(w.raw(title))}"
    under = [x for x in (_why(it.get("note"), title), *_detail(it, day)) if x]
    return [first, f"   ↳ {w.italic(w.raw(' · '.join(under)))}"] if under else [first]


def _section(s: dict[str, Any], w: Writer, day: str) -> str:
    kind = str(s.get("kind"))
    title = str(s.get("title") or SECTION_TITLES.get(kind, ""))
    lines = [f"{SECTION_ICONS.get(kind, '▫️')} {w.bold(w.raw(title))}"]
    for it in s.get("items", []):
        lines += _item(it, w, day)
    return "\n".join(lines)


def _free(card: dict[str, Any], w: Writer) -> str:
    windows = card.get("free") or []
    if not windows:
        return ""
    shown = ", ".join(f"{x['from']}–{x['to']}" for x in windows[:4])
    return f"🟢 {w.bold('Free')} {w.raw(shown)}" + ("" if len(windows) <= 4 else w.raw(f" and {len(windows) - 4} more"))


def render(answer: str, found: list[dict[str, Any]], limit: int, agent: str | None = None, style: Style = "plain", title: str | None = None) -> str:
    """An answer and the cards it found, written for a chat and signed with the agent that gave it. `title` heads a briefing ("Plan for")."""
    w = Writer(style)
    say = w.text(answer) if answer and answer.strip() else ""
    blocks: list[tuple[str, bool]] = []  # (text, may be left out when the message is too long)
    briefing = next((f for f in found if f.get("kind") == "briefing"), None)
    if briefing:
        day = str(briefing.get("date") or "")
        head = [f"🗓️ {w.bold(w.raw(((title + ' ') if title else '') + _day(day)))}"]
        if briefing.get("headline"):
            head.append(w.raw(str(briefing["headline"]).strip()))
        blocks.append(("\n".join(head), False))
        lead = say or (w.text(str(briefing["summary"])) if briefing.get("summary") else "")
        if lead:
            blocks.append((lead, False))
        for s in briefing.get("sections", []):
            if s.get("items"):
                blocks.append((_section(s, w, day), True))
        if _free(briefing, w):
            blocks.append((_free(briefing, w), False))
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
        cards = ["\n".join([f"📝 {w.bold(w.raw(str(x['title'])))}", *(f"   ↳ {w.raw(str(ln))}" for ln in x.get("lines", []))]) for x in proposals[:6]]
        blocks.append(("\n\n".join(cards) + f"\n\n👉 Reply {w.bold('YES')} to save, or {w.bold('NO')} to drop.", False))
    for s in [f for f in found if f.get("kind") == "schedule"][:1]:
        lines = [f"🗓️ {w.bold('Proposed schedule')}"] + [f"• {w.bold(w.raw(str(b['from'])))} {w.raw(str(b['title']))}" for b in s.get("blocks", [])]
        lines.append(f"👉 Reply {w.bold('YES')} to put these on your planner, or {w.bold('NO')} to drop." if s.get("proposalId") else "👉 Approve it in Stiamo (Attention) and it goes on your planner.")
        blocks.append(("\n".join(lines), False))
    tail = [w.italic(w.raw(f"— {agent}"))] if agent else []
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
