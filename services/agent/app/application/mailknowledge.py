"""Loads what is known about the owner's world for one run of a mail agent (see domain/mailfacts.py). Every part is best effort: when the Sent folder,
the contacts or the cases can't be read, the mail is still judged, with fewer facts."""

from __future__ import annotations

import logging

from app.application.ports import DataGateway
from app.domain.llm import AiError
from app.domain.mailfacts import Knowledge, case_knowledge, contact_addresses

log = logging.getLogger(__name__)


async def load(data: DataGateway, account_id: str, own: str, priority: list[str], *, workspace: str | None, note_correspondents: bool = True) -> Knowledge:
    kn = Knowledge(priority=list(priority), own={own.lower()} if own else set())
    try:
        if note_correspondents:
            kn.correspondents = {a.lower() for a in await data.mail_correspondents(account_id)}
        kn.sent_ids = {i.lower() for i in await data.mail_sent_ids(account_id)}
    except AiError:
        log.info("the Sent folder of %s could not be read", account_id)
    try:
        kn.contacts = contact_addresses(await data.contacts("", 200))
    except Exception:  # noqa: BLE001  contacts are a bonus
        log.info("contacts could not be read")
    if workspace in (None, "sb"):
        try:
            kn.cases = case_knowledge(await data.cases())
        except Exception:  # noqa: BLE001  a workspace without the practice service has no cases
            log.info("cases could not be read")
    return kn
