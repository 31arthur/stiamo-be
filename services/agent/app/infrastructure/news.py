"""Reading news feeds: the items of an RSS feed, the real address behind a Google News link, and what the publisher's own page says.

Google News feeds link to a redirect page, not the article, and say little more than the headline and the paper. So an article is
read in three steps: the feed item, the publisher's address (decoded from the Google link), and the publisher's page for its
description and picture. Only articles that are new are read this far: the rest are known by their id and skipped before any page is fetched.
"""

from __future__ import annotations

import asyncio
import base64
import html
import json
import re
import xml.etree.ElementTree as ET
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}
MEDIA = "{http://search.yahoo.com/mrss/}content"
FEED_CONCURRENCY = 6
PAGE_CONCURRENCY = 6
MAX_PAGE_BYTES = 600_000  # the head of a page holds what is wanted; a long article needs no more


# --- the feed ---------------------------------------------------------------------------------------------


def clean_text(value: str) -> str:
    """Plain text from a snippet of HTML: tags gone, entities decoded, white space tidied."""
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", value or ""))).strip()


def parse_rss(xml_text: str, lang: str, key: str, now: datetime | None = None, max_age_days: int | None = 3) -> list[dict[str, Any]]:
    """The articles of an RSS feed, newest first, no older than `max_age_days`. A feed that isn't XML gives none."""
    now = now or datetime.now(UTC)
    try:
        root = ET.fromstring(xml_text.strip().encode("utf-8"))
    except ET.ParseError:
        return []
    out: list[dict[str, Any]] = []
    for item in root.iter("item"):
        def text(tag: str, item: ET.Element = item) -> str:
            return (item.findtext(tag) or "").strip()

        title, link = clean_text(text("title")), text("link")
        if not title or not link:
            continue
        source_el = item.find("source")
        source = clean_text(source_el.text or "") if source_el is not None else ""
        if source and title.endswith(f" - {source}"):  # Google News puts the paper at the end of the headline
            title = title[: -len(source) - 3].strip()
        try:
            published = parsedate_to_datetime(text("pubDate")).astimezone(UTC)
        except (TypeError, ValueError):
            published = now
        if max_age_days and now - published > timedelta(days=max_age_days):
            continue
        media = item.find(MEDIA)
        enclosure = item.find("enclosure")
        image = (media.get("url") if media is not None else None) or (enclosure.get("url") if enclosure is not None else None)
        snippet = clean_text(text("description"))
        out.append({
            "guid": text("guid") or link, "title": title[:300], "link": link, "source": source or "Google News",
            "source_url": (source_el.get("url") if source_el is not None else None) or "", "published_at": published.isoformat(timespec="seconds").replace("+00:00", "Z"),
            # Google's own snippet is just the headline and the paper again: not worth keeping as a description.
            "description": snippet if snippet and snippet.lower() not in (title.lower(), f"{title} {source}".lower()) and len(snippet) > 60 else None,
            "image_url": image, "lang": lang, "feed_key": key,
        })
    return sorted(out, key=lambda a: a["published_at"], reverse=True)


# --- the publisher's page ---------------------------------------------------------------------------------

_DESC = [
    r"""<meta[^>]+property=["']og:description["'][^>]+content=["']([^"']{20,1000})["']""",
    r"""<meta[^>]+content=["']([^"']{20,1000})["'][^>]+property=["']og:description["']""",
    r"""<meta[^>]+name=["']description["'][^>]+content=["']([^"']{20,1000})["']""",
    r"""<meta[^>]+content=["']([^"']{20,1000})["'][^>]+name=["']description["']""",
    r"""<meta[^>]+name=["']twitter:description["'][^>]+content=["']([^"']{20,1000})["']""",
    r"""<meta[^>]+itemprop=["']description["'][^>]+content=["']([^"']{20,1000})["']""",
    r""""description"\s*:\s*"([^"]{20,1000})\"""",
]
_IMAGE = [
    r"""<meta[^>]+property=["']og:image["'][^>]+content=["']([^"']+)["']""",
    r"""<meta[^>]+content=["']([^"']+)["'][^>]+property=["']og:image["']""",
    r"""<meta[^>]+property=["']og:image:secure_url["'][^>]+content=["']([^"']+)["']""",
    r"""<meta[^>]+name=["']twitter:image(?::src)?["'][^>]+content=["']([^"']+)["']""",
    r"""<meta[^>]+itemprop=["']image["'][^>]+content=["']([^"']+)["']""",
    r"""<link[^>]+rel=["']image_src["'][^>]+href=["']([^"']+)["']""",
    r""""image"\s*:\s*(?:"([^"]+)"|\{[^}]*"url"\s*:\s*"([^"]+)")""",
]
_GOOGLE = ("news.google.com", "google_news", "gstatic.com", "Google News")


def extract_meta(page: str) -> tuple[str | None, str | None]:
    """The page's own description and picture. The page's tags first; if it has none, the first real paragraphs of the article."""
    description = image = None
    for pattern in _DESC:
        m = re.search(pattern, page, re.IGNORECASE)
        if m:
            text = clean_text(m.group(1))
            if len(text) >= 20 and not any(g in text for g in _GOOGLE):
                description = text
                break
    if description is None:
        paragraphs = [clean_text(p) for p in re.findall(r"<p[^>]*>(.*?)</p>", page, re.IGNORECASE | re.DOTALL)]
        good = [p for p in paragraphs if len(p) >= 80 and not any(g in p for g in _GOOGLE)][:3]
        description = " ".join(good)[:600] or None
    for pattern in _IMAGE:
        m = re.search(pattern, page, re.IGNORECASE)
        val = (m.group(1) or (m.group(2) if m.lastindex and m.lastindex > 1 else None)) if m else None
        if val:
            val = val.strip()
            if val.startswith("//"):
                val = "https:" + val
            if val.startswith("http") and not any(g in val for g in _GOOGLE):
                image = html.unescape(val)
                break
    return description, image


def old_style_url(token: str) -> str | None:
    """Older Google News links carry the article's address inside them, in base64. Newer ones don't."""
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
    except (ValueError, TypeError):
        return None
    m = re.search(rb"https?://[^\x00-\x20\"'<>\\\x7f-\xff]+", raw)
    return m.group(0).decode() if m else None


async def decode_google_news(http: httpx.AsyncClient, url: str) -> str | None:
    """The publisher's address behind a Google News link, or None when Google won't say."""
    token = url.rstrip("/").rsplit("/", 1)[-1].split("?")[0]
    if not token:
        return None
    found = old_style_url(token)
    if found:
        return found
    try:
        sig = ts = None
        for page in (f"https://news.google.com/articles/{token}", f"https://news.google.com/rss/articles/{token}"):
            r = await http.get(page, headers=HEADERS, follow_redirects=True, timeout=10)
            sig, ts = re.search(r'data-n-a-sg="([^"]+)"', r.text), re.search(r'data-n-a-ts="([^"]+)"', r.text)
            if sig and ts:
                break
        if not (sig and ts):
            return None
        request = ["Fbv4je", json.dumps(["garturlreq", [["X", "X", ["X", "X"], None, None, 1, 1, "US:en", None, 1, None, None, None, None, None, 0, 1], "X", "X", 1, [1, 1, 1], 1, 1, None, 0, 0, None, 0], token, int(ts.group(1)), sig.group(1)])]
        r = await http.post("https://news.google.com/_/DotsSplashUi/data/batchexecute", content=f"f.req={httpx.QueryParams({'x': json.dumps([[request]])})['x']}".encode(), headers={**HEADERS, "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"}, timeout=10)
        data = json.loads(r.text.split("\n\n")[1])
        return json.loads(data[0][2])[1] or None
    except Exception:  # noqa: BLE001  Google changes this now and then: an article without its address is still an article
        return None


async def read_page(http: httpx.AsyncClient, article: dict[str, Any]) -> None:
    """Fills in the article's real address, description and picture from the publisher, as far as they can be had. Never raises."""
    link = article["link"]
    if "news.google.com" in link:
        real = await decode_google_news(http, link)
        if real:
            article["link"] = real
    targets = [article["link"]] if "news.google.com" not in article["link"] else []
    if article.get("source_url") and article["source_url"] not in targets:
        targets.append(article["source_url"])  # the paper's front page at least has its own description and logo
    for url in targets:
        try:
            r = await http.get(url, headers=HEADERS, follow_redirects=True, timeout=8)
            if r.status_code >= 400:
                continue
            description, image = extract_meta(r.text[:MAX_PAGE_BYTES])
        except Exception:  # noqa: BLE001
            continue
        if url == article["link"]:
            article["description"] = description or article.get("description")
            article["image_url"] = article.get("image_url") or image
            if description or image:
                return
        elif not article.get("image_url"):
            article["image_url"] = image


async def fetch_articles(http: httpx.AsyncClient, feeds: list[dict[str, Any]], since_days: int | None, known: Callable[[dict[str, Any]], bool], limit: int) -> tuple[list[dict[str, Any]], int]:
    """The newest articles across the feeds that `known` doesn't recognise, up to `limit`, each read from its publisher.
    Also how many new articles there are in all, so the caller knows whether to come back for more."""
    gate = asyncio.Semaphore(FEED_CONCURRENCY)

    async def one(feed: dict[str, Any]) -> list[dict[str, Any]]:
        async with gate:
            try:
                r = await http.get(feed["url"], headers=HEADERS, follow_redirects=True, timeout=15)
                return parse_rss(r.text, feed.get("lang", "en"), feed.get("key", ""), max_age_days=since_days) if r.status_code == 200 else []
            except Exception:  # noqa: BLE001  one feed down is not a reason to lose the rest
                return []

    seen: set[str] = set()
    fresh: list[dict[str, Any]] = []
    for batch in await asyncio.gather(*(one(f) for f in feeds)):
        for a in batch:
            if a["guid"] not in seen and not known(a):
                seen.add(a["guid"])
                fresh.append(a)
    fresh.sort(key=lambda a: a["published_at"], reverse=True)
    chosen = fresh[:limit]
    page_gate = asyncio.Semaphore(PAGE_CONCURRENCY)

    async def read(a: dict[str, Any]) -> None:
        async with page_gate:
            await read_page(http, a)

    await asyncio.gather(*(read(a) for a in chosen))
    return chosen, len(fresh)
