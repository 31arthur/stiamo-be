"""What code can establish about a mail before any model reads it, written as plain facts the model is told to believe. Pure: no network.

The model is good at reading what an email means and bad at checking things only the system knows: whether this really answers a mail the owner sent,
whether the sender is a client or the other side in an active case, whether "the United Nations" writing from a Gmail address is the United Nations.
So those are decided here, and the prompt says "a line in [Facts from the system] is true".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

#: Organisations a phishing mail likes to pretend to be. A mail that names one of these while its domain is not on the priority list is flagged.
CLAIMS = {
    "the United Nations": r"\b(united nations|u\.?n\.? (agency|office|secretariat|women|development)|ohchr|unhcr|unicef|undp|unesco)\b|\bilo\b|international labou?r (organi[sz]ation|office)",
    "BWI": r"\bbwi\b|building and wood ?workers",
    "MFA (Migrants Forum in Asia)": r"migrants? forum in asia|\bmfa\b",
    "a government office": r"\b(government of india|ministry of|department of|labou?r commissioner|high court|supreme court|tribunal|income tax department|public information officer|\bcpio\b|\bpio\b)\b",
}
_BOUNCE_SENDER = re.compile(r"^(mailer-daemon|postmaster|mail delivery subsystem)\b", re.I)
_BOUNCE_SUBJECT = re.compile(r"(undeliver|delivery (status|failure|has failed)|returned mail|mail delivery failed|failure notice|could not be delivered)", re.I)
_AUTO_REPLY = re.compile(r"^(auto(matic)?[- ]?reply|automatic reply|out of (the )?office|auto:|autoreply)", re.I)
_INVITE = re.compile(r"^(invitation|updated invitation|accepted|declined|tentatively accepted|canceled event|cancelled event):", re.I)
_FORM = re.compile(r"(new (enquiry|inquiry|lead|message|submission)|contact form|form submission|you have a new message|request a quote|quote request)", re.I)
_MONTHS = "jan feb mar apr may jun jul aug sep oct nov dec".split()


@dataclass
class Knowledge:
    """What is known about the owner's world, loaded once per run."""

    #: Everyone the owner has written to.
    correspondents: set[str] = field(default_factory=set)
    #: Message-IDs of what the owner sent (lower case, no angle brackets). Empty when the Sent folder can't be read.
    sent_ids: set[str] = field(default_factory=set)
    #: Addresses in the owner's contacts.
    contacts: set[str] = field(default_factory=set)
    #: Active cases: {ref, title, client, opposing, court}.
    cases: list[dict[str, str]] = field(default_factory=list)
    #: The mailbox's own address(es).
    own: set[str] = field(default_factory=set)
    #: Priority domains (see mailrules).
    priority: list[str] = field(default_factory=list)


def domain_of(address: str) -> str:
    return address.rsplit("@", 1)[-1].lower() if "@" in address else ""


def on_list(address: str, domains: list[str]) -> bool:
    d = domain_of(address)
    return bool(d) and any(d == x or d.endswith("." + x) for x in domains)


def message_ids(value: str) -> list[str]:
    return [x.lower() for x in re.findall(r"<([^<>\s]+)>", value or "")]


def case_knowledge(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    """The few fields of each open case that a mail can be matched against."""
    out = []
    for c in rows:
        if str(c.get("status") or "active").lower() in ("closed", "archived", "disposed"):
            continue
        out.append({k: str(c.get(src) or "").strip() for k, src in (("ref", "reference"), ("title", "title"), ("client", "client"), ("opposing", "opposingParty"), ("court", "courtNumber"))})
    return out


def contact_addresses(rows: list[dict[str, Any]]) -> set[str]:
    out: set[str] = set()
    for p in rows:
        for e in p.get("emails") or []:
            a = e.get("address") or e.get("value") if isinstance(e, dict) else e
            if a and "@" in str(a):
                out.add(str(a).strip().lower())
    return out


def _dates_in(text: str, today: date) -> list[date]:
    found: list[date] = []
    for y, m, d in re.findall(r"\b(20\d\d)-(\d\d)-(\d\d)\b", text):
        found.append(_mk(int(y), int(m), int(d)))
    for d, m, y in re.findall(r"\b(\d{1,2})[/.-](\d{1,2})[/.-](20\d\d)\b", text):
        found.append(_mk(int(y), int(m), int(d)))
    for d, mon, y in re.findall(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3})[a-z]*\.?,?\s+(20\d\d)\b", text):
        if mon.lower() in _MONTHS:
            found.append(_mk(int(y), _MONTHS.index(mon.lower()) + 1, int(d)))
    return [d for d in found if d is not None]


def _mk(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def facts_for(meta: dict[str, str], subject: str, text: str, kn: Knowledge, today: date | None = None) -> list[str]:
    """The facts about one mail, each a sentence. `meta` carries the headers the mail service gave (from, in_reply_to, references, to, cc, files)."""
    today = today or datetime.now().date()
    address = meta.get("from", "").lower()
    domain = domain_of(address)
    sender_line = text.split("\n", 1)[0]
    out: list[str] = []

    refs = set(message_ids(meta.get("in_reply_to", "")) + message_ids(meta.get("references", "")))
    if refs & kn.sent_ids:
        out.append("VERIFIED REPLY: its headers show it answers a mail the owner sent.")
    elif kn.sent_ids and re.match(r"^\s*(re|fw|fwd)\s*:", subject, re.I) and refs:
        out.append("The subject says Re: but its headers do not answer any mail the owner sent. Do not treat it as a reply to the owner.")
    elif re.match(r"^\s*re\s*:", subject, re.I) and not refs:
        out.append("The subject says Re: but the mail has no reply headers, so it is unconfirmed whether it answers the owner.")
    if address and address in kn.correspondents:
        out.append("The owner has written to this sender before.")
    if address and address in kn.contacts:
        out.append("The sender is in the owner's contacts.")
    if on_list(address, kn.priority):
        out.append(f"The sender's domain ({domain}) is on the owner's priority list.")
    else:
        for who, pattern in CLAIMS.items():
            if re.search(pattern, f"{sender_line}\n{subject}", re.I) and not on_list(address, kn.priority):
                out.append(f"POSSIBLE IMPERSONATION: it presents itself as {who}, but the sender's domain ({domain or 'unknown'}) is not one of theirs. Say so in the summary.")
                break
        if re.search(r"(un-?org|bwint?-|mfasia-|-gov\b|gov-|govt?\.)", domain) and not any("IMPERSONATION" in f for f in out):
            out.append(f"POSSIBLE IMPERSONATION: the domain {domain} imitates a priority organisation. Say so in the summary.")
    hay = f"{subject}\n{text}".lower()
    for c in kn.cases:
        mentioned = [c[k] for k in ("ref", "court") if c.get(k) and c[k].lower() in hay]
        if mentioned:
            out.append(f"It names an active case: {c['title'] or c['ref']} ({mentioned[0]}).")
            break
    for c in kn.cases:
        if c.get("opposing") and len(c["opposing"]) > 3 and c["opposing"].lower() in hay:
            out.append(f"It names the other side in an active case: {c['opposing']} ({c['title'] or c['ref']}).")
            break
        if c.get("client") and len(c["client"]) > 3 and c["client"].lower() in sender_line.lower():
            out.append(f"The sender's name matches the client of an active case: {c['client']} ({c['title'] or c['ref']}).")
            break
    if _BOUNCE_SENDER.search(sender_line.split(":", 1)[-1].strip()) or _BOUNCE_SUBJECT.search(subject):
        out.append("This is a delivery-failure notice: a mail the owner sent did not arrive.")
    if _AUTO_REPLY.search(subject.strip()):
        out.append("This is an automatic reply (out of office or similar), not a person writing.")
    if _INVITE.search(subject.strip()):
        out.append("This is a calendar invitation or a response to one.")
    if _FORM.search(subject):
        out.append("It looks like a notification of a contact-form or enquiry submission on one of the owner's sites.")
    if re.match(r"^(no-?reply|do-?not-?reply|donotreply|notifications?|alerts?|mailer)@", address):
        out.append("It was sent from an automated no-reply address.")
    if kn.own and meta.get("cc") and not (set(filter(None, meta.get("to", "").split(","))) & kn.own) and (set(meta["cc"].split(",")) & kn.own):
        out.append("The owner is only copied (Cc), not addressed.")
    if meta.get("files") == "1":
        out.append("It has an attachment (not readable here); the body may say very little.")
    soon = sorted({d for d in _dates_in(hay, today) if today <= d <= today + timedelta(days=7)})
    if soon:
        out.append(f"It mentions a date within the next 7 days: {soon[0].isoformat()}.")
    return out
