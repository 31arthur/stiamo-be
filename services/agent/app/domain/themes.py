"""Organisation themes: how a reply sounds and looks when it goes to a particular organisation. Pure: no network, no framework.

A theme says who it is for (by address, domain or words in the mail), how replies are written (the tone and language, the greeting and sign-off),
and how they look (a letterhead, an accent colour, a signature and footer). It is kept in the person's settings and used when an agent drafts and
sends a reply.
"""

from __future__ import annotations

import re
import uuid
from html import escape
from typing import Any

from stiamo_common.errors import ValidationFailed

MAX_THEMES = 30
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
DEFAULT_ACCENT = "#1f4e79"


def _short(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _words(values: Any, *, lower: bool = True, limit: int = 50) -> list[str]:
    out: dict[str, None] = {}
    for v in values if isinstance(values, list) else []:
        s = str(v).strip()
        s = s.lower() if lower else s
        if s:
            out[s[:120]] = None
    return list(out)[:limit]


def clean_theme(raw: dict[str, Any]) -> dict[str, Any]:
    """A theme as it is kept: every field checked and trimmed, the id made if it has none. Raises with what to fix."""
    name = _short(raw.get("name"), 60)
    if not name:
        raise ValidationFailed("Give the theme a name, like the organisation it is for.")
    accent = _short(raw.get("accent"), 7) or DEFAULT_ACCENT
    if not _HEX.match(accent):
        raise ValidationFailed("The accent colour is a hex colour like #1f4e79.")
    match = raw.get("match") if isinstance(raw.get("match"), dict) else {}
    head = raw.get("letterhead") if isinstance(raw.get("letterhead"), dict) else {}
    return {
        "id": _short(raw.get("id"), 40) or uuid.uuid4().hex[:10],
        "name": name,
        "emoji": _short(raw.get("emoji"), 8) or "🏛️",
        "accent": accent.lower(),
        "default": bool(raw.get("default")),
        "match": {
            "domains": [d.lstrip("@").removeprefix("*.") for d in _words(match.get("domains"))],
            "addresses": [a for a in _words(match.get("addresses")) if "@" in a],
            "phrases": _words(match.get("phrases")),
        },
        "tone": _short(raw.get("tone"), 1000),
        "language": _short(raw.get("language"), 40) or "English",
        "greeting": _short(raw.get("greeting"), 120) or "Dear {name},",
        "signoff": _short(raw.get("signoff"), 80) or "Yours sincerely,",
        "signature": str(raw.get("signature") or "").strip()[:600],
        "letterhead": {"title": _short(head.get("title"), 120), "tagline": _short(head.get("tagline"), 160), "footer": str(head.get("footer") or "").strip()[:400]},
    }


def clean_themes(raw: Any) -> list[dict[str, Any]]:
    """The whole set, with at most one default and no two themes sharing an id."""
    if not isinstance(raw, list) or len(raw) > MAX_THEMES:
        raise ValidationFailed(f"Keep up to {MAX_THEMES} themes.")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw:
        t = clean_theme(item if isinstance(item, dict) else {})
        if t["id"] in seen:
            t["id"] = uuid.uuid4().hex[:10]
        seen.add(t["id"])
        out.append(t)
    first_default = next((i for i, t in enumerate(out) if t["default"]), None)
    return [{**t, "default": i == first_default} for i, t in enumerate(out)]


def pick_theme(themes: list[dict[str, Any]], address: str, subject: str = "", text: str = "") -> dict[str, Any] | None:
    """The theme for a mail: one named for the sender's address, then one for the sender's domain, then one for words in the mail, then the
    default. None when there is no theme that applies (the reply is then plain)."""
    address = address.strip().lower()
    domain = address.rsplit("@", 1)[-1] if "@" in address else ""
    for t in themes:
        if address and address in t["match"]["addresses"]:
            return t
    for t in themes:
        if domain and any(domain == d or domain.endswith("." + d) for d in t["match"]["domains"]):
            return t
    haystack = f"{subject} {text}".lower()
    for t in themes:
        if any(re.search(r"(?<!\w)" + re.escape(p) + r"(?!\w)", haystack) for p in t["match"]["phrases"]):
            return t
    return next((t for t in themes if t.get("default")), None)


def first_name(full: str) -> str:
    """What to call someone in a greeting: the first word of their name, or nothing when it is just an address."""
    full = full.strip().strip('"')
    if not full or "@" in full:
        return ""
    return full.split()[0].strip(",") if full.split() else ""


def greeting_for(theme: dict[str, Any] | None, name: str) -> str:
    template = (theme or {}).get("greeting") or "Dear {name},"
    return template.replace("{name}", name or "Sir/Madam")


def render_email(theme: dict[str, Any] | None, body: str, name: str) -> tuple[str, str]:
    """A reply as plain text and as a page: the greeting, the body, the sign-off and signature, and the footer, in the theme's letterhead."""
    t = theme or {}
    head = t.get("letterhead") or {}
    greeting, signoff, signature, footer = greeting_for(theme, name), t.get("signoff") or "Regards,", t.get("signature") or "", head.get("footer") or ""
    body = body.strip()
    text_parts = [greeting, "", body, "", signoff]
    if signature:
        text_parts.append(signature)
    if footer:
        text_parts += ["", "--", footer]
    text = "\n".join(text_parts)
    if not theme:
        return text, ""
    accent = t.get("accent") or DEFAULT_ACCENT
    para = lambda s: "".join(f'<p style="margin:0 0 14px">{escape(p).replace(chr(10), "<br>")}</p>' for p in s.strip().split("\n\n") if p.strip())  # noqa: E731
    letterhead = ""
    if head.get("title") or head.get("tagline"):
        letterhead = (
            f'<div style="padding:18px 28px;border-bottom:3px solid {accent}">'
            f'<div style="font-size:20px;font-weight:700;color:{accent};letter-spacing:.2px">{escape(head.get("title") or "")}</div>'
            + (f'<div style="font-size:12px;color:#6b7280;margin-top:2px">{escape(head["tagline"])}</div>' if head.get("tagline") else "")
            + "</div>"
        )
    sig = f'<div style="margin-top:6px">{escape(signoff)}<br><strong>{escape(signature).replace(chr(10), "<br>")}</strong></div>' if signature else f'<div style="margin-top:6px">{escape(signoff)}</div>'
    foot = f'<div style="padding:14px 28px;border-top:1px solid #e5e7eb;font-size:11px;color:#6b7280;line-height:1.5">{escape(footer).replace(chr(10), "<br>")}</div>' if footer else ""
    html = (
        '<div style="background:#f3f4f6;padding:16px;font-family:Georgia,\'Times New Roman\',serif">'
        '<div style="max-width:640px;margin:0 auto;background:#ffffff;border-top:5px solid ' + accent + ';border-radius:4px;overflow:hidden">'
        + letterhead
        + '<div style="padding:24px 28px;font-size:15px;line-height:1.6;color:#111827">'
        + f'<p style="margin:0 0 14px">{escape(greeting)}</p>'
        + para(body)
        + sig
        + "</div>"
        + foot
        + "</div></div>"
    )
    return text, html
