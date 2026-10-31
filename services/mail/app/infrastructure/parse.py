"""Turning raw RFC 822 mail into the shapes the screen shows: people, text, safe HTML, attachments.

Nothing here touches the network, so it is tested on plain bytes. Email is hostile input: the HTML is stripped of scripts, forms
and frames before it leaves this module, and what remains is shown in a sandboxed frame by the client as a second layer.
"""

from __future__ import annotations

import base64
import re
from datetime import UTC, datetime
from email import policy
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import parseaddr

import nh3

from app.domain.mail import Attachment, Message, MessageSummary, Person

_TAGS = {
    "a", "abbr", "b", "blockquote", "br", "caption", "center", "cite", "code", "col", "colgroup", "dd", "div", "dl", "dt", "em", "font",
    "h1", "h2", "h3", "h4", "h5", "h6", "hr", "i", "img", "li", "ol", "p", "pre", "q", "s", "small", "span", "strike", "strong", "sub",
    "sup", "table", "tbody", "td", "tfoot", "th", "thead", "tr", "u", "ul",
}  # fmt: skip
_COMMON = {"style", "class", "dir", "lang", "title", "align", "valign", "width", "height", "bgcolor", "color", "border", "cellpadding", "cellspacing"}
_ATTRS = {"*": _COMMON, "a": {"href"}, "img": {"src", "alt"}, "td": {"colspan", "rowspan"}, "th": {"colspan", "rowspan"}, "font": {"face", "size"}}
_SNIPPET = 160
_INLINE_LIMIT = 4_000_000  # of inline images turned into data: URLs, per message
_CID = re.compile(r"""(["'(])cid:([^"')\s]+)""", re.IGNORECASE)
_REMOTE = re.compile(r"""(?:src|url\()\s*=?\s*["']?\s*https?://""", re.IGNORECASE)


def parse(raw: bytes) -> EmailMessage:
    """Reads a message leniently: a partial or slightly broken one still gives whatever it can."""
    return BytesParser(policy=policy.default).parsebytes(raw)  # type: ignore[return-value]


def _person(value) -> Person:
    name, address = parseaddr(str(value or ""))
    return Person(name=name.strip(), address=address.strip())


def _people(value) -> list[Person]:
    if not value:
        return []
    try:
        return [Person(name=a.display_name.strip(), address=a.addr_spec) for a in value.addresses]
    except Exception:  # an address header that can't be read
        return [p for p in (_person(part) for part in str(value).split(",")) if p.address]


def _date(msg: EmailMessage) -> datetime | None:
    try:
        dt = msg["date"].datetime if msg["date"] else None
    except Exception:
        return None
    if dt is None:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _decode(part) -> str:
    """The text of a part, even when its declared charset is wrong or unknown."""
    try:
        return part.get_content()
    except Exception:
        payload = part.get_payload(decode=True) or b""
        try:
            return payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        except LookupError:  # a charset name nobody has heard of
            return payload.decode("latin-1")


def _bodies(msg: EmailMessage) -> tuple[str, str]:
    """(plain text, html), preferring what the sender marked as the body rather than as an attachment."""
    text = html = ""
    for part in msg.walk():
        if part.is_multipart() or part.get_content_disposition() == "attachment":
            continue
        kind = part.get_content_type()
        if kind == "text/plain" and not text:
            text = _decode(part)
        elif kind == "text/html" and not html:
            html = _decode(part)
    return text, html


def html_to_text(html: str) -> str:
    text = re.sub(r"(?is)<(script|style|head)\b.*?</\1>", " ", html)
    text = re.sub(r"(?i)<br\s*/?>|</(p|div|tr|li|h\d)>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    return _tidy(nh3.clean(text, tags=set()))


def _tidy(text: str) -> str:
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    return re.sub(r"\n\s*\n\s*\n+", "\n\n", text).strip()


def _snippet(text: str) -> str:
    flat = " ".join(line for line in text.split() if line)
    flat = re.sub(r"https?://\S+", "", flat)
    return " ".join(flat.split())[:_SNIPPET]


def attachments(msg: EmailMessage) -> list[tuple[Attachment, EmailMessage]]:
    """Files that came with the message: real attachments, and inline images that were given a name."""
    found: list[tuple[Attachment, EmailMessage]] = []
    for part in msg.walk():
        if part.is_multipart():
            continue
        name = part.get_filename()
        if part.get_content_disposition() != "attachment" and not (name and not part.get_content_maintype() == "text"):
            continue
        data = part.get_payload(decode=True) or b""
        found.append((Attachment(index=len(found), filename=name or "attachment", content_type=part.get_content_type(), size=len(data)), part))
    return found


def _inline_images(msg: EmailMessage) -> dict[str, str]:
    """Content-ID -> data: URL, so images that travel inside the message still show without any remote request."""
    found: dict[str, str] = {}
    budget = _INLINE_LIMIT
    for part in msg.walk():
        cid = (part.get("content-id") or "").strip("<> ")
        if not cid or part.get_content_maintype() != "image":
            continue
        data = part.get_payload(decode=True) or b""
        if len(data) > budget:
            continue
        budget -= len(data)
        found[cid] = f"data:{part.get_content_type()};base64,{base64.b64encode(data).decode()}"
    return found


def _keep_attribute(tag: str, attr: str, value: str) -> str | None:
    value = value.strip()
    low = value.lower()
    if attr == "src":  # only images carry a src after cleaning; a data: URL is fine there, nothing that runs is
        return value if low.startswith(("http://", "https://", "data:image/")) else None
    if attr == "href":
        return value if low.startswith(("http://", "https://", "mailto:", "tel:")) else None
    if attr == "style" and re.search(r"(?i)expression\s*\(|javascript:|behavior\s*:|-moz-binding", value):
        return None
    return value


def safe_html(html: str, inline: dict[str, str] | None = None) -> str:
    if inline:
        html = _CID.sub(lambda m: m.group(1) + inline.get(m.group(2), "cid:" + m.group(2)), html)
    return nh3.clean(
        html,
        tags=_TAGS,
        attributes=_ATTRS,
        url_schemes={"http", "https", "mailto", "tel", "data"},
        link_rel="noopener noreferrer nofollow",
        attribute_filter=_keep_attribute,
    )


def has_remote_content(html: str) -> bool:
    return bool(_REMOTE.search(html))


def _has_attachment_in(structure) -> bool:
    """Whether a BODYSTRUCTURE response names an attachment anywhere in it."""
    if isinstance(structure, (bytes, str)):
        return (structure.decode(errors="ignore") if isinstance(structure, bytes) else structure).upper() == "ATTACHMENT"
    if isinstance(structure, (tuple, list)):
        return any(_has_attachment_in(item) for item in structure)
    return False


def _word(value: bytes | str | None) -> str:
    """An envelope field: bytes that may be an encoded word (=?UTF-8?B?...?=)."""
    if value is None:
        return ""
    raw = value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value
    try:
        return str(make_header(decode_header(raw))).strip()
    except Exception:
        return raw.strip()


def _envelope_sender(envelope) -> Person:
    first = (getattr(envelope, "from_", None) or [None])[0]
    if first is None:
        return Person("", "")
    mailbox, host = _word(first.mailbox), _word(first.host)
    return Person(_word(first.name), f"{mailbox}@{host}" if mailbox and host else mailbox)


def summary(uid: int, raw: bytes, flags: tuple, structure=None, envelope=None) -> MessageSummary:
    """A row for the list from the start of a message (headers and the first few kilobytes of body).

    The server's own ENVELOPE (subject, sender, date) fills in whatever the text of the message could not give.
    """
    msg = parse(raw)
    text, html = _bodies(msg)
    seen = {f.decode() if isinstance(f, bytes) else str(f) for f in flags}
    body = text or (html_to_text(html) if html else "")
    has_files = _has_attachment_in(structure) if structure is not None else bool(attachments(msg))
    sender = _person(msg["from"])
    subject = str(msg["subject"] or "").strip()
    date = _date(msg)
    if envelope is not None:
        if not sender.address:
            sender = _envelope_sender(envelope)
        subject = subject or _word(envelope.subject)
        if date is None and envelope.date is not None:
            date = envelope.date.replace(tzinfo=UTC) if envelope.date.tzinfo is None else envelope.date.astimezone(UTC)
    return MessageSummary(
        uid=uid,
        subject=subject or "(no subject)",
        sender=sender,
        date=date,
        snippet=_snippet(body),
        seen="\\Seen" in seen,
        flagged="\\Flagged" in seen,
        has_attachments=has_files,
    )


def message(uid: int, raw: bytes, flags: tuple) -> tuple[Message, list[tuple[Attachment, EmailMessage]]]:
    msg = parse(raw)
    text, html = _bodies(msg)
    cleaned = safe_html(html, _inline_images(msg)) if html else ""
    files = attachments(msg)
    names = {f.decode() if isinstance(f, bytes) else str(f) for f in flags}
    return (
        Message(
            uid=uid,
            subject=str(msg["subject"] or "").strip() or "(no subject)",
            sender=_person(msg["from"]),
            to=_people(msg["to"]),
            cc=_people(msg["cc"]),
            date=_date(msg),
            seen="\\Seen" in names,
            flagged="\\Flagged" in names,
            text=_tidy(text) if text else html_to_text(html),
            html=cleaned,
            has_remote_content=has_remote_content(cleaned),
            attachments=[a for a, _ in files],
            message_id=str(msg["message-id"] or "").strip(),
            in_reply_to=str(msg["in-reply-to"] or "").strip(),
        ),
        files,
    )
