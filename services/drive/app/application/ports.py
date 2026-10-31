"""Ports: what the use cases need from Google."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.domain.search import Content, DriveFile


@dataclass(frozen=True)
class Page:
    files: list[DriveFile]
    next_page_token: str | None = None


class NotConnected(Exception):
    """No Google credentials are configured on this server."""


class DriveUnavailable(Exception):
    """Google could not be reached, or refused the request."""


class FileNotFound(Exception):
    """No such file, or it isn't shared with the service account."""


class FileTooLarge(Exception):
    """The file is bigger than the app will stream."""


class DriveGateway(ABC):
    @property
    @abstractmethod
    def configured(self) -> bool: ...

    @abstractmethod
    async def search(self, query: str, *, limit: int, page_token: str | None = None, newest_first: bool = False) -> Page:
        """Runs a Drive query (see `files_query`). Raises NotConnected / DriveUnavailable."""

    @abstractmethod
    async def folder_name(self, folder_id: str) -> str | None:
        """The shared folder's name, for display; None when it can't be read."""

    @abstractmethod
    async def content(self, file_id: str) -> Content:
        """The file's bytes (Google's own documents exported). Raises FileNotFound / FileTooLarge / NotConnected / DriveUnavailable."""
