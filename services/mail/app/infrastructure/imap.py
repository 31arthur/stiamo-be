"""The IMAP adapter (imapclient). Each call opens its own short connection, reads, and leaves; the one long-lived connection is IDLE.

Rules it keeps: folders are opened read-only unless a flag is being changed, bodies are fetched with BODY.PEEK so reading never marks
a message seen by itself, and nothing here can delete or move mail.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
import ssl
from collections.abc import Callable
from contextlib import contextmanager
from threading import Event

from imapclient import IMAPClient
from imapclient.exceptions import IMAPClientError, LoginError

from app.application.ports import MailError, MailGateway
from app.domain.mail import Credentials, Download, Folder, Message, Page
from app.infrastructure import parse

log = logging.getLogger("stiamo.mail.imap")

_ROLES = {b"\\inbox": "inbox", b"\\sent": "sent", b"\\drafts": "drafts", b"\\trash": "trash", b"\\junk": "junk", b"\\archive": "archive", b"\\all": "archive"}
_BY_NAME = {"inbox": "inbox", "sent": "sent", "sent items": "sent", "sent mail": "sent", "drafts": "drafts", "trash": "trash", "deleted items": "trash", "bin": "trash", "spam": "junk", "junk": "junk", "junk email": "junk", "archive": "archive"}
_PEEK = 3000  # bytes of each message fetched for the list: its headers and the start of its body
_MAX_DOWNLOAD = 30_000_000


def check_host(host: str, *, allow_private: bool) -> None:
    """Refuses mail servers that are really machines on the private network this service runs in."""
    if allow_private:
        return
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)}
    except OSError:
        raise MailError(f"Couldn’t find a server called {host}. Check the spelling.") from None
    for address in addresses:
        if not ipaddress.ip_address(address.split("%")[0]).is_global:
            raise MailError("That address points at a private network, which isn’t allowed. Use your mail provider’s public IMAP server.")


class ImapGateway(MailGateway):
    def __init__(self, *, timeout: float, idle_seconds: int, allow_private: bool = False) -> None:
        self.timeout = timeout
        self.idle_seconds = idle_seconds
        self.allow_private = allow_private

    # -- connection ------------------------------------------------------------------------------------------------------------

    @contextmanager
    def _open(self, creds: Credentials):
        check_host(creds.host, allow_private=self.allow_private)
        context = ssl.create_default_context()
        client: IMAPClient | None = None
        try:
            client = IMAPClient(creds.host, port=creds.port, ssl=creds.security == "ssl", ssl_context=context, timeout=self.timeout)
            if creds.security == "starttls":
                client.starttls(ssl_context=context)
            client.login(creds.username, creds.password)
            yield client
        except LoginError as e:
            raise MailError(_reason(e) or "The server didn’t accept that username and password.", auth=True) from None
        except ssl.SSLError:
            raise MailError("The connection couldn’t be made secure. Check the port and the security setting.") from None
        except (OSError, IMAPClientError) as e:
            raise MailError(_reason(e) or "Couldn’t reach the mail server.") from None
        finally:
            if client is not None:
                try:
                    client.logout()
                except Exception:  # already closed
                    pass

    def check(self, creds: Credentials) -> None:
        with self._open(creds):
            pass

    # -- reading ---------------------------------------------------------------------------------------------------------------

    def folders(self, creds: Credentials) -> list[Folder]:
        with self._open(creds) as c:
            found = []
            for flags, _delimiter, name in c.list_folders():
                if b"\\noselect" in {f.lower() for f in flags}:
                    continue
                role = next((r for f in flags if (r := _ROLES.get(f.lower()))), "") or ("inbox" if name.upper() == "INBOX" else _BY_NAME.get(name.lower().rsplit("/", 1)[-1].rsplit(".", 1)[-1], ""))
                try:
                    status = c.folder_status(name, ["MESSAGES", "UNSEEN"])
                except IMAPClientError:
                    status = {}
                found.append(Folder(name=name, label="Inbox" if role == "inbox" else name.rsplit("/", 1)[-1], role=role, unseen=int(status.get(b"UNSEEN", 0)), total=int(status.get(b"MESSAGES", 0))))
        order = ["inbox", "drafts", "sent", "archive", "junk", "trash", ""]
        return sorted(found, key=lambda f: (order.index(f.role), f.label.lower()))

    def page(self, creds: Credentials, folder: str, *, before: int | None, limit: int, query: str) -> Page:
        with self._open(creds) as c:
            info = _select(c, folder, readonly=True)
            uids = sorted(c.search(["TEXT", query], charset="UTF-8") if query else c.search(["ALL"]))
            matches = len(uids)
            if before is not None:
                uids = [u for u in uids if u < before]
            chosen = uids[-limit:]
            rows = c.fetch(chosen, ["FLAGS", "BODYSTRUCTURE", f"BODY.PEEK[]<0.{_PEEK}>"]) if chosen else {}
            messages = []
            for uid in sorted(rows, reverse=True):
                data = rows[uid]
                raw = next((v for k, v in data.items() if k.startswith(b"BODY[")), b"")
                messages.append(parse.summary(uid, raw, data.get(b"FLAGS", ()), data.get(b"BODYSTRUCTURE")))
            return Page(messages=messages, total=matches, next_before=chosen[0] if len(uids) > len(chosen) else None, uid_validity=int(info.get(b"UIDVALIDITY", 0)))

    def message(self, creds: Credentials, folder: str, uid: int) -> Message:
        return self._fetch(creds, folder, uid)[0]

    def attachment(self, creds: Credentials, folder: str, uid: int, index: int) -> Download:
        _, files = self._fetch(creds, folder, uid)
        if not 0 <= index < len(files):
            raise MailError("That attachment isn’t in the message any more.")
        meta, part = files[index]
        data = part.get_payload(decode=True) or b""
        if len(data) > _MAX_DOWNLOAD:
            raise MailError("That attachment is too large to download here.")
        return Download(filename=meta.filename, content_type=meta.content_type, data=data)

    def _fetch(self, creds: Credentials, folder: str, uid: int):
        with self._open(creds) as c:
            _select(c, folder, readonly=True)
            rows = c.fetch([uid], ["FLAGS", "BODY.PEEK[]"])
            if uid not in rows:
                raise MailError("That message isn’t in the mailbox any more.")
            data = rows[uid]
            raw = next((v for k, v in data.items() if k.startswith(b"BODY[")), b"")
            return parse.message(uid, raw, data.get(b"FLAGS", ()))

    # -- the only changes it can make -----------------------------------------------------------------------------------------

    def set_flags(self, creds: Credentials, folder: str, uid: int, *, seen: bool | None, flagged: bool | None) -> None:
        with self._open(creds) as c:
            _select(c, folder, readonly=False)
            for flag, wanted in ((b"\\Seen", seen), (b"\\Flagged", flagged)):
                if wanted is True:
                    c.add_flags([uid], [flag], silent=True)
                elif wanted is False:
                    c.remove_flags([uid], [flag], silent=True)

    # -- IDLE (RFC 2177): the server tells us, we don't ask ---------------------------------------------------------------------

    def idle(self, creds: Credentials, folder: str, stop: Event, on_change: Callable[[], None], on_state: Callable[[str], None]) -> None:
        """Keeps one connection open on the folder. The server pushes "EXISTS", "EXPUNGE" or "FETCH" the moment something changes.

        Connections are quietly dropped by servers and networks, so IDLE is re-issued before the 29-minute limit and the whole
        connection is rebuilt with a growing pause if it fails. Anything that arrived while it was down is picked up by the screen
        asking for the list again when the state returns to "live".
        """
        delay = 2.0
        while not stop.is_set():
            try:
                with self._open(creds) as c:
                    _select(c, folder, readonly=True)
                    on_state("live")
                    delay = 2.0
                    while not stop.is_set():
                        c.idle()
                        waited = 0.0
                        changed = False
                        late: list = []
                        try:
                            while waited < self.idle_seconds and not stop.is_set() and not changed:
                                if c.idle_check(timeout=5):  # returns at once if there is news, else after 5 s of quiet
                                    changed = True
                                waited += 5
                        finally:
                            _text, late = c.idle_done()
                        if changed or late:
                            on_change()
            except MailError as e:
                if e.auth:
                    on_state("auth")
                    return  # a wrong password won't fix itself; the person has to update it
                on_state("down")
                log.info("idle on %s lost: %s", creds.host, e)
            except Exception:
                on_state("down")
                log.exception("idle on %s failed", creds.host)
            if stop.wait(delay):
                return
            delay = min(delay * 2, 120.0)


def _select(client: IMAPClient, folder: str, *, readonly: bool) -> dict:
    try:
        return client.select_folder(folder, readonly=readonly)
    except IMAPClientError:
        raise MailError("That folder isn’t on the server.") from None


def _reason(error: Exception) -> str:
    """The server's own words, trimmed, when it gave any ("Application-specific password required")."""
    text = str(error)
    if isinstance(error, (socket.timeout, TimeoutError)):
        return "The mail server took too long to answer."
    if isinstance(error, socket.gaierror):
        return "Couldn’t find that mail server. Check the address."
    if isinstance(error, ConnectionRefusedError):
        return "The mail server refused the connection. Check the port."
    if text.startswith("b'") or text.startswith('b"'):
        text = text[2:-1]
    return text.strip()[:200]
