"""Small helpers shared by the tool modules: symbol lookup, date decoding, response unwrapping."""

from __future__ import annotations

from datetime import date
from typing import Any

from . import http

ACTIONS = {2: "strong buy", 1: "buy", 0: "neutral", -1: "sell", -2: "strong sell"}  # inferred from buy/sell counts


def norm(s: str) -> str:
    """Arabic ye/kaf to Persian, ZWNJ and spaces out, lower case: the site's lists mix both spellings."""
    return str(s).replace("ي", "ی").replace("ك", "ک").replace("\u200c", "").replace(" ", "").lower()


def day(n: Any) -> str | None:
    """Series points carry the day as a proleptic Gregorian ordinal (date.toordinal()); return an ISO date."""
    try:
        return date.fromordinal(int(n)).isoformat()
    except (TypeError, ValueError, OverflowError):
        return None


def data(body: Any) -> Any:
    """The research API wraps everything as {"data": ..., "isSuccess": true}; 204 bodies are None."""
    return body.get("data") if isinstance(body, dict) else body


async def research(path: str, params: dict[str, Any] | list[tuple[str, Any]] | None = None, ttl: int = http.CACHE_TTL):
    return data(await http.fetch(f"{http.RESEARCH}/{path}", params, ttl))


async def stock_list() -> list[dict]:
    return await research("stock/all", ttl=http.CACHE_LONG) or []


async def fund_list() -> list[dict]:
    return await research("fund/all", ttl=http.CACHE_LONG) or []


async def stock_isin(query: str) -> str:
    """Resolve a symbol (فولاد), part of a company name or an ISIN to an ISIN; raise if it is not a single match."""
    q = norm(query)
    stocks = await stock_list()
    if len(q) == 12 and q.startswith("iro"):
        return q.upper()
    exact = [s for s in stocks if norm(s["symbol"]) == q]
    if len(exact) == 1:
        return exact[0]["isin"]
    hits = exact or [s for s in stocks if q in norm(s["symbol"]) or q in norm(s.get("companyName", ""))]
    if len(hits) == 1:
        return hits[0]["isin"]
    names = ", ".join(f"{s['symbol']} ({s['isin']})" for s in hits[:8])
    raise http.ApiError(f"{'No stock' if not hits else 'Several stocks'} match '{query}'. {names}".strip())


async def fund(query: str) -> dict:
    """Resolve a fund national code, name or symbol to its fund/all row."""
    q = norm(query)
    funds = await fund_list()
    exact = [f for f in funds if q in (norm(f["nationalCode"]), norm(f["name"]), norm(f.get("symbol", "")))]
    if len(exact) == 1:
        return exact[0]
    hits = exact or [f for f in funds if q in norm(f["name"]) or q in norm(f.get("symbol", ""))]
    if len(hits) == 1:
        return hits[0]
    names = ", ".join(f"{f['name']} ({f['nationalCode']})" for f in hits[:8])
    raise http.ApiError(f"{'No fund' if not hits else 'Several funds'} match '{query}'. {names}".strip())


def tail(rows: list, n: int) -> list:
    return rows[-n:] if n > 0 else rows
