from app.domain.mail import Credentials, Outgoing
from app.infrastructure.smtp import build, smtp_server

from tests.conftest import NEW, headers

BODY = {"to": ["ada@example.com"], "subject": "Re: Hearing", "text": "Noted, thank you.", "inReplyTo": "<orig@example.com>"}


async def account(client) -> str:
    return (await client.post("/v1/mail/accounts", json=NEW, headers=headers())).json()["id"]


def test_the_smtp_server_is_worked_out_from_the_imap_one():
    c = lambda host, security="ssl": Credentials(host, 993, security, "u", "p")  # noqa: E731
    assert smtp_server(c("imap.gmail.com")) == ("smtp.gmail.com", 465, "ssl")
    assert smtp_server(c("imap.example.com", "starttls")) == ("smtp.example.com", 587, "starttls")
    assert smtp_server(c("outlook.office365.com")) == ("smtp.office365.com", 587, "starttls")
    assert smtp_server(c("mail.sbchambers.in")) == ("mail.sbchambers.in", 465, "ssl")  # a host that does both is used as it is


def test_a_reply_is_threaded_and_carries_both_the_page_and_the_plain_text():
    m = build("arjun@sb.example.com", "Chambers", Outgoing(["ada@example.com"], "Re: Hearing", "Noted.", ["cc@example.com"], "<p>Noted.</p>", "<orig@example.com>", "<first@example.com>"))
    assert m["From"] == "Chambers <arjun@sb.example.com>" and m["To"] == "ada@example.com" and m["Cc"] == "cc@example.com"
    assert m["In-Reply-To"] == "<orig@example.com>" and m["References"] == "<first@example.com> <orig@example.com>" and m["Message-ID"].endswith("@sb.example.com>")
    assert m.get_body(("plain",)).get_content().strip() == "Noted." and "<p>Noted.</p>" in m.get_body(("html",)).get_content()


async def test_a_message_is_sent_from_the_mailbox_itself(client, mail):
    a = await account(client)
    r = await client.post(f"/v1/mail/accounts/{a}/send", json=BODY, headers=headers())
    assert r.status_code == 200 and r.json() == {"sent": True, "messageId": "<new-id@example.com>", "to": ["ada@example.com"]}
    address, name, out = mail.sent[0]
    assert address == "arjun@sb.example.com" and name == "Chambers" and out.to == ["ada@example.com"] and out.in_reply_to == "<orig@example.com>" and out.text == "Noted, thank you."


async def test_sending_is_refused_for_bad_input_and_for_someone_elses_mailbox(client, mail):
    a = await account(client)
    for bad in ({**BODY, "to": []}, {**BODY, "to": ["not-an-address"]}, {**BODY, "subject": "two\nlines"}, {**BODY, "text": "   "}):
        assert (await client.post(f"/v1/mail/accounts/{a}/send", json=bad, headers=headers())).status_code == 422
    assert (await client.post(f"/v1/mail/accounts/{a}/send", json=BODY, headers=headers("u2", "b@example.com"))).status_code == 404
    assert mail.sent == []


async def test_a_server_that_refuses_says_why(client, mail):
    r = await client.post("/v1/mail/accounts", json={**NEW, "password": "nosend"}, headers=headers())
    refused = await client.post(f"/v1/mail/accounts/{r.json()['id']}/send", json=BODY, headers=headers())
    assert refused.status_code == 422 and "for sending" in refused.json()["error"]["message"]
