"""HTTP access for external providers: rate limiting, retries, caching,
circuit breaking and SSRF-safe page fetching."""

import hashlib
import ipaddress
import json
import logging
import socket
import threading
import time
from dataclasses import dataclass, field
from datetime import timedelta
from urllib.parse import urljoin, urlparse

import httpx
from sqlalchemy import delete

from ...config import get_settings
from ...db import session_scope
from ...models import SearchCache, utcnow

log = logging.getLogger(__name__)

# Requests per second allowed per host, from each provider's published policy.
HOST_RATES = {
    "api.openalex.org": 8.0,
    "api.crossref.org": 5.0,
    "api.semanticscholar.org": 0.9,  # shared unauthenticated pool: 1 rps
    "export.arxiv.org": 1 / 3.0,  # arXiv API terms: one request every 3 seconds
    "wikipedia.org": 5.0,
    "api.search.brave.com": 1.0,
    "www.googleapis.com": 5.0,
}
DEFAULT_RATE = 2.0


class ProviderError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class _HostLimiter:
    def __init__(self) -> None:
        self._next: dict[str, float] = {}
        self._lock = threading.Lock()

    def wait(self, host: str) -> None:
        rate = next((r for h, r in HOST_RATES.items() if host == h or host.endswith("." + h)), DEFAULT_RATE)
        interval = 1.0 / rate
        with self._lock:
            now = time.monotonic()
            slot = max(now, self._next.get(host, 0.0))
            self._next[host] = slot + interval
        delay = slot - now
        if delay > 0:
            time.sleep(delay)


limiter = _HostLimiter()


@dataclass
class CallStats:
    requests: int = 0
    successes: int = 0
    cache_hits: int = 0
    errors: list[str] = field(default_factory=list)
    status: str = "ok"  # ok | degraded | unavailable | skipped | disabled


class ProviderClient:
    """One instance per provider per analysis job."""

    def __init__(self, provider: str, client: httpx.Client | None = None, use_cache: bool = True):
        self.provider = provider
        self.settings = get_settings()
        self.client = client or httpx.Client(
            timeout=self.settings.http_timeout_s,
            headers={"User-Agent": self._user_agent()},
            follow_redirects=True,
        )
        self.use_cache = use_cache
        self.stats = CallStats()
        self._consecutive_failures = 0

    def _user_agent(self) -> str:
        ua = self.settings.user_agent
        if self.settings.contact_email:
            ua += f" (mailto:{self.settings.contact_email})"
        return ua

    @property
    def available(self) -> bool:
        return self.stats.status != "unavailable"

    def _cache_key(self, url: str, params: dict | None) -> str:
        raw = json.dumps([self.provider, url, sorted((params or {}).items())], default=str)
        return hashlib.sha256(raw.encode()).hexdigest()

    def get_json(self, url: str, params: dict | None = None, headers: dict | None = None, cache: bool = True):
        return self._get(url, params, headers, cache, parse="json")

    def get_text(self, url: str, params: dict | None = None, headers: dict | None = None, cache: bool = True) -> str:
        return self._get(url, params, headers, cache, parse="text")

    def _get(self, url: str, params, headers, cache: bool, parse: str):
        if not self.available:
            raise ProviderError(f"{self.provider} is unavailable for this analysis")
        key = self._cache_key(url, params)
        if cache and self.use_cache:
            cached = _cache_get(key, self.settings.search_cache_ttl_hours)
            if cached is not None:
                self.stats.cache_hits += 1
                return json.loads(cached) if parse == "json" else cached
        host = urlparse(url).hostname or ""
        last_error: Exception | None = None
        for attempt in range(3):
            limiter.wait(host)
            self.stats.requests += 1
            try:
                resp = self.client.get(url, params=params, headers=headers)
            except (httpx.ConnectError, httpx.ProxyError, httpx.UnsupportedProtocol) as e:
                # DNS failure, refused connection or blocked by a proxy: not
                # transient, so fail fast instead of backing off.
                self._failure(f"cannot connect ({type(e).__name__})", hard=True)
                raise ProviderError(f"cannot connect to {host}") from e
            except httpx.HTTPError as e:
                last_error = ProviderError(f"network error: {type(e).__name__}")
                time.sleep(min(8, 2**attempt))
                continue
            if resp.status_code == 404:
                self._success()
                raise ProviderError("not found", 404)
            if resp.status_code == 429 or resp.status_code >= 500:
                retry_after = resp.headers.get("retry-after", "")
                wait = float(retry_after) if retry_after.isdigit() else 2 ** (attempt + 1)
                last_error = ProviderError(f"HTTP {resp.status_code}", resp.status_code)
                time.sleep(min(wait, 20))
                continue
            if resp.status_code >= 400:
                self._failure(f"HTTP {resp.status_code}")
                raise ProviderError(f"HTTP {resp.status_code}", resp.status_code)
            body = resp.json() if parse == "json" else resp.text
            if cache and self.use_cache:
                _cache_put(key, self.provider, json.dumps(body) if parse == "json" else body)
            self._success()
            return body
        self._failure(str(last_error))
        raise last_error or ProviderError("request failed")

    def _success(self) -> None:
        self._consecutive_failures = 0
        self.stats.successes += 1

    def _failure(self, message: str, hard: bool = False) -> None:
        self._consecutive_failures += 2 if hard else 1
        if message not in self.stats.errors:
            self.stats.errors.append(message)
        self.stats.status = "degraded"
        if self._consecutive_failures >= 3:
            # Circuit breaker: stop hammering a failing provider for this job.
            self.stats.status = "unavailable"

    def close(self) -> None:
        self.client.close()


def _cache_get(key: str, ttl_hours: int) -> str | None:
    try:
        with session_scope() as db:
            row = db.get(SearchCache, key)
            if row is None:
                return None
            created = row.created_at if row.created_at.tzinfo else row.created_at.replace(tzinfo=utcnow().tzinfo)
            if utcnow() - created > timedelta(hours=ttl_hours):
                db.delete(row)
                return None
            return row.payload
    except Exception:  # noqa: BLE001 - cache problems must never fail an analysis
        log.exception("search cache read failed")
        return None


def _cache_put(key: str, provider: str, payload: str) -> None:
    try:
        with session_scope() as db:
            row = db.get(SearchCache, key)
            if row:
                row.payload, row.created_at = payload, utcnow()
            else:
                db.add(SearchCache(key=key, provider=provider, payload=payload))
    except Exception:  # noqa: BLE001
        log.exception("search cache write failed")


def purge_cache(older_than_hours: int) -> int:
    with session_scope() as db:
        cutoff = utcnow() - timedelta(hours=older_than_hours)
        result = db.execute(delete(SearchCache).where(SearchCache.created_at < cutoff))
        return result.rowcount or 0


# ---------------------------------------------------------------- page fetch


class FetchBlocked(Exception):
    pass


def _check_public_host(url: str) -> None:
    """Reject URLs that resolve to private, loopback or link-local addresses
    (server-side request forgery protection)."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise FetchBlocked("unsupported URL")
    if parsed.port not in (None, 80, 443):
        raise FetchBlocked("non-standard port")
    try:
        infos = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror as e:
        raise FetchBlocked("host not found") from e
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            raise FetchBlocked("address not public")


@dataclass
class FetchedResource:
    url: str
    content_type: str
    content: bytes


def fetch_resource(client: httpx.Client, url: str, max_bytes: int, accept: str = "*/*") -> FetchedResource:
    """GET a public URL with manual, re-validated redirects and a size cap."""
    for _ in range(5):
        _check_public_host(url)
        limiter.wait(urlparse(url).hostname or "")
        with client.stream("GET", url, headers={"Accept": accept}, follow_redirects=False) as resp:
            if resp.is_redirect:
                location = resp.headers.get("location")
                if not location:
                    raise FetchBlocked("bad redirect")
                url = urljoin(url, location)
                continue
            if resp.status_code in (401, 402, 403):
                raise FetchBlocked(f"access denied (HTTP {resp.status_code}) - likely paywalled or bot-protected")
            if resp.status_code >= 400:
                raise FetchBlocked(f"HTTP {resp.status_code}")
            declared = resp.headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > max_bytes:
                raise FetchBlocked("resource too large")
            chunks, size = [], 0
            for chunk in resp.iter_bytes():
                size += len(chunk)
                if size > max_bytes:
                    raise FetchBlocked("resource too large")
                chunks.append(chunk)
            return FetchedResource(str(resp.url), resp.headers.get("content-type", "").lower(), b"".join(chunks))
    raise FetchBlocked("too many redirects")
