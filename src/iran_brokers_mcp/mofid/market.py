"""Bourse View market tools: symbol search, quotes, one-symbol card, depth, candles, market overview and rankings."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Literal

from pydantic import Field

from ..registry import tool
from .http import CACHE_LONG, ApiError, fetch
from .util import norm, pct, pick, rows

# stockTickers type `t` -> our kind. Anything not listed (saffron, certificates, Product...) is only found with kind="all".
KINDS: dict[str, set[str]] = {
    "stock": {"CommonStock", "Right"},
    "etf": {
        "etfstock",
        "etffixedincome",
        "etfgold",
        "etfventurecapital",
        "SilverETF",
        "fund_etf_mixed",
        "LeveragedEtf",
        "realstateetf",
        "PrivateEquityETF",
        "etfFundOfFunds",
        "agricultureetf",
        "etfprojects",
        "EnergyETF",
    },  # fmt: skip
    "option": {"stockCallOption", "stockPutOption"},
    "index": {"IndexIndustry", "IndexOverall"},
    "commodity": {"commodity", "Product"},
    "currency": {"currency"},
}
TEHRAN = timezone(timedelta(hours=3, minutes=30))  # Iran has no daylight saving since 2022
RESOLUTIONS = ("1", "5", "15", "30", "60", "1D", "1W")
BOARDS: dict[str, str] = {  # board -> endpoint that serves it
    **dict.fromkeys(
        ["mostvalue", "mostvolume", "valuetoaveragevalue30", "valuetomarketcap", "volumetoaveragevolume30",
         "volumetofloatingratio", "volumetonumberofshare"], "marketTopValue"),
    **dict.fromkeys(
        ["mostmarketcap", "mosteffectonindex", "netindividual", "netinstitutional", "netinstitutionalvalue",
         "mostbuyindpower", "institutionalbuypower", "mostbidpressure", "mostnetind"], "marketTop"),
    **dict.fromkeys(
        ["return1W_desc", "return3M_desc", "return6M_desc", "return1Y_desc", "DailyChange_desc",
         "highPercent50d_asc", "highPercent200d_asc"], "marketTopReturn"),
    **dict.fromkeys(
        ["p2e_asc", "p2b_asc", "p2s_asc", "p2nav_asc", "p2d_asc", "p2fcff_asc", "p2fcfe_asc", "evtoebit_asc",
         "ev2s_asc", "p2s_monthly_asc", "ev2s_monthly_asc"], "marketTopMultiples"),
    **dict.fromkeys(["bidQueue", "askQueue"], "marketValues"),
}  # fmt: skip
Board = Literal[
    "mostvalue", "mostvolume", "valuetoaveragevalue30", "valuetomarketcap", "volumetoaveragevolume30",
    "volumetofloatingratio", "volumetonumberofshare", "mostmarketcap", "mosteffectonindex", "netindividual",
    "netinstitutional", "netinstitutionalvalue", "mostbuyindpower", "institutionalbuypower", "mostbidpressure",
    "mostnetind", "return1W_desc", "return3M_desc", "return6M_desc", "return1Y_desc", "DailyChange_desc",
    "highPercent50d_asc", "highPercent200d_asc", "p2e_asc", "p2b_asc", "p2s_asc", "p2nav_asc", "p2d_asc",
    "p2fcff_asc", "p2fcfe_asc", "evtoebit_asc", "ev2s_asc", "p2s_monthly_asc", "ev2s_monthly_asc", "bidQueue",
    "askQueue",
]  # fmt: skip
ISIN_HELP = "ISIN-style id from br_search, e.g. IRO1MSMI0001 (فملی), IREXOVERALLINDEX (overall index)."
Isin = Annotated[str, Field(description=ISIN_HELP, min_length=3, max_length=40, pattern=r"^[A-Za-z0-9]+$")]


async def _tickers() -> list[dict[str, Any]]:
    return rows(await fetch("/stockTickers", ttl=CACHE_LONG))


async def search(
    query: Annotated[
        str,
        Field(
            description="Part of a symbol, company name or ISIN, Persian or Latin, e.g. 'فملی', 'اهرم', 'IRO1MSMI'.",
            min_length=1,
        ),
    ],
    kind: Annotated[
        Literal["stock", "etf", "option", "index", "commodity", "currency", "all"],
        Field(description="Instrument group. Default 'stock'; options are 2300 contracts, so ask for them explicitly."),
    ] = "stock",
    limit: Annotated[int, Field(description="Maximum results.", ge=1, le=50)] = 10,
) -> dict[str, Any]:
    """Find the ISIN of a Tehran-exchange symbol (stock, fund/ETF, option, index, commodity) by name or symbol.

    Use this first: every other tool takes the ISIN. Exact symbol matches come first.
    """
    q = norm(query)
    allowed = KINDS.get(kind)
    hits: list[tuple[int, dict[str, Any]]] = []
    for t in await _tickers():
        if allowed is not None and t.get("t") not in allowed:
            continue
        s, n, i = norm(t.get("s")), norm(t.get("n")), norm(t.get("i"))
        if q == s or q == i:
            rank = 0
        elif s.startswith(q) or i.startswith(q):
            rank = 1
        elif q in s or q in n or q in i:
            rank = 2
        else:
            continue
        hits.append((rank, t))
    hits.sort(key=lambda h: (h[0], len(h[1].get("s") or "")))
    return {
        "count": len(hits),
        "results": [
            {
                "isin": t["i"],
                "symbol": t.get("s"),
                "name": t.get("n"),
                "type": t.get("tfa"),
                "exchange": t.get("ExchangeNameFA"),
            }
            for _, t in hits[:limit]
        ],
    }


def _quote(r: dict[str, Any]) -> dict[str, Any]:
    out = pick(
        r,
        {
            "isin": "isin", "tickerFA": "symbol", "tickerNameFA": "name", "type2FA": "type", "industryFA": "industry",
            "close": "last", "closing": "close", "previousClosing": "prev_close", "open": "open", "high": "high",
            "low": "low", "volume": "volume", "value": "value", "minAuthPrice": "min_price", "maxAuthPrice": "max_price",
            "orderEntryFA": "order_state", "bidPrice": "bid", "bidQuantity": "bid_qty", "askPrice": "ask",
            "askQuantity": "ask_qty", "closeChg": "last_change",
        },
    )  # fmt: skip
    out["last_change_pct"] = pct(r.get("closeChgPer"))
    out["close_change_pct"] = pct(r.get("closingChgPer"))
    out["returns_pct"] = {k: pct(r.get(f"return{k.upper()}")) for k in ("1w", "1m", "3m", "6m", "1y", "3y", "5y")}
    return out


@tool("Get quotes")
async def br_quotes(
    isins: Annotated[list[str], Field(description=ISIN_HELP, min_length=1, max_length=30)],
) -> dict[str, Any]:
    """Return live price rows for 1 to 30 symbols at once: last and closing price, change, volume, limits, best bid and ask, returns.

    Source: Mofid only. Use it after br_search to price several symbols at once, or for fund and index ids; br_stock gives one symbol in depth.

    Prices are Rial. `last` is the last trade, `close` the closing (weighted) price the exchange uses for change. Index and fund ids work too.
    """
    data = rows(await fetch("/symbolLatest", {"isin": ",".join(i.strip() for i in isins)}))
    return {"count": len(data), "quotes": [_quote(r) for r in data]}


async def symbol_card(isin: Isin) -> dict[str, Any]:
    """Return one stock or fund in detail: price card with P/E, EPS and NAV, company profile, ownership split, returns and industry.

    Combines five Bourse View calls. Sections the exchange has no data for (for example profile of a fund) are left out.
    """
    today = datetime.now(TEHRAN).strftime("%Y%m%d")
    calls = {
        "quote": fetch("/stocksQuotes", {"isin": isin}),
        "info": fetch("/stockInfo", {"isin": isin}),
        "ind_inst": fetch("/stockIndInst", {"isin": isin}),
        "overall": fetch(f"/overall/stock/{isin}", {"start_date": today, "end_date": today, "timeFrame": "daily"}),
        "score": fetch("/stockEvaluation", {"tickers": isin}),
    }
    got = dict(zip(calls, await asyncio.gather(*calls.values(), return_exceptions=True), strict=True))
    if all(isinstance(v, ApiError) for v in got.values()):
        raise next(iter(got.values()))  # type: ignore[misc]
    ok = {k: v for k, v in got.items() if not isinstance(v, BaseException)}
    out: dict[str, Any] = {"isin": isin}
    if q := (rows(ok.get("quote")) or [None])[0]:
        out["quote"] = pick(
            q,
            {
                "symbol": "symbol", "companyName": "name", "close": "close", "last": "last", "open": "open", "high": "high",
                "low": "low", "prevLast": "prev_last", "lastChgPer": "last_change_pct", "closeChgPer": "close_change_pct",
                "value": "value", "volume": "volume", "minAuthPrice": "min_price", "maxAuthPrice": "max_price",
                "lastTradeTime": "last_trade_time", "p2e": "p2e", "eps": "eps", "dps": "dps", "nav": "nav", "p2nav": "p2nav",
                "p2s": "p2s", "theoreticalPrice": "theoretical_price", "marketCap": "market_cap",
                "industryFA": "industry", "market": "market",
            },
        )  # fmt: skip
    if i := ok.get("info"):
        out["shares"] = pick(
            i,
            {
                "numberOfOutstandingShares": "outstanding",
                "freeFloat": "free_float",
                "firstTradingDayKey": "first_trade_day",
            },
        )
        out["indices"] = {k: v.get("namePouya") for k, v in (i.get("indices") or {}).items() if isinstance(v, dict)}
    if x := ok.get("ind_inst"):
        out["individual_vs_institutional"] = pick(
            x,
            {
                "indBuyVol": "individual_buy_volume", "indSellVol": "individual_sell_volume", "insBuyVol": "institutional_buy_volume",
                "insSellVol": "institutional_sell_volume", "indBuyNum": "individual_buyers", "indSellNum": "individual_sellers",
                "insBuyNum": "institutional_buyers", "insSellNum": "institutional_sellers", "indBuyPow": "individual_buy_power",
                "buyValueInd": "individual_buy_value", "sellValueInd": "individual_sell_value",
                "buyValueIns": "institutional_buy_value", "sellValueIns": "institutional_sell_value",
            },
        )  # fmt: skip
    if o := ok.get("overall"):
        out["profile"] = pick(
            o.get("profile") or {},
            {
                "name": "name", "exchange": "exchange", "sector": "sector", "industry": "industry", "ipoDateShamsi": "listed",
                "fiscalYearShamsi": "fiscal_year_end", "activitySubjectFA": "activity", "siteUrl": "website",
                "marketBoard": "board", "auditor": "auditor", "floatingPercent": "float_pct",
            },
        )  # fmt: skip
        out["financials"] = {f["Id"]: f["value"] for f in o.get("financialSummary") or [] if f.get("value") is not None}
        out["performance"] = {
            f["Id"]: f["value"] for f in o.get("performanceSummary") or [] if f.get("value") is not None
        }
    if (s := (rows(ok.get("score")) or [None])[0]) is not None:
        out["scores"] = pick(
            s, {"techScore": "technical", "fundScore": "fundamental_of_10", "theoretical": "theoretical_price"}
        )
    return out


async def depth(isin: Isin) -> dict[str, Any]:
    """Return the five best bid and ask levels (price, quantity, number of orders) of a symbol plus total queue volumes.

    Prices are Rial. Levels are rank 1 (best) to 5.
    """
    d = await fetch("/stockBidAsk", {"symbol": isin})
    levels = d.get("bidAskList") or {}
    out = pick(
        d,
        {
            "sumVolumeBid": "total_bid_volume",
            "sumVolumeAsk": "total_ask_volume",
            "sumValueBid": "total_bid_value",
            "sumValueAsk": "total_ask_value",
        },
    )
    out["levels"] = [
        pick(v, {"rank": "rank", "bidPrice": "bid_price", "bidQty": "bid_qty", "bidNumberOfOrder": "bid_orders",
                 "askPrice": "ask_price", "askQty": "ask_qty", "askNumberOfOrder": "ask_orders"})
        for _, v in sorted(levels.items(), key=lambda kv: int(kv[0]))
    ]  # fmt: skip
    return {"isin": isin, **out}


async def history(
    isin: Isin,
    resolution: Annotated[
        Literal["1", "5", "15", "30", "60", "1D", "1W"],
        Field(description="Candle size: minutes (1, 5, 15, 30, 60), '1D' daily or '1W' weekly."),
    ] = "1D",
    days: Annotated[
        int,
        Field(
            description="How many calendar days back. Minute candles older than a few weeks may be missing.",
            ge=1,
            le=3650,
        ),
    ] = 60,
    limit: Annotated[int, Field(description="Return only the newest N candles.", ge=1, le=500)] = 120,
    adjusted: Annotated[bool, Field(description="Prices adjusted for capital increases and dividends.")] = True,
) -> dict[str, Any]:
    """Return OHLC candles with volume and traded value for a stock, fund, index or option, oldest first.

    Works for any ISIN from br_search, including indices such as IREXOVERALLINDEX. `time` is Tehran local time.
    """
    now = int(time.time())
    d = await fetch(
        "/quotes/history",
        {
            "symbol": isin,
            "resolution": resolution,
            "from_time": now - days * 86400,
            "to_time": now,
            "adjusted": str(adjusted).lower(),
        },
    )
    if d.get("s") != "ok":
        return {"isin": isin, "resolution": resolution, "count": 0, "candles": [], "note": d.get("s") or "no data"}
    fmt = "%Y-%m-%d" if resolution in ("1D", "1W") else "%Y-%m-%d %H:%M"
    cols = [d.get(k) or [] for k in ("t", "o", "h", "l", "c", "v", "val")]
    candles = [
        {
            "time": datetime.fromtimestamp(t, TEHRAN).strftime(fmt),
            "open": o,
            "high": h,
            "low": lo,
            "close": c,
            "volume": v,
            "value": val,
        }
        for t, o, h, lo, c, v, val in zip(*cols, strict=False)
    ]
    return {"isin": isin, "resolution": resolution, "count": len(candles), "candles": candles[-limit:]}


async def market_summary() -> dict[str, Any]:
    """Return today's market snapshot: overall indices (TEDPIX, Fara-bourse, equal-weight, gold and fixed-income ETF), value, volume, market cap, individual net buying, and buy/sell queue counts.

    Values are Rial; dollar variants are included. Start here for 'how is the bourse today'.
    """
    summary, queues, idx = await asyncio.gather(
        fetch("/marketSummary"),
        fetch("/marketQueueCount", {"isin": "Stock"}),
        fetch("/indexQuotes"),
        return_exceptions=True,
    )
    if isinstance(summary, BaseException):
        raise summary
    out: dict[str, Any] = {
        "markets": [
            {
                **pick(
                    r,
                    {
                        "isin": "isin",
                        "tickerFa": "name",
                        "close": "close",
                        "previousClose": "prev_close",
                        "volume": "volume",
                        "tradeValue": "value",
                        "marketCap": "market_cap",
                        "marketCapDollar": "market_cap_usd",
                        "tradeValueDollar": "value_usd",
                        "netIndividualValue": "net_individual_value",
                        "individualBuyPower": "individual_buy_power",
                        "marketDayKey": "day",
                    },
                ),  # fmt: skip
                "change_pct": pct(float(r["dailyReturn"])) if r.get("dailyReturn") not in (None, "") else None,
            }
            for r in rows(summary)
        ]
    }
    if not isinstance(queues, BaseException):
        out["queues"] = {
            r["type"]: {"count": r["count"], "value": r["val"], "pct_of_symbols": r["perc"]}
            for r in queues.get("live") or []
        }
    return out


@tool("Market rankings")
async def br_market_top(
    board: Annotated[
        Board,
        Field(
            description="Which ranking. Value/volume: mostvalue, mostvolume, valuetomarketcap, volumetoaveragevolume30. "
            "Money flow: netindividual, netinstitutional, mostbuyindpower, institutionalbuypower, mostbidpressure. "
            "Size: mostmarketcap, mosteffectonindex. Returns: return1W_desc ... return1Y_desc, DailyChange_desc. "
            "Cheapest by ratio (ascending): p2e_asc, p2b_asc, p2s_asc, p2nav_asc, p2d_asc, evtoebit_asc. "
            "Queues: bidQueue, askQueue (symbols locked at the limit)."
        ),
    ],
    limit: Annotated[int, Field(description="Rows to return.", ge=1, le=20)] = 10,
) -> dict[str, Any]:
    """Return one of the market's live top-N lists (most traded, biggest buy queue, best weekly return, lowest P/E, net individual buying, ...).

    Source: Mofid only. Agah's own ranking of all stocks (money flow, buyer power, queue, returns) is br_stocks; open a row with br_stock.

    Rows carry the symbol, ISIN, price, daily change and the ranking value. Prices are Rial.
    """
    endpoint = BOARDS[board]
    d = await fetch(
        f"/{endpoint}", {"top_count": 20} if endpoint in ("marketTop", "marketTopValue", "marketValues") else None
    )
    out = []
    for r in (d.get(board) or [])[:limit]:
        out.append(
            {
                "isin": r.get("isin"),
                "symbol": r.get("name") or r.get("ticker_fa") or r.get("symbol"),
                "price": r.get("price") or r.get("closing") or r.get("close"),
                "change_pct": next(
                    (r[k] for k in ("changePercent", "last_change_percent", "lastChangePercent") if k in r), None
                ),
                "value": r.get("value", r.get("valueNumber")),
                "traded_value": next((r[k] for k in ("tradeValue", "trade_value", "TradeValue") if k in r), None),
                "volume": r.get("volume"),
                "bid_queue_value": r.get("bidValue"),
                "ask_queue_value": r.get("askValue"),
                "theoretical_price": r.get("theoreticalPrice"),
            }
        )
    return {"board": board, "count": len(out), "rows": out}


async def index_card(
    isin: Annotated[
        str,
        Field(
            description="Index ISIN: IREXOVERALLINDEX (Iran Exchange overall), IRX6XTPI0006 (TEDPIX), IRXZXOCI0006 (Fara-bourse), or an industry index from br_search kind='index'.",
            pattern=r"^[A-Za-z0-9]+$",
        ),
    ] = "IREXOVERALLINDEX",
    members: Annotated[int, Field(description="List this many member symbols (0 = none).", ge=0, le=100)] = 0,
) -> dict[str, Any]:
    """Return an index: its name, market cap, free float, valuation ratios (P/E, P/B ...), today's price move and optionally member symbols.

    Candles of an index come from br_history with the same ISIN.
    """
    detail, fin, mean = await asyncio.gather(
        fetch(f"/overall/indice/detail/{isin}"),
        fetch(f"/overall/indice/financialSummary/{isin}"),
        fetch("/marketPriceMeanLive", {"isin": isin}),
        return_exceptions=True,
    )
    if isinstance(detail, BaseException):
        raise detail
    out = pick(
        detail,
        {
            "isin": "isin",
            "nameLocalFa": "name",
            "nameLocal": "name_en",
            "marketCap": "market_cap",
            "freeFloat": "free_float",
            "type": "type",
        },
    )
    out["member_count"] = len(detail.get("members") or [])
    if not isinstance(fin, BaseException):
        out["ratios"] = {i["Id"]: i["value"] for i in fin.get("items") or [] if i.get("value") is not None}
    if not isinstance(mean, BaseException) and rows(mean):
        last = rows(mean)[-1]
        out["intraday_mean_change_pct"] = {
            "vs_last_trade_price": last.get("closeToClosingPrev"),
            "vs_closing_price": last.get("closingToClosingPrev"),
        }
    if members:
        out["members"] = [
            {"isin": m["isin"], "symbol": m.get("symbolPouyaFa"), "market_cap": m.get("marketCap")}
            for m in (detail.get("members") or [])[:members]
        ]
    return out
