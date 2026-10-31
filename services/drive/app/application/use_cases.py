"""Searching the Drive: free text, and suggestions for a case from its identifying details."""

import asyncio
from dataclasses import dataclass

from stiamo_common.errors import AppError, Forbidden, NotFound, ValidationFailed

from app.application.ports import DriveGateway, DriveUnavailable, FileNotFound, FileTooLarge, NotConnected, Page
from app.domain.search import FILE_ID, Content, DriveFile, Match, clean_terms, files_query, parse_terms


class DriveNotConnected(AppError):
    """Google Drive isn't connected on this server yet."""

    status_code = 503
    code = "drive_not_connected"


class DriveDown(AppError):
    """Google Drive couldn't be reached. Try again in a moment."""

    status_code = 502
    code = "drive_unavailable"


@dataclass
class Status:
    available: bool
    folder: str | None = None
    reason: str | None = None


class DriveSearch:
    def __init__(self, gateway: DriveGateway, allowed_emails: set[str], folder_id: str = "") -> None:
        self._gateway, self._allowed, self._folder_id = gateway, allowed_emails, folder_id

    # --- who may use it --------------------------------------------------------------------------------

    def permits(self, email: str) -> bool:
        return email.strip().lower() in self._allowed

    def require(self, email: str) -> None:
        if not self.permits(email):
            raise Forbidden("Drive search isn't turned on for your account.")

    async def status(self, email: str) -> Status:
        if not self.permits(email):
            return Status(False, reason="Not turned on for this account.")
        if not self._gateway.configured:
            return Status(False, reason="Google Drive isn't connected on the server yet.")
        name = await self._gateway.folder_name(self._folder_id) if self._folder_id else None
        return Status(True, folder=name)

    # --- searching -------------------------------------------------------------------------------------

    async def _run(self, query: str, **kw) -> Page:
        try:
            return await self._gateway.search(query, **kw)
        except NotConnected as exc:
            raise DriveNotConnected() from exc
        except DriveUnavailable as exc:
            raise DriveDown() from exc

    async def search(self, email: str, text: str, *, limit: int = 20, page_token: str | None = None) -> Page:
        """Files whose name or contents contain every word. With no words: the most recently changed files."""
        self.require(email)
        terms = parse_terms(text)
        return await self._run(files_query(terms), limit=limit, page_token=page_token, newest_first=not terms)

    async def content(self, email: str, file_id: str) -> Content:
        """The file's bytes, fetched with the service account so no Google sign-in is needed to view it."""
        self.require(email)
        if not FILE_ID.match(file_id):
            raise NotFound("No such file.")
        try:
            return await self._gateway.content(file_id)
        except FileNotFound as exc:
            raise NotFound("That file isn't in the shared Drive folder.") from exc
        except FileTooLarge as exc:
            raise ValidationFailed("That file is too large to show here. Open it in Drive.") from exc
        except NotConnected as exc:
            raise DriveNotConnected() from exc
        except DriveUnavailable as exc:
            raise DriveDown() from exc

    async def suggest(self, email: str, terms: list[str], *, limit_per_term: int = 8) -> list[Match]:
        """Files that mention any of the given details (a case number, a court number, a party's name…), best first:
        a file that matches more of them ranks higher."""
        self.require(email)
        wanted = clean_terms(terms)
        if not wanted:
            return []
        pages = await asyncio.gather(*[self._run(files_query([t], phrases=True), limit=limit_per_term) for t in wanted])
        found: dict[str, tuple[DriveFile, list[str]]] = {}
        for term, page in zip(wanted, pages, strict=True):
            for f in page.files:
                entry = found.setdefault(f.id, (f, []))
                entry[1].append(term)
        ranked = sorted(found.values(), key=lambda e: (-len(e[1]), -(_stamp(e[0])), e[0].name.lower()))
        return [Match(**f.model_dump(), matched=matched) for f, matched in ranked]


def _stamp(f: DriveFile) -> int:
    """Sortable form of the modified time (newer first when everything else is equal)."""
    digits = "".join(c for c in (f.modified_time or "") if c.isdigit())
    return int(digits or 0)
