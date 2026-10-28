"""Supporting documents attached to any record: an uploaded file, or a link to a document kept elsewhere."""

import re
from typing import Literal
from urllib.parse import parse_qs, unquote, urlparse

from pydantic import Field
from stiamo_common.models import DomainModel

EntityKind = Literal["task", "meeting", "followUp", "case", "hearing", "plan", "goal", "counsel", "client", "project"]
Backend = Literal["storage", "link"]
LinkProvider = Literal[
    "google-doc", "google-sheet", "google-slides", "google-drive", "google-folder", "google-form", "web"
]

LINK_MIME = "text/uri-list"

PROVIDER_LABELS: dict[str, str] = {
    "google-doc": "Google Doc", "google-sheet": "Google Sheet", "google-slides": "Google Slides",
    "google-drive": "Google Drive file", "google-folder": "Google Drive folder", "google-form": "Google Form",
    "web": "Web link",
}


class EntityRef(DomainModel):
    kind: EntityKind
    id: str

    def same_as(self, other: "EntityRef") -> bool:
        return self.kind == other.kind and self.id == other.id


class DocumentLink(DomainModel):
    #: What was pasted.
    url: str
    provider: LinkProvider
    #: An address that can be framed to show the document inside the app, when the provider allows it.
    embed_url: str | None = None


class Attachment(DomainModel):
    id: str = Field(min_length=1, max_length=64)
    user_id: str
    owner: EntityRef
    name: str
    mime: str
    size: int
    backend: Backend
    #: Object path inside the file store, for uploaded files.
    path: str | None = None
    #: Set for linked documents, which have no stored bytes.
    link: DocumentLink | None = None
    caption: str | None = None
    created_at: str
    updated_at: str


# --- links ----------------------------------------------------------------------------------------------

_GOOGLE_ID = r"[\w-]{15,}"


def _route(host: str, path: str) -> re.Pattern[str]:
    return re.compile(rf"^https://{host}/{path}")


_PATTERNS: list[tuple[re.Pattern[str], str, str | None]] = [
    (_route(r"docs\.google\.com", rf"document/(?:u/\d+/)?d/({_GOOGLE_ID})"), "google-doc",
     "https://docs.google.com/document/d/{}/preview"),
    (_route(r"docs\.google\.com", rf"spreadsheets/(?:u/\d+/)?d/({_GOOGLE_ID})"), "google-sheet",
     "https://docs.google.com/spreadsheets/d/{}/preview"),
    (_route(r"docs\.google\.com", rf"presentation/(?:u/\d+/)?d/({_GOOGLE_ID})"), "google-slides",
     "https://docs.google.com/presentation/d/{}/preview"),
    (_route(r"docs\.google\.com", rf"forms/(?:u/\d+/)?d/(?:e/)?({_GOOGLE_ID})"), "google-form", None),
    (_route(r"drive\.google\.com", rf"file/(?:u/\d+/)?d/({_GOOGLE_ID})"), "google-drive",
     "https://drive.google.com/file/d/{}/preview"),
    (_route(r"drive\.google\.com", rf"drive/(?:u/\d+/)?folders/({_GOOGLE_ID})"), "google-folder",
     "https://drive.google.com/embeddedfolderview?id={}#list"),
]


def describe_link(text: str) -> DocumentLink | None:
    """Works out what a pasted address is and, for Google's documents, the address that shows it inside the
    app. Only http(s) addresses are accepted."""
    url = urlparse(text.strip())
    if url.scheme not in ("http", "https") or not url.netloc:
        return None
    clean = f"https://{url.netloc}{url.path}"
    full = url.geturl()
    for pattern, provider, embed in _PATTERNS:
        if m := pattern.match(clean):
            return DocumentLink(url=full, provider=provider, embed_url=embed.format(m.group(1)) if embed else None)
    if url.netloc == "drive.google.com" and url.path == "/open":
        file_id = (parse_qs(url.query).get("id") or [""])[0]
        if re.fullmatch(_GOOGLE_ID, file_id):
            return DocumentLink(url=full, provider="google-drive",
                                embed_url=f"https://drive.google.com/file/d/{file_id}/preview")
    # A direct link to a PDF can be framed as it is.
    if url.scheme == "https" and url.path.lower().endswith(".pdf"):
        return DocumentLink(url=full, provider="web", embed_url=full)
    return DocumentLink(url=full, provider="web")


def default_link_name(link: DocumentLink) -> str:
    """A name to show when the person doesn't give one."""
    if link.provider != "web":
        return PROVIDER_LABELS[link.provider]
    u = urlparse(link.url)
    last = unquote(next(iter(reversed([p for p in u.path.split("/") if p])), ""))
    return last or re.sub(r"^www\.", "", u.netloc)


def format_bytes(n: int) -> str:
    return f"{max(1, round(n / 1024))} KB" if n < 1024 * 1024 else f"{n / 1024 / 1024:.1f} MB"


#: Types a browser can safely show in place; everything else is offered as a download.
_INLINE = re.compile(r"^(image/(png|jpe?g|gif|webp|avif)|application/pdf|audio/.+|video/.+|text/plain)$")


def is_inline_safe(mime: str) -> bool:
    return bool(_INLINE.match(mime.lower()))
