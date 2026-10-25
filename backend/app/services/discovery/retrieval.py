"""Full-text retrieval for candidate sources.

Rules:
* Only open sources are fetched: Wikipedia via its API, open-access PDFs
  (OpenAlex / Semantic Scholar / arXiv links), and public web pages returned
  by a licensed search API.
* robots.txt is honoured for web pages; nothing behind a login or paywall is
  circumvented. If the full text is not reachable, the abstract or snippet
  is used and the source is labelled accordingly in the report.
* Fetched public text is cached (FetchCache) to avoid re-downloading.
"""

import hashlib
import logging
import re
import threading
from datetime import timedelta
from html.parser import HTMLParser
from urllib import robotparser
from urllib.parse import urlparse

import httpx

from ...config import get_settings
from ...db import session_scope
from ...models import FetchCache, utcnow
from ..extraction.parsers import ExtractionError
from ..extraction.sandbox import extract
from .http import FetchBlocked, fetch_resource
from .providers import Candidate, WikipediaProvider

log = logging.getLogger(__name__)

MAX_PDF_BYTES = 15 * 1024 * 1024
MAX_HTML_BYTES = 3 * 1024 * 1024
FETCH_CACHE_DAYS = 30


class _TextExtractor(HTMLParser):
    """Collects readable text from article-like HTML, skipping navigation,
    scripts and boilerplate containers."""

    SKIP = {"script", "style", "noscript", "nav", "header", "footer", "aside", "form", "svg", "button", "select", "template"}
    BLOCK = {"p", "li", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "td", "th", "pre", "dd", "dt", "figcaption", "div", "section", "article", "br"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip_depth += 1
        elif tag == "title":
            self._in_title = True
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip_depth:
            self._skip_depth -= 1
        elif tag == "title":
            self._in_title = False
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self._skip_depth:
            self.parts.append(data)


def html_to_text(raw: str) -> tuple[str, str]:
    parser = _TextExtractor()
    parser.feed(raw)
    parser.close()
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in "".join(parser.parts).split("\n")]
    # Keep lines that look like prose: navigation menus are short fragments.
    prose = [ln for ln in lines if len(ln) >= 60 or (len(ln) >= 25 and ln[-1:] in ".!?:")]
    return "\n\n".join(prose), parser.title.strip()


class RobotsCache:
    def __init__(self, client: httpx.Client, user_agent: str):
        self.client = client
        self.user_agent = user_agent
        self._cache: dict[str, robotparser.RobotFileParser | None] = {}
        self._lock = threading.Lock()

    def allowed(self, url: str) -> bool:
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        with self._lock:
            if origin not in self._cache:
                rp = robotparser.RobotFileParser()
                try:
                    res = fetch_resource(self.client, origin + "/robots.txt", 512 * 1024, "text/plain")
                    rp.parse(res.content.decode("utf-8", errors="replace").splitlines())
                    self._cache[origin] = rp
                except FetchBlocked as e:
                    # RFC 9309: a 4xx robots.txt means no restrictions; a
                    # server error or unreachable host means assume full
                    # disallow (an unparsed RobotFileParser denies everything).
                    self._cache[origin] = None if "HTTP 4" in str(e) or "access denied" in str(e) else rp
                except httpx.HTTPError:
                    self._cache[origin] = rp
            rp = self._cache[origin]
        return True if rp is None else rp.can_fetch(self.user_agent, url)


def _cache_key(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()


def _cached(url: str) -> FetchCache | None:
    with session_scope() as db:
        row = db.get(FetchCache, _cache_key(url))
        if row is None:
            return None
        created = row.created_at if row.created_at.tzinfo else row.created_at.replace(tzinfo=utcnow().tzinfo)
        if utcnow() - created > timedelta(days=FETCH_CACHE_DAYS):
            return None
        db.expunge(row)
        return row


def _store(url: str, ok: bool, detail: str, title: str = "", text: str = "") -> None:
    try:
        with session_scope() as db:
            row = db.get(FetchCache, _cache_key(url))
            if row is None:
                db.add(FetchCache(key=_cache_key(url), url=url[:2000], ok=ok, detail=detail[:300], title=title[:500], text=text))
            else:
                row.ok, row.detail, row.title, row.text, row.created_at = ok, detail[:300], title[:500], text, utcnow()
    except Exception:  # noqa: BLE001
        log.exception("fetch cache write failed")


def fetch_text(client: httpx.Client, robots: RobotsCache, url: str, expect_pdf: bool) -> tuple[str, str, str]:
    """Return (text, title, note). Raises FetchBlocked with a reason."""
    cached = _cached(url)
    if cached is not None:
        if not cached.ok:
            raise FetchBlocked(cached.detail)
        return cached.text, cached.title, "cached"
    if not robots.allowed(url):
        _store(url, False, "disallowed by robots.txt")
        raise FetchBlocked("disallowed by robots.txt")
    try:
        res = fetch_resource(
            client,
            url,
            MAX_PDF_BYTES if expect_pdf else MAX_HTML_BYTES,
            "application/pdf" if expect_pdf else "text/html,application/xhtml+xml,application/pdf;q=0.9",
        )
    except FetchBlocked as e:
        _store(url, False, str(e))
        raise
    title = ""
    if res.content.startswith(b"%PDF-") or "pdf" in res.content_type:
        try:
            text = extract("source.pdf", res.content).text
        except ExtractionError as e:
            _store(url, False, f"PDF unreadable: {e}")
            raise FetchBlocked(f"PDF unreadable: {e}") from e
    elif "html" in res.content_type or res.content.lstrip()[:15].lower().startswith((b"<!doctype", b"<html")):
        text, title = html_to_text(res.content.decode("utf-8", errors="replace"))
    elif res.content_type.startswith("text/plain"):
        text = res.content.decode("utf-8", errors="replace")
    else:
        _store(url, False, f"unsupported content type {res.content_type or 'unknown'}")
        raise FetchBlocked("unsupported content type")
    text = text[: get_settings().max_source_chars]
    if len(text.split()) < 80:
        _store(url, False, "too little readable text (possible paywall or script-rendered page)")
        raise FetchBlocked("too little readable text (possible paywall or script-rendered page)")
    _store(url, True, "ok", title, text)
    return text, title, "fetched"


def retrieve_full_text(
    candidate: Candidate,
    client: httpx.Client,
    robots: RobotsCache,
    wikipedia: WikipediaProvider | None,
) -> tuple[str, str, str]:
    """Best available text for a candidate: (text, text_kind, note)."""
    settings = get_settings()
    if candidate.provider == "wikipedia" and wikipedia and candidate.provider_id:
        try:
            text = wikipedia.full_text(candidate.provider_id)
            if text:
                return text[: settings.max_source_chars], "full_text", "Wikipedia API"
        except Exception as e:  # noqa: BLE001
            return candidate.text, candidate.text_kind, f"Wikipedia extract failed: {e}"
    if not settings.fetch_full_text:
        return candidate.text, candidate.text_kind, "full-text retrieval disabled"
    targets = []
    if candidate.pdf_url:
        targets.append((candidate.pdf_url, True))
    if candidate.kind == "web" and candidate.url:
        targets.append((candidate.url, False))
    notes = []
    for url, is_pdf in targets:
        try:
            text, _title, note = fetch_text(client, robots, url, is_pdf)
            return text, "full_text", f"open access {'PDF' if is_pdf else 'page'} ({note})"
        except FetchBlocked as e:
            notes.append(str(e))
        except httpx.HTTPError as e:
            notes.append(f"network error ({type(e).__name__})")
    if not targets:
        notes.append("no open-access full text available")
    return candidate.text, candidate.text_kind, "; ".join(notes)
