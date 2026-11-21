"""What a link looks like when it is shared: the title, description and picture a page offers for sharing (Open Graph), which is what WhatsApp shows under a message.

The page is fetched by this server, so it is fetched carefully: only http and https, never an address inside a private network, a short time, a small size.
"""

from __future__ import annotations

import ipaddress
import socket
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

MAX_BYTES = 600_000
#: WhatsApp's own crawler name: some sites show their share preview only to it.
UA = "WhatsApp/2.23.20.0 A"


class Meta(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.meta: dict[str, str] = {}
        self.title = ""
        self._in_title = False
        self.canonical = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "meta":
            key = (a.get("property") or a.get("name") or "").lower()
            if key and a.get("content") and key not in self.meta:
                self.meta[key] = a["content"].strip()
        elif tag == "title":
            self._in_title = True
        elif tag == "link" and "canonical" in a.get("rel", "").lower() and a.get("href"):
            self.canonical = a["href"]

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title and len(self.title) < 300:
            self.title += data


def parse_preview(html: str, url: str) -> dict[str, str]:
    """{title, description, image, site, url} from a page's own sharing tags, falling back to its title."""
    p = Meta()
    try:
        p.feed(html[:MAX_BYTES])
    except Exception:  # noqa: BLE001  a page that cannot be read to the end still gave what it gave
        pass
    m = p.meta
    clean = lambda s, n: " ".join(s.split())[:n]  # noqa: E731
    image = m.get("og:image") or m.get("og:image:url") or m.get("twitter:image") or ""
    host = urlparse(url).netloc.removeprefix("www.")
    return {"title": clean(m.get("og:title") or m.get("twitter:title") or p.title, 160), "description": clean(m.get("og:description") or m.get("twitter:description") or m.get("description") or "", 300),
            "image": urljoin(url, image) if image else "", "site": clean(m.get("og:site_name") or host, 80), "url": m.get("og:url") or p.canonical or url, "host": host}


def is_public_url(url: str) -> bool:
    """http(s) only, and a host that is not on a private, loopback or link-local address."""
    try:
        u = urlparse(url)
        if u.scheme not in ("http", "https") or not u.hostname:
            return False
        for info in socket.getaddrinfo(u.hostname, u.port or (443 if u.scheme == "https" else 80), proto=socket.IPPROTO_TCP):
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
                return False
        return True
    except (OSError, ValueError):
        return False


class HttpLinkPreview:
    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    async def fetch(self, url: str) -> dict[str, Any]:
        if not is_public_url(url):
            return {"error": "That address can't be opened from here. Use a public web address (https://…)."}
        try:
            async with self._http.stream("GET", url, headers={"User-Agent": UA, "Accept": "text/html,*/*;q=0.8"}, timeout=8, follow_redirects=True) as r:
                if r.status_code >= 400:
                    return {"error": f"The page answered {r.status_code}."}
                if not is_public_url(str(r.url)):
                    return {"error": "That address leads somewhere that can't be opened from here."}
                kind = r.headers.get("content-type", "")
                if kind.startswith("image/"):
                    return {"title": "", "description": "", "image": str(r.url), "site": urlparse(str(r.url)).netloc.removeprefix("www."), "url": str(r.url), "host": urlparse(str(r.url)).netloc.removeprefix("www.")}
                body = b""
                async for chunk in r.aiter_bytes():
                    body += chunk
                    if len(body) > MAX_BYTES:
                        break
                return parse_preview(body.decode(r.encoding or "utf-8", errors="replace"), str(r.url))
        except httpx.HTTPError:
            return {"error": "The page couldn't be reached."}
