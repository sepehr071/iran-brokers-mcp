"""Shared async HTTP client for the public JSON APIs behind Mofid (Bourse View gateway, emofid.com, learning blog).

Every tool goes through `fetch`, which caps concurrency, caches answers briefly and turns HTTP failures
into `ToolError` messages the model can act on.
"""

from __future__ import annotations

import asyncio
import copy
import json as json_lib
import os
import random
import time
from typing import Any

import httpx
from curl_cffi.const import CurlOpt
from httpx_curl_cffi import AsyncCurlTransport
from mcp.server.mcpserver.exceptions import ToolError

# Site key -> (base URL, Referer). The web apps call these same-origin (checked in Chrome 2026-11-01). Doubles as the host allowlist.
BV = "bv"  # Bourse View gateway, the market data behind bv.emofid.com
WWW = "www"  # emofid.com marketing site API
LEARN = "learn"  # learning.emofid.com WordPress REST
SITES = {
    BV: ("https://bv.emofid.com/gateway", "https://bv.emofid.com/market"),
    WWW: ("https://www.emofid.com/api", "https://www.emofid.com/fa/funds/"),
    LEARN: ("https://learning.emofid.com/wp-json/wp/v2", "https://learning.emofid.com/"),
}

HEADERS = {
    # User-Agent, sec-ch-ua and TLS come from the impersonated Chrome profile (see _get_client).
    "Accept-Language": "fa-IR,fa;q=0.9,en;q=0.8",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
    "priority": "u=1, i",
}

MAX_CONCURRENCY = 2
JITTER = (0.1, 0.4)  # seconds before each uncached call
MAX_RETRIES = 2
MAX_RETRY_WAIT = 10  # cap for Retry-After and backoff, seconds
CACHE_TTL = 30  # seconds; quotes move by the second, lists below ask for more
CACHE_LONG = 3600  # seconds, for lists that change rarely (symbol list, company list, filing types)
CACHE_MAX = 128
RETRY_DELAY = 0.5  # seconds, doubled per retry
MAX_BODY = 6_000_000  # bytes; larger responses are refused

_transport: httpx.AsyncBaseTransport | None = None
_client: httpx.AsyncClient | None = None
_limit: asyncio.Semaphore | None = None
_proxy_index = 0
_retired: set[asyncio.Task] = set()
_cache: dict[tuple, tuple[float, Any]] = {}
_clock_offset = 0  # ms; server epoch minus local epoch, learned from /epoch if the gateway refuses our clock


class _XhrTransport(AsyncCurlTransport):
    """The Chrome profile sends navigation-only headers; a None value makes curl drop them on XHR calls."""

    def _create_request_params(self, req: httpx.Request) -> dict[str, Any]:
        p = super()._create_request_params(req)
        extra = [(b"cache-control", None)]  # Chrome sends max-age=0 only on a reload
        if req.headers.get("sec-fetch-mode") != "navigate":
            extra += [(b"upgrade-insecure-requests", None), (b"sec-fetch-user", None)]
        p["headers"] = [*p["headers"], *extra]
        return p


class ApiError(ToolError):
    """A failed upstream call. `status` is the HTTP status, or None for network errors."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def set_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    """Swap the transport (tests use httpx.MockTransport). Drops the current client."""
    global _transport, _client, _limit, _proxy_index, _clock_offset
    _transport, _client, _limit, _proxy_index, _clock_offset = transport, None, None, 0, 0
    _cache.clear()


def _proxies() -> list[str]:
    """IRAN_BROKERS_MCP_PROXY: one proxy URL, or a comma-separated list used one at a time."""
    return [p.strip() for p in os.environ.get("IRAN_BROKERS_MCP_PROXY", "").split(",") if p.strip()]


def _next_proxy(client: httpx.AsyncClient) -> None:
    """Sticky pool: move to the next proxy after a block or connect error; the next call rebuilds the client.

    Only the client that failed may rotate. The old client is closed late because other requests may still use it.
    """
    global _client, _limit, _proxy_index
    if len(_proxies()) < 2 or client is not _client:
        return
    _client, _limit, _proxy_index = None, None, _proxy_index + 1
    t = asyncio.create_task(_close_later(client))
    _retired.add(t)
    t.add_done_callback(_retired.discard)


async def _close_later(client: httpx.AsyncClient) -> None:
    await asyncio.sleep(30)  # longer than the request timeout
    await client.aclose()


def _get_client() -> tuple[httpx.AsyncClient, asyncio.Semaphore]:
    global _client, _limit
    if _client is None:
        # System proxy settings are ignored; the proxy only comes from IRAN_BROKERS_MCP_PROXY. curl_cffi
        # presents Chrome's TLS/HTTP2 fingerprint and default headers. The proxy goes to the transport.
        proxies = _proxies()
        _client = httpx.AsyncClient(
            transport=_transport
            or _XhrTransport(
                impersonate="chrome",
                default_headers=True,
                curl_options={
                    CurlOpt.NOPROXY: "*"
                },  # httpx-curl-cffi drops its own NOPROXY unless this dict is non-empty
                proxy=proxies[_proxy_index % len(proxies)] if proxies else None,
                max_connections=2 * MAX_CONCURRENCY,
            ),
            headers=HEADERS,
            timeout=httpx.Timeout(25, connect=8),
            limits=httpx.Limits(max_connections=2 * MAX_CONCURRENCY),
            follow_redirects=False,
            trust_env=False,
        )
        _limit = asyncio.Semaphore(MAX_CONCURRENCY)
    assert _limit is not None
    return _client, _limit


def accept_info() -> str:
    """The Bourse View anti-scrape header: (server time in ms) * 4564567 + '.3094776426' (bundle: getInfo())."""
    return f"{(int(time.time() * 1000) + _clock_offset) * 4564567}.3094776426"


async def fetch(
    path: str,
    params: dict[str, Any] | list[tuple[str, Any]] | None = None,
    ttl: int = CACHE_TTL,
    site: str = BV,
    full: bool = False,
) -> Any:
    """GET a Mofid API path the way the web app does and return the parsed JSON body.

    `params` is a dict or a list of pairs (a key may repeat). Retried up to MAX_RETRIES times on timeout,
    network error, 5xx or 429, with jittered exponential backoff or after Retry-After when sent.
    403 is never retried. With full=True the result is (body, response headers) (WordPress paging totals).
    """
    global _clock_offset
    query = list(params.items()) if isinstance(params, dict) else list(params or [])
    base, referer = SITES[site]
    url = base + path
    host = httpx.URL(url).host
    key = (url, tuple(sorted((k, str(v)) for k, v in query)), full)
    if (hit := _cache.get(key)) and time.monotonic() - hit[0] < ttl:
        return copy.deepcopy(hit[1])
    synced = False
    for attempt in range(MAX_RETRIES + 1):
        last = attempt == MAX_RETRIES
        wait = RETRY_DELAY * 2**attempt * random.uniform(1, 1.5)
        headers = {"Referer": referer, "Accept": "application/json, text/plain, */*"}
        if site == BV:
            headers["accept-info"] = accept_info()
        try:
            r, content = await _request(url, query, headers, host)
        except ApiError as e:
            if last or e.status is not None:
                raise
            await asyncio.sleep(min(wait, MAX_RETRY_WAIT))
            continue
        if r.status_code == 401 and site == BV and not synced:
            # Our clock may be off: the app derives the header from the server time.
            synced = True
            try:
                _clock_offset = int(str(await fetch("/epoch", ttl=0)).strip()) - int(time.time() * 1000)
            except (ApiError, ValueError):
                break
            continue
        if (r.status_code == 429 or r.status_code >= 500) and not last:
            await asyncio.sleep(min(_retry_after(r) or wait, MAX_RETRY_WAIT))
            continue
        break

    if r.status_code >= 400:
        raise ApiError(_status_message(r, host, content), r.status_code)
    try:
        body = json_lib.loads(content)
    except ValueError as e:
        raise ApiError(
            f"{host} returned a non-JSON response (HTTP {r.status_code}). Try again in a moment.", r.status_code
        ) from e
    out = (body, dict(r.headers)) if full else body
    if len(_cache) >= CACHE_MAX:
        _cache.pop(next(iter(_cache)))
    _cache[key] = (time.monotonic(), copy.deepcopy(out))
    return out


async def _send(client, limit, url, query, headers, host) -> tuple[httpx.Response, bytes]:
    try:
        async with limit:
            await asyncio.sleep(random.uniform(*JITTER))
            async with client.stream("GET", url, params=query, headers=headers) as r:
                return r, await _read_capped(r, host)
    except httpx.TimeoutException as e:
        raise ApiError(f"{host} did not answer in time. Try again in a moment.") from e
    except httpx.RequestError as e:
        if isinstance(e, httpx.ConnectError | httpx.ProxyError):
            _next_proxy(client)
        raise ApiError(
            f"Could not reach {host} ({type(e).__name__}). Check the internet connection "
            "(or the IRAN_BROKERS_MCP_PROXY setting if you use one)."
        ) from e


async def _request(url, query, headers, host) -> tuple[httpx.Response, bytes]:
    client, limit = _get_client()
    r, content = await _send(client, limit, url, query, headers, host)
    if r.status_code in (403, 429):
        _next_proxy(client)  # blocked: try the next proxy on the following call
    return r, content


async def _read_capped(r: httpx.Response, host: str) -> bytes:
    """Read the body, refusing anything over MAX_BODY so one huge answer cannot flood memory or context."""
    buf = bytearray()
    async for chunk in r.aiter_bytes():
        buf += chunk
        if len(buf) > MAX_BODY:
            raise ApiError(f"{host} sent a response over {MAX_BODY // 1_000_000} MB; refused.", r.status_code)
    return bytes(buf)


def _retry_after(r: httpx.Response) -> float | None:
    """Seconds from a numeric Retry-After on a 429/503, else None (HTTP-date form is ignored)."""
    v = r.headers.get("retry-after", "")
    return float(v) if r.status_code in (429, 503) and v.replace(".", "", 1).isdigit() else None


def _status_message(r: httpx.Response, host: str, content: bytes) -> str:
    code = r.status_code
    if code in (400, 422):
        try:
            msg = json_lib.loads(content).get("message")
            msg = msg.get("message", msg) if isinstance(msg, dict) else msg
        except (ValueError, AttributeError):
            msg = None
        return (
            f"{host} rejected the request (HTTP {code}): {str(msg)[:300]}"
            if msg
            else f"{host} rejected the request (HTTP {code})."
        )
    if code == 401:
        return (
            f"{host} refused the request (HTTP 401). The gateway header check failed or this data needs a Mofid login."
        )
    if code == 403:
        return f"{host} blocked the request (HTTP 403). Try another network or set IRAN_BROKERS_MCP_PROXY."
    if code == 404:
        return f"{host} found nothing at that address (HTTP 404). Check the id or symbol."
    if code == 429:
        return f"{host} is rate limiting requests (HTTP 429). Wait a minute before retrying."
    if code >= 500:
        return f"{host} had a server error (HTTP {code}). Try again later."
    return f"{host} rejected the request (HTTP {code})."
