"""Tools that answer from both brokers (Mofid, Agah) through a `source` argument, plus Agah's fund and option wrappers."""

from __future__ import annotations

import asyncio
import re
from typing import Annotated, Any, Literal

from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from .agah import funds as agah_funds
from .agah import learn as agah_learn
from .agah import market as agah_market
from .agah.http import ApiError as AgahError
from .mofid import assets as mofid_assets
from .mofid import info as mofid_info
from .mofid import market as mofid_market
from .mofid.http import ApiError as MofidError
from .mofid.util import norm
from .registry import tool
from .sources import Source, by_source

ISIN = re.compile(r"IR[A-Za-z0-9]{8,}")  # stock, fund, option and index ids all look like this


async def _mofid_isin(q: str) -> str:
    """Mofid tools take an ISIN: pass one through, or resolve an exact symbol through the ticker list."""
    q = q.strip()
    if ISIN.fullmatch(q):
        return q.upper()
    for kind in ("stock", "etf", "all"):  # a stock beats a same-named commodity or certificate
        hits = (await mofid_market.search(q, kind, 50))["results"]
        exact = [h for h in hits if norm(h["symbol"]) == norm(q)]
        one = exact or (hits if len(hits) == 1 else [])
        if len(one) == 1:
            return one[0]["isin"]
        if hits:
            break
    names = ", ".join(f"{h['symbol']} ({h['isin']})" for h in (exact or hits)[:8])
    raise MofidError(f"{'No symbol' if not hits else 'Several symbols'} match {q!r} on Mofid. {names}".strip())


@tool("Search symbols, funds, options and indices")
async def br_search(
    query: Annotated[
        str,
        Field(
            description="Part of a symbol, company or fund name, or an ISIN; Persian or Latin, e.g. 'فولاد'.",
            min_length=1,
        ),
    ],
    kind: Annotated[
        Literal["stock", "fund", "option", "index", "all"],
        Field(description="fund = ETFs and funds, index = market and industry indices."),
    ] = "stock",
    limit: Annotated[int, Field(description="Maximum results per source.", ge=1, le=50)] = 10,
    source: Source = "all",
) -> dict[str, Any]:
    """Find the id of a stock, fund, option or index by name or symbol, in Mofid and Agah.

    Use it first when you only have a name. Mofid results carry `isin`, Agah results `id` (funds have a different id
    in each source); pass either to br_stock, br_history, br_fund or br_options.
    """
    mk = {"stock": "stock", "fund": "etf", "option": "option", "index": "index", "all": "all"}[kind]
    ak = {"stock": "stock", "fund": "fund", "option": "option", "index": "industry", "all": "any"}[kind]
    return await by_source(
        source,
        {
            "mofid": lambda: mofid_market.search(query, mk, limit),
            "agah": lambda: agah_market.search(query, ak, limit),
        },
    )


@tool("One stock or ETF in detail")
async def br_stock(
    symbol: Annotated[
        str,
        Field(description="Symbol (فولاد), part of the company name, or ISIN. ETF symbols work on Mofid."),
    ],
    order_book: Annotated[bool, Field(description="Also return Mofid's five best bid and ask levels.")] = False,
    source: Source = "all",
) -> dict[str, Any]:
    """Show one stock (or, on Mofid, an ETF) in detail: price, ratios, returns, real vs legal flows, from Mofid and Agah.

    Use it after br_search. The two sources have different fields, so both are shown. Prices are Rial. Next: br_history,
    br_stock_events (dividends), br_technical; for an Agah fund use br_fund.
    """

    async def mofid() -> dict[str, Any]:
        isin = await _mofid_isin(symbol)
        if not order_book:
            return await mofid_market.symbol_card(isin)
        card, book = await asyncio.gather(
            mofid_market.symbol_card(isin), mofid_market.depth(isin), return_exceptions=True
        )
        if isinstance(card, BaseException):
            raise card
        card["order_book"] = {"error": str(book)} if isinstance(book, Exception) else book
        return card

    return await by_source(source, {"mofid": mofid, "agah": lambda: agah_market.stock(symbol)})


@tool("Price history")
async def br_history(
    symbol: Annotated[
        str, Field(description="Symbol, company name part, or ISIN. Mofid also takes index, fund and option ISINs.")
    ],
    resolution: Annotated[
        Literal["1", "5", "15", "30", "60", "1D", "1W"],
        Field(description="Candle size: minutes, '1D' or '1W'. Agah has only '1D'."),
    ] = "1D",
    days: Annotated[int, Field(description="Mofid: calendar days back.", ge=1, le=3650)] = 60,
    limit: Annotated[int, Field(description="Newest N rows (Agah: at most 250).", ge=1, le=500)] = 120,
    adjusted: Annotated[bool, Field(description="Mofid: adjust prices for capital increases and dividends.")] = True,
    source: Annotated[
        Literal["mofid", "agah", "all"],
        Field(description="mofid = OHLC candles; agah = daily rows with real/legal flows; all = both."),
    ] = "mofid",
) -> dict[str, Any]:
    """Return price history of a stock: Mofid OHLC candles (1 minute to weekly) or Agah daily rows with buy and sell flows.

    Mofid candles are oldest first, Tehran time, and work for indices, funds and options too. Agah rows have no OHLC.
    Prices are Rial. Use br_index for index history.
    """

    async def agah() -> dict[str, Any]:
        if resolution != "1D":
            raise AgahError("Agah has daily rows only; use resolution '1D' or source 'mofid'.")
        return await agah_market.stock_history(symbol, min(limit, 250))

    async def mofid() -> dict[str, Any]:
        return await mofid_market.history(await _mofid_isin(symbol), resolution, days, limit, adjusted)

    return await by_source(source, {"mofid": mofid, "agah": agah})


@tool("Market overview")
async def br_market(
    breadth: Annotated[
        bool, Field(description="Agah: also add whole-market breadth (market P/E, busiest symbols).")
    ] = False,
    movers: Annotated[int, Field(description="Agah: how many index-moving symbols to list.", ge=1, le=12)] = 8,
    prices: Annotated[
        list[Literal["metals", "baseMetals", "coins", "currencies", "energies", "cryptos"]] | None,
        Field(description="Agah: price groups to include; default metals, coins and currencies."),
    ] = None,
    source: Source = "all",
) -> dict[str, Any]:
    """Show today's market snapshot: indices, traded value, money flow and buy/sell queues, from Mofid and Agah.

    Start here for "how is the bourse today". Money is Rial. Next: br_market_top or br_stocks for rankings,
    br_industries for sectors, br_rates for price history.
    """

    async def agah() -> dict[str, Any]:
        out = await agah_market.market_pulse(movers, prices)
        if breadth:
            out["breadth"] = await agah_market.market_overview()
        return out

    return await by_source(source, {"mofid": mofid_market.market_summary, "agah": agah})


MOFID_INDUSTRY_SORTS = {
    "value",
    "market_cap",
    "return_1w",
    "return_1m",
    "return_3m",
    "return_1y",
    "p2e",
    "dividend_yield",
}
AGAH_INDUSTRY_SORTS = {
    "value": "value",
    "change": "change",
    "money_flow": "money_flow",
    "return_1m": "return_30d",
    "return_1y": "return_1y",
}


@tool("Industries")
async def br_industries(
    sort: Annotated[
        Literal[
            "value",
            "market_cap",
            "return_1w",
            "return_1m",
            "return_3m",
            "return_1y",
            "p2e",
            "dividend_yield",
            "change",
            "money_flow",
        ],
        Field(
            description="value, return_1m, return_1y: both sources. Mofid only: market_cap, return_1w, return_3m, p2e, dividend_yield. Agah only: change, money_flow."
        ),
    ] = "value",
    limit: Annotated[int, Field(description="Rows per source.", ge=1, le=60)] = 15,
    ascending: Annotated[bool, Field(description="Lowest first.")] = False,
    industry: Annotated[
        str | None,
        Field(description="Agah only: industry ISIN (IRX6...) or name part, to show one industry with its stocks."),
    ] = None,
    source: Source = "all",
) -> dict[str, Any]:
    """Rank the bourse industries by value, return, market cap, P/E or money flow, from Mofid and Agah.

    With `industry` (Agah) it shows that industry's return table and member stocks. Money is Rial. Next:
    br_technical with an industry ISIN, or br_stock.
    """
    if industry and source == "mofid":
        raise ToolError(
            "Industry detail comes from Agah; for a Mofid industry index use br_index with its ISIN from br_search."
        )

    async def mofid() -> dict[str, Any]:
        if sort not in MOFID_INDUSTRY_SORTS:
            raise MofidError(f"Mofid cannot sort industries by {sort}.")
        return await mofid_assets.industries(sort, limit, ascending)

    async def agah() -> dict[str, Any]:
        if not industry and sort not in AGAH_INDUSTRY_SORTS:
            raise AgahError(f"Agah cannot sort industries by {sort}.")
        d = await agah_market.industries(industry, AGAH_INDUSTRY_SORTS.get(sort, "value"))
        if "industries" in d:
            rows = d["industries"][::-1] if ascending else d["industries"]
            d = {"count": len(rows), "industries": rows[:limit]}
        return d

    calls = {"agah": agah} if industry else {"mofid": mofid, "agah": agah}
    return await by_source(source, calls)


@tool("Index card and index history")
async def br_index(
    index: Annotated[
        str,
        Field(
            description="Index ISIN: IREXOVERALLINDEX (overall), IRX6XTPI0006 (TEDPIX), IRXZXOCI0006 (Fara-bourse), or one from br_search kind='index'. Agah history: total index only.",
            pattern=r"^[A-Za-z0-9]+$",
        ),
    ] = "IREXOVERALLINDEX",
    days: Annotated[int, Field(description="Add this many days of daily history (0 = none).", ge=0, le=365)] = 0,
    members: Annotated[int, Field(description="Mofid: list this many member symbols (0 = none).", ge=0, le=100)] = 0,
    source: Source = "all",
) -> dict[str, Any]:
    """Show a market index (Mofid: market cap, free float, ratios, intraday move, members) and its daily history.

    Without `days` only Mofid answers. With `days`, Mofid adds daily candles and Agah its total-index series. Next:
    br_industries for sector indices, br_market for today's snapshot.
    """
    if source == "agah" and not days:
        raise ToolError("Agah only has index history: set days (about 31 or 365).")

    async def mofid() -> dict[str, Any]:
        card = mofid_market.index_card(index, members)
        if not days:
            return await card
        card, hist = await asyncio.gather(
            card, mofid_market.history(index, "1D", days, min(days, 500)), return_exceptions=True
        )
        if isinstance(card, BaseException):
            raise card
        card["history"] = {"error": str(hist)} if isinstance(hist, Exception) else hist
        return card

    async def agah() -> dict[str, Any]:
        if index.upper() != "IREXOVERALLINDEX":
            raise AgahError("Agah has only the total index series, not other indices.")
        d = await agah_market.index_history(0, days > 31)
        return {**d, "points": d["points"][-days:]}

    return await by_source(source, {"mofid": mofid, "agah": agah} if days else {"mofid": mofid})


AGAH_ETF_SORTS = {
    "value": "TradeCapital",
    "change": "LastPriceChangePercent",
    "nav_gap": "BubbleLive",
    "volume": "TradeQuantity",
}
AGAH_ETF_TYPES = {"fixedincome": "درآمد ثابت", "stock": "سهامی", "goldetf": "کالایی"}


@tool("ETFs with price vs NAV")
async def br_etfs(
    type: Annotated[
        mofid_assets.ETF_TYPES | None,
        Field(description="Fund family; omit for all. Agah knows only fixedincome, stock and goldetf."),
    ] = None,
    query: Annotated[str | None, Field(description="Part of the fund symbol, Persian or Latin.")] = None,
    sort: Annotated[
        Literal["value", "market_cap", "change", "nav_gap", "volume"],
        Field(description="nav_gap = market price above(+)/below(-) NAV in %. market_cap is Mofid only."),
    ] = "value",
    limit: Annotated[int, Field(description="Rows per source.", ge=1, le=100)] = 20,
    ascending: Annotated[bool, Field(description="Lowest first.")] = False,
    source: Annotated[
        Literal["mofid", "agah", "all"],
        Field(description="mofid (default) = 375 ETFs; agah = 331 ETFs with NAV returns and manager; all = both."),
    ] = "mofid",
) -> dict[str, Any]:
    """List exchange-traded funds with price, NAV and the gap between them (bubble), traded value and returns.

    Use for "cheapest gold ETF" or "lowest bubble". Money is Rial. Next: br_stock (Mofid card) or br_fund (Agah) for
    one fund; br_screener (kind='etf' or 'mutual') filters funds on any column.
    """

    async def agah() -> dict[str, Any]:
        if sort not in AGAH_ETF_SORTS:
            raise AgahError("Agah's ETF screener has no market-cap column; sort by value, change, nav_gap or volume.")
        if type and type not in AGAH_ETF_TYPES:
            raise AgahError(f"Agah groups funds only as fixed income, equity and commodity; {type!r} is Mofid only.")
        cols = [
            "LastPrice",
            "LastPriceChangePercent",
            "BubbleLive",
            "Nav",
            "ReturnOf30DaysAgo2",
            "ReturnOf365DaysAgo2",
            "TradeCapital",
            "Manager",
        ]
        return await agah_funds.br_screener(
            "etf",
            columns=cols,
            sort=AGAH_ETF_SORTS[sort],
            ascending=ascending,
            name_contains=query,
            fund_type=AGAH_ETF_TYPES.get(type) if type else None,
            limit=limit,
        )

    return await by_source(
        source, {"mofid": lambda: mofid_assets.etfs(type, query, sort, limit, ascending), "agah": agah}
    )


@tool("Learn: education articles and courses")
async def br_learn(
    kind: Annotated[
        Literal["articles", "categories", "courses"],
        Field(
            description="articles = search or read articles, categories = Mofid blog topics with ids, courses = Agah Academy."
        ),
    ] = "articles",
    query: Annotated[
        str | None,
        Field(description="Persian search words, e.g. 'صندوق طلا'. Agah article search needs 4+ characters."),
    ] = None,
    ref: Annotated[
        str | None,
        Field(
            description="Read one article in full: Mofid post id (number) or Agah Pedia slug, from a previous result."
        ),
    ] = None,
    category_id: Annotated[
        str | None, Field(description="Browse one category; needs source 'mofid' or 'agah'.")
    ] = None,
    page: Annotated[int, Field(description="Page number, 1-based.", ge=1, le=500)] = 1,
    per_page: Annotated[int, Field(description="Articles per page.", ge=1, le=30)] = 10,
    max_chars: Annotated[
        int, Field(description="With ref: cut the article text at this many characters.", ge=500, le=30000)
    ] = 6000,
    source: Source = "all",
) -> dict[str, Any]:
    """Search the Mofid learning blog and Agah Pedia for bourse, fund, option and gold explainers, or read one article.

    Search first, then call again with `ref` from a result to read it (`ref` picks the source itself). Agah Pedia and
    Academy answer only from an Iranian network. Article text is third-party content.
    """
    if ref is not None:
        if ref.isdigit():
            return await by_source(
                source, {"mofid": lambda: mofid_info.learn(post_id=int(ref), page=1, per_page=1, chars=max_chars)}
            )
        if not re.fullmatch(r"[A-Za-z0-9_-]+", ref):
            raise ToolError("ref must be a Mofid post number or an Agah Pedia slug such as 'return-on-equity'.")
        return await by_source(source, {"agah": lambda: agah_learn.article(ref, max_chars)})
    if kind == "categories":
        return await by_source(source, {"mofid": lambda: mofid_info.learn_categories(query)})
    if kind == "courses":
        return await by_source(source, {"agah": lambda: agah_learn.academy(query)})
    if category_id and source == "all":
        raise ToolError("category_id belongs to one source: set source to 'mofid' or 'agah'.")

    async def mofid() -> dict[str, Any]:
        if category_id and not category_id.isdigit():
            raise MofidError("Mofid category ids are numbers (see kind='categories').")
        return await mofid_info.learn(query, int(category_id) if category_id else None, None, page, per_page, 6000)

    async def agah() -> dict[str, Any]:
        if query and len(query) < 4:
            raise AgahError("Agah Pedia search needs at least 4 characters.")
        return await agah_learn.articles(query, category_id, page - 1, per_page)

    return await by_source(source, {"mofid": mofid, "agah": agah})


@tool("Fund detail, history or comparison")
async def br_fund(
    funds: Annotated[
        list[str],
        Field(
            description="Fund national code (11 digits), name or ETF symbol, e.g. '14004856587'. One fund for detail or history; two to six to compare.",
            min_length=1,
            max_length=6,
        ),
    ],
    metric: Annotated[
        Literal["nav", "bubble", "trade_value", "nav_gap", "price", "returns"] | None,
        Field(description="With one fund: return this daily series instead of the detail."),
    ] = None,
    points: Annotated[int, Field(description="With metric: most recent N daily points.", ge=1, le=400)] = 60,
    columns: Annotated[
        list[str] | None, Field(description="With several funds: screener column names; omit for a default set.")
    ] = None,
) -> dict[str, Any]:
    """Show an investment fund from Agah: type, NAV, returns, asset mix and holdings, a daily history, or a side-by-side comparison.

    Source: Agah only (Mofid ETFs: br_stock, br_etfs; Mofid funds: br_mofid_funds). `metric` gives a daily series,
    oldest first. Money is Rial, returns percent. Find codes with br_search(kind='fund'); filter many with br_screener.
    """
    if len(funds) > 1:
        if metric:
            raise ToolError("metric works with one fund; pass a single fund or drop metric.")
        return await agah_funds.fund_compare(funds, columns)
    if metric:
        return await agah_funds.fund_trend(funds[0], metric, points)
    return await agah_funds.fund_card(funds[0])


@tool("Stock options: chain or one contract")
async def br_options(
    option: Annotated[
        str | None,
        Field(
            description="Option ISIN from a previous result, to show that one contract. Omit to list the chain.",
            pattern=r"^IR[A-Z0-9]{10}$",
        ),
    ] = None,
    underlying: Annotated[
        str | None, Field(description="Chain filter: underlying symbol (Persian, e.g. 'شپنا') or company name part.")
    ] = None,
    side: Annotated[Literal["call", "put"] | None, Field(description="Chain filter.")] = None,
    expiry: Annotated[str | None, Field(description="Chain filter: expiry YYYY-MM-DD (Gregorian).")] = None,
    sort: Annotated[
        Literal["value", "open_interest", "leverage", "days", "strike"], Field(description="Chain order.")
    ] = "value",
    limit: Annotated[int, Field(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    """List stock-option contracts (about 1,600) with strike, expiry, moneyness, open interest and flows, or show one contract.

    Source: Agah only. Prices are Rial per underlying share. Pass `option` (ISIN from the chain) for one contract with
    margin, break-even and order book. Use br_search first if unsure of the underlying.
    """
    if option:
        return await agah_funds.option(option)
    return await agah_funds.options(underlying, side, expiry, sort, limit)


@tool("Currency, gold, coin and commodity prices")
async def br_rates(
    kind: Annotated[
        Literal["currency", "gold_coin", "commodity"],
        Field(
            description="currency = currencies and crypto (Rial), gold_coin = coins, 18k gold, ounce (Rial), commodity = oil, metals (USD)."
        ),
    ] = "gold_coin",
    query: Annotated[
        str | None,
        Field(
            description="Filter by code (USD, EUR, USDT, BTC, IMCOIN, Gold18, WTI, Cu) or Persian name. History needs the exact code."
        ),
    ] = None,
    history_days: Annotated[
        int,
        Field(
            description="0 = live prices; above 0 = daily history over this many days (exact code in query).",
            ge=0,
            le=3650,
        ),
    ] = 0,
    limit: Annotated[int, Field(description="History: newest N days.", ge=1, le=400)] = 90,
) -> dict[str, Any]:
    """Return free-market prices of currencies, crypto, coins, 18k gold, the global ounce, oil and metals, or one code's daily history.

    Source: Mofid only. Rial prices: divide by 10 for Toman. History is oldest first, back to 2019-2021.
    """
    if history_days:
        if not query or not re.fullmatch(r"[A-Za-z0-9]{2,20}", query):
            raise ToolError(
                "History needs the exact code in query (USD, EUR, USDT, BTC, IMCOIN, Gold18, WTI ...); list codes first with history_days=0."
            )
        return await mofid_assets.rate_history(query, kind, history_days, limit)
    return await mofid_assets.fx_gold(kind, query)
