"""What is decided in code, before any model reads a mail: which senders are on the priority list and which kinds of automated mail are dropped.
Pure: no network. The model is only asked about what these rules leave open, so a one-time code never costs a call and never gets a chance to
look important."""

from __future__ import annotations

#: Senders whose mail is always worth a careful look (domains and their subdomains). A person is judged by the model, not by this list.
PRIORITY_DOMAINS = [
    "bwint.org", "bwint.net", "mfasia.org", "migrantforum.org",
    "un.org", "ilo.org", "ohchr.org", "undp.org", "unicef.org", "unhcr.org", "iom.int", "unwomen.org", "unesco.org", "unfpa.org", "unep.org", "unodc.org", "who.int", "wfp.org", "fao.org", "unops.org", "unaids.org",
    "gov.in", "nic.in", "gov", "gov.uk", "gov.au", "gov.ph", "gov.np", "gov.bd", "gov.lk", "gov.my", "gov.sg", "go.th", "go.id", "europa.eu",
    "rtionline.gov.in", "cic.gov.in",
]

#: Subject patterns (regular expressions, case-insensitive) that mark automated mail nobody needs to be told about.
DISCARD_SUBJECT = {
    "one-time code, login or security mail": r"\b(otp|one[- ]?time (pass|code|pin)|verification code|security code|login code|log[- ]?in code|sign[- ]?in (code|attempt|alert)|new sign[- ]?in|password reset|reset your password|2fa|two[- ]factor)\b",
    "account statement or balance mail": r"(account statement|e-?statement|statement of account|available balance|balance (alert|update|summary)|credit card statement|monthly statement|transaction alert|has been (debited|credited))",
    "order, shipping or receipt mail": r"(order (confirmation|confirmed|summary|receipt)|your order|has been (shipped|dispatched|delivered)|out for delivery|tracking (number|id)|payment receipt|tax invoice)",
    "bulk or promotional mail": r"(unsubscribe|view in browser|newsletter|webinar|% off|flash sale|limited[- ]time offer|special offer)",
}
#: Personal mail only: in a law practice an "order" is often a court order, so this one is never applied there.
DISCARD_ORDER_COPY = {"order copy": r"(order copy|copy of (your|the) order|order #)"}
#: Patterns checked against the start of the text as well as the subject: a code is often only in the body.
DISCARD_TEXT = {"one-time code or login mail": DISCARD_SUBJECT["one-time code, login or security mail"]}


def domain_patterns(domains: list[str] | None = None, width: int = 150) -> list[str]:
    """The priority domains as a few regular expressions for the sender's address (a condition keeps at most 200 characters each)."""
    out: list[str] = []
    group: list[str] = []
    for d in domains or PRIORITY_DOMAINS:
        piece = d.replace(".", r"\.")
        if sum(len(x) + 1 for x in group) + len(piece) > width:
            out.append(r"@([a-z0-9-]+\.)*(" + "|".join(group) + ")$")
            group = []
        group.append(piece)
    if group:
        out.append(r"@([a-z0-9-]+\.)*(" + "|".join(group) + ")$")
    return out
