import base64
from email.message import EmailMessage

import pytest
from app.application.ports import MailError
from app.infrastructure import parse
from app.infrastructure.imap import check_host


def build(**parts) -> bytes:
    m = EmailMessage()
    m["From"] = "Ada Lovelace <ada@example.com>"
    m["To"] = "me@example.com, Bob <bob@example.com>"
    m["Subject"] = parts.pop("subject", "Hearing notice")
    m["Date"] = "Tue, 13 Oct 2026 09:30:00 +0530"
    m["Message-ID"] = "<abc@example.com>"
    m.set_content(parts.pop("text", "Please attend on Monday.\n\nRegards"))
    if html := parts.pop("html", None):
        m.add_alternative(html, subtype="html")
    for name, data in parts.pop("files", {}).items():
        m.add_attachment(data, maintype="application", subtype="pdf", filename=name)
    return m.as_bytes()


def test_summary_reads_the_people_subject_date_and_snippet():
    s = parse.summary(5, build(), (b"\\Seen",))
    assert (s.uid, s.subject, s.sender.name, s.sender.address) == (5, "Hearing notice", "Ada Lovelace", "ada@example.com")
    assert s.snippet.startswith("Please attend on Monday.") and s.seen and not s.flagged
    assert s.date.isoformat() == "2026-10-13T04:00:00+00:00"


def test_a_message_without_a_subject_still_has_a_row():
    raw = b"From: a@b.co\r\n\r\nhi"
    assert parse.summary(1, raw, ()).subject == "(no subject)"


def test_a_truncated_message_still_gives_a_snippet():
    raw = build(html="<p>" + "Long body " * 500 + "</p>")
    s = parse.summary(1, raw[:900], ())
    assert s.snippet and s.subject == "Hearing notice"


def test_html_is_made_safe():
    dirty = '<p onclick="x()">Hi</p><script>alert(1)</script><a href="javascript:alert(1)">bad</a><a href="https://ok.example">ok</a><iframe src="https://evil"></iframe><form action="/x"><input></form><img src="https://t.example/p.gif">'
    msg, _ = parse.message(1, build(html=dirty), ())
    assert "script" not in msg.html and "onclick" not in msg.html and "javascript:" not in msg.html
    assert "iframe" not in msg.html and "<form" not in msg.html and "<input" not in msg.html
    assert 'href="https://ok.example"' in msg.html and "noopener" in msg.html
    assert msg.has_remote_content is True


def test_inline_images_become_data_urls_so_nothing_is_fetched():
    m = EmailMessage()
    m["From"] = "a@b.co"
    m.set_content("plain")
    m.add_alternative('<p>Logo <img src="cid:logo1"></p>', subtype="html")
    m.get_payload()[1].add_related(b"\x89PNG", maintype="image", subtype="png", cid="<logo1>")
    msg, _ = parse.message(1, m.as_bytes(), ())
    assert "data:image/png;base64," + base64.b64encode(b"\x89PNG").decode() in msg.html
    assert msg.has_remote_content is False


def test_attachments_are_listed_with_names_and_sizes():
    msg, files = parse.message(1, build(files={"Notice ü.pdf": b"%PDF-1.4"}), ())
    assert [(a.filename, a.size, a.content_type) for a in msg.attachments] == [("Notice ü.pdf", 8, "application/pdf")]
    assert files[0][1].get_payload(decode=True) == b"%PDF-1.4"
    assert parse.summary(1, build(files={"x.pdf": b"1"}), ()).has_attachments is True


def test_html_only_mail_gets_a_text_version():
    raw = b"From: a@b.co\r\nSubject: s\r\nContent-Type: text/html\r\n\r\n<p>Hello<br>world</p><style>p{}</style>"
    msg, _ = parse.message(1, raw, ())
    assert msg.text == "Hello\nworld"


def test_people_lists():
    msg, _ = parse.message(1, build(), ())
    assert [(p.name, p.address) for p in msg.to] == [("", "me@example.com"), ("Bob", "bob@example.com")]
    assert msg.message_id == "<abc@example.com>"


def test_wrong_charset_does_not_break_reading():
    raw = b"From: a@b.co\r\nSubject: s\r\nContent-Type: text/plain; charset=made-up\r\nContent-Transfer-Encoding: 8bit\r\n\r\ncaf\xe9"
    assert "caf" in parse.message(1, raw, ())[0].text


@pytest.mark.parametrize("host", ["127.0.0.1", "10.0.0.5", "192.168.1.9", "169.254.169.254", "localhost"])
def test_private_servers_are_refused(host):
    with pytest.raises(MailError):
        check_host(host, allow_private=False)


def test_private_servers_can_be_allowed_for_development():
    check_host("127.0.0.1", allow_private=True)
