"""What a search over the Drive means. Pure rules: no network, no framework."""

import re

from pydantic import Field
from stiamo_common.errors import ValidationFailed
from stiamo_common.models import DomainModel

FOLDER_MIME = "application/vnd.google-apps.folder"

MAX_QUERY_CHARS = 200
MAX_TERMS = 8
MAX_TERM_CHARS = 120


class DriveFile(DomainModel):
    """A file in the Drive, as the app needs it. Only this metadata ever leaves Google; the content stays there."""

    id: str
    name: str
    mime_type: str
    modified_time: str | None = None
    #: The address that opens the file in Google's own viewer; this is what gets attached to a case.
    web_view_link: str | None = None
    size: int | None = None


FILE_ID = re.compile(r"^[A-Za-z0-9_-]{10,100}$")

#: Google's own documents have no bytes; they are exported to a format the app can show.
EXPORTS = {
    "application/vnd.google-apps.document": "application/pdf",
    "application/vnd.google-apps.presentation": "application/pdf",
    "application/vnd.google-apps.drawing": "application/pdf",
    "application/vnd.google-apps.spreadsheet": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}

MAX_CONTENT_BYTES = 40 * 1024 * 1024


class Content(DomainModel):
    """A file's bytes, as the app can show them."""

    name: str
    mime_type: str
    data: bytes


class Match(DriveFile):
    """A file found for a suggestion, with the search terms it matched."""

    matched: list[str] = Field(default_factory=list)


def escape(text: str) -> str:
    """Drive's query language quotes with single quotes; inside them only backslash and quote need escaping."""
    return text.replace("\\", "\\\\").replace("'", "\\'")


_TOKEN = re.compile(r'"([^"]+)"|(\S+)')


def parse_terms(text: str) -> list[str]:
    """Words, with "quoted phrases" kept together. Every term has to match."""
    clean = (text or "").strip()
    if not clean:
        return []
    if len(clean) > MAX_QUERY_CHARS:
        raise ValidationFailed(f"Search for at most {MAX_QUERY_CHARS} characters.")
    return [(m.group(1) or m.group(2)).strip() for m in _TOKEN.finditer(clean) if (m.group(1) or m.group(2)).strip()]


def _contains(term: str, phrase: bool) -> str:
    # fullText covers the file's name and its contents. A phrase keeps "TUMWU-2026-001" or "WP 1290/2026" in one piece.
    quoted = f'"{term}"' if phrase else term
    return f"fullText contains '{escape(quoted)}'"


def files_query(terms: list[str], *, phrases: bool = False) -> str:
    """Every term must appear (in the name or the content); folders and trashed files are left out."""
    parts = [_contains(t, phrases or " " in t) for t in terms]
    parts += ["trashed = false", f"mimeType != '{FOLDER_MIME}'"]
    return " and ".join(parts)


def clean_terms(terms: list[str]) -> list[str]:
    """For suggestions: tidy whitespace, drop duplicates and anything too short to mean something."""
    kept: dict[str, str] = {}
    for t in terms:
        t = " ".join((t or "").split())[:MAX_TERM_CHARS]
        if len(t) >= 3:
            kept.setdefault(t.lower(), t)
    return list(kept.values())[:MAX_TERMS]
