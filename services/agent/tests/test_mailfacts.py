"""The part of the mail agents that code decides, before any model: which mail is dropped by a rule and which facts are written above the rest. A labelled
set of mails stands for the owner's real inbox; a change to a rule or a fact that moves any of them shows up here."""

from datetime import date

import pytest

from app.domain.mailfacts import Knowledge, contact_addresses, facts_for
from app.domain.mailwatch import watch_config
from app.domain.workflows import Rules, discarded_by, item_from_mail, with_context

TODAY = date(2026, 12, 1)
SENT = {"sent-1@sb.example.com"}


def mail(subject, address, text="hello", **extra):
    return item_from_mail({"uid": 1, "subject": subject, "sender": {"name": "Someone", "address": address}, "snippet": text, **extra}, 1, "a")


def kn(**over):
    base = dict(sent_ids=set(SENT), priority=watch_config("sb", ["a"])["rules"]["priorityDomains"], own={"me@sb.example.com"}, correspondents={"lead@client.io"}, contacts={"sister@family.in"},
                cases=[{"ref": "SA-2026-001", "title": "Alur NHAI wages", "client": "Alur workers", "opposing": "NHAI", "court": "WP 1290/2026"}])
    return Knowledge(**{**base, **over})


# (workspace, subject, sender, extra mail fields, dropped by a rule?)
GOLDEN = [
    ("sb", "Your OTP is 483920", "no-reply@bank.com", {}, True),
    ("sb", "Account statement for October", "alerts@bank.com", {}, True),
    ("sb", "Your order has been shipped", "orders@shop.com", {}, True),
    ("sb", "Weekly newsletter: 50% off webinars", "news@vendor.io", {}, True),
    ("sb", "Certified order copy in WP 1290/2026", "registry@court.gov.in", {}, False),
    ("sb", "Reply to RTI application No. 4471", "noreply@rtionline.gov.in", {}, False),
    ("sb", "Newsletter: BWI Weekly Bulletin", "news@bwint.org", {}, False),  # a priority sender is never dropped by a rule; the model sorts it into digest
    ("sb", "Undelivered Mail Returned to Sender", "mailer-daemon@sb.example.com", {}, False),
    ("sb", "Hearing notice, notice of 3 December", "office@court.gov.in", {}, False),
    ("sb", "Thanks for today", "client@gmail.com", {}, False),
    ("sb", "Re: draft", "x@ilo.org", {"inReplyTo": "<sent-1@sb.example.com>"}, False),
    ("sb", "Verification code for your account", "security@x.com", {}, True),
    ("personal", "Your OTP is 481516", "no-reply@bank.com", {}, True),
    ("personal", "Your credit card statement is ready", "cards@bank.com", {}, True),
    ("personal", "Order copy attached", "shop@x.com", {}, True),
    ("personal", "Your order #8841 is out for delivery", "courier@x.com", {}, True),
    ("personal", "Suspicious transaction on your card", "alerts@bank.com", {}, False),
    ("personal", "Your auto-debit payment failed", "alerts@bank.com", {}, False),
    ("personal", "Your flight has been rescheduled", "noreply@airline.com", {}, False),
    ("personal", "New enquiry from your contact form", "noreply@site.io", {}, False),
    ("personal", "Request for a quote: editing", "agency@x.com", {}, False),
    ("personal", "Boost your sales 10x, unsubscribe any time", "sales@pitch.io", {}, True),
    ("personal", "Re: proposal", "director@client.io", {"inReplyTo": "<sent-1@sb.example.com>"}, False),
    ("personal", "New sign-in attempt from Chrome", "security@x.com", {}, True),
]


@pytest.mark.parametrize("workspace,subject,sender,extra,dropped", GOLDEN)
def test_the_rules_drop_exactly_what_the_owner_never_wants_and_let_the_rest_reach_the_model(workspace, subject, sender, extra, dropped):
    rules = Rules.model_validate(watch_config(workspace, ["a"])["rules"])
    assert (discarded_by(mail(subject, sender, **extra), rules, kn()) is not None) is dropped


def facts(subject, sender, text="hello", **extra):
    item = mail(subject, sender, text, **extra)
    return " ".join(facts_for(item.meta, item.title, item.text, kn(), TODAY))


def test_a_reply_is_verified_by_its_headers_not_by_its_subject():
    assert "VERIFIED REPLY" in facts("Re: quote", "lead@client.io", inReplyTo="<Sent-1@sb.example.com>")
    assert "VERIFIED REPLY" in facts("Anything", "x@y.io", references="<a@b> <sent-1@sb.example.com>")
    assert "do not answer any mail the owner sent" in facts("Re: quote", "x@y.io", inReplyTo="<stranger@elsewhere.com>") or "headers do not answer" in facts("Re: quote", "x@y.io", inReplyTo="<stranger@elsewhere.com>")
    assert "unconfirmed" in facts("Re: quote", "x@y.io")
    assert "VERIFIED" not in facts("Quote", "x@y.io")


def test_the_facts_know_the_owners_world():
    assert "written to this sender" in facts("Hi", "lead@client.io") and "in the owner's contacts" in facts("Hi", "sister@family.in")
    assert "priority list" in facts("Consultation", "desk@ilo.org") and "priority list" not in facts("Consultation", "desk@gmail.com")
    assert "active case" in facts("Listing of WP 1290/2026", "registry@court.gov.in")
    assert "other side" in facts("Letter", "legal@nhai.org", "From NHAI legal cell regarding the wages")
    assert "IMPERSONATION" in facts("Grant award", "claims@un-org-secretariat.com", "United Nations grant award") and "IMPERSONATION" not in facts("Grant award", "desk@un.org", "United Nations grant award")
    assert "IMPERSONATION" in facts("Notice", "pay@gov-tax-refund.com", "Income Tax Department refund")
    assert "delivery-failure" in facts("Undelivered Mail Returned to Sender", "mailer-daemon@x.com")
    assert "automatic reply" in facts("Automatic reply: away", "x@y.com") and "calendar invitation" in facts("Invitation: sync @ Mon", "x@y.com")
    assert "contact-form" in facts("New enquiry from your site", "noreply@site.io") and "no-reply" in facts("Hi", "noreply@site.io")
    assert "only copied" in facts("FYI", "x@y.com", to=["boss@x.org"], cc=["me@sb.example.com"]) and "only copied" not in facts("FYI", "x@y.com", to=["me@sb.example.com"])
    assert "attachment" in facts("Notice", "x@y.com", hasAttachments=True)
    assert "2026-12-04" in facts("Hearing", "x@y.com", "hearing on 4 Dec 2026") and "within the next 7 days" not in facts("Hearing", "x@y.com", "hearing on 4 Jan 2027")
    assert facts("Plain", "x@y.com") == ""


def test_the_system_facts_reach_the_model_above_the_mail_and_contact_emails_are_read_either_way():
    rules = Rules.model_validate(watch_config("sb", ["a"])["rules"])
    told = with_context(mail("Re: draft", "x@ilo.org", inReplyTo="<sent-1@sb.example.com>"), rules, set(), kn())
    assert told.text.startswith("[Facts from the system: ") and "VERIFIED REPLY" in told.text.split("\n")[0]
    assert contact_addresses([{"emails": ["A@b.io", {"address": "c@d.io"}]}, {"emails": []}]) == {"a@b.io", "c@d.io"}
