"""Sending mail over SMTP, from the same mailbox that is read over IMAP (same username and password).

The mailbox only stores its IMAP server, so the SMTP one is worked out from it: imap.example.com is read as smtp.example.com, and Microsoft 365
and Outlook are told apart because they use another port. A provider that does something else says so in the error, which names the server tried.
"""

from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from app.application.ports import MailError
from app.domain.mail import Credentials, Outgoing


def smtp_server(creds: Credentials) -> tuple[str, int, str]:
    """(host, port, security) to send through, for a mailbox read from `creds`."""
    host = creds.host
    if host.endswith(("office365.com", "outlook.com", "hotmail.com", "live.com")):
        return ("smtp-mail.outlook.com" if "outlook.com" in host else "smtp.office365.com"), 587, "starttls"
    for imap, smtp in (("imap.", "smtp."), ("imap-", "smtp-"), ("imap4.", "smtp."), ("imaps.", "smtp.")):
        if host.startswith(imap):
            host = smtp + host[len(imap):]
            break
    return host, (465 if creds.security == "ssl" else 587), ("ssl" if creds.security == "ssl" else "starttls")


def build(address: str, name: str, out: Outgoing) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = f"{name} <{address}>" if name else address
    msg["To"] = ", ".join(out.to)
    if out.cc:
        msg["Cc"] = ", ".join(out.cc)
    msg["Subject"] = out.subject
    msg["Date"] = formatdate(localtime=False)
    msg["Message-ID"] = make_msgid(domain=address.rsplit("@", 1)[-1])
    if out.in_reply_to:
        msg["In-Reply-To"] = out.in_reply_to
        msg["References"] = (out.references + " " + out.in_reply_to).strip() if out.references else out.in_reply_to
    msg.set_content(out.text)
    if out.html:
        msg.add_alternative(out.html, subtype="html")
    return msg


def send(creds: Credentials, address: str, name: str, out: Outgoing, *, timeout: float, check_host) -> str:
    host, port, security = smtp_server(creds)
    check_host(host)
    msg = build(address, name, out)
    context = ssl.create_default_context()
    try:
        client = smtplib.SMTP_SSL(host, port, timeout=timeout, context=context) if security == "ssl" else smtplib.SMTP(host, port, timeout=timeout)
        with client:
            if security == "starttls":
                client.starttls(context=context)
            client.login(creds.username, creds.password)
            client.send_message(msg, from_addr=address, to_addrs=[*out.to, *out.cc])
    except smtplib.SMTPAuthenticationError:
        raise MailError(f"The mail server ({host}) didn’t accept the username and password for sending.", auth=True) from None
    except smtplib.SMTPRecipientsRefused:
        raise MailError("The mail server refused the recipient’s address.") from None
    except ssl.SSLError:
        raise MailError(f"The connection to {host}:{port} couldn’t be made secure.") from None
    except (TimeoutError, OSError, smtplib.SMTPException) as e:
        raise MailError(f"Couldn’t send through {host}:{port}: {str(e)[:160] or 'the server didn’t answer'}") from None
    return str(msg["Message-ID"])
