"""Stock-market tools: symbol search, market pulse, market watch, one stock, history, events, technical state, industries, indexes."""

from __future__ import annotations

import asyncio
from typing import Annotated, Any, Literal

from pydantic import Field

from ..registry import tool
from . import http
from .util import ACTIONS, data, day, fund_list, norm, research, stock_isin, stock_list, tail

# sort key -> field of market/instrumentInfo
SORTS = {
    "change": "lastPriceChangePercent",
    "close_change": "closingPriceTodayChangePercent",
    "value": "tradeCapital",
    "volume": "tradeQuantity",
    "net_money_flow": "maximumCashFlow",
    "buyer_power": "customerPowerByFormula",
    "queue_value": "queueValue",
    "market_cap": "marketCapital",
    "return_7d": "returnOf7DaysAgo",
    "return_30d": "returnOf30DaysAgo",
    "return_90d": "returnOf90DaysAgo",
    "return_1y": "returnOf365DaysAgo",
    "volume_vs_avg": "suspiciousValue",
}


def _row(s: dict) -> dict:
    """One market-watch row. Money is Rial; maximumCashFlow is the net money entering (+) or leaving (-) via individual investors."""
    return {
        "symbol": s["symbol"],
        "isin": s["instrumentIsin"],
        "market": s.get("marketName"),
        "last": s.get("lastPrice"),
        "last_change_pct": s.get("lastPriceChangePercent"),
        "close": s.get("closingPriceToday"),
        "close_change_pct": s.get("closingPriceTodayChangePercent"),
        "value": s.get("tradeCapital"),
        "volume": s.get("tradeQuantity"),
        "net_money_flow": s.get("maximumCashFlow"),
        "buyer_power": s.get("customerPowerByFormula"),
        "queue_value": s.get("queueValue"),
        "market_cap": s.get("marketCapital"),
        "return_7d": s.get("returnOf7DaysAgo"),
        "return_30d": s.get("returnOf30DaysAgo"),
        "return_1y": s.get("returnOf365DaysAgo"),
        "status": (s.get("status") or {}).get("title"),
    }


async def option_list() -> list[dict]:
    """Option contracts with their underlying symbol: option/all names are contract codes like 'ضشنا8056'."""
    names, rows = await asyncio.gather(
        research("optionScreener/baseInfo", ttl=http.CACHE_LONG), research("optionScreener/data", ttl=300)
    )
    recs = (dict(zip(names or [], r, strict=False)) for r in rows or [])
    return [
        {
            "isin": r.get("OptionIsin"),
            "name": r.get("Name"),
            "nickName": r.get("Symbol"),
            "companyName": r.get("CompanyName"),
        }
        for r in recs
    ]


async def search(
    query: Annotated[
        str, Field(description="Part of a symbol, company or fund name, Persian, e.g. 'فولاد' or 'طلا'.", min_length=1)
    ],
    kind: Annotated[
        Literal["any", "stock", "fund", "option", "industry"],
        Field(description="Restrict to one kind; 'any' searches all."),
    ] = "any",
    limit: Annotated[int, Field(ge=1, le=50)] = 15,
) -> dict[str, Any]:
    """Find the ISIN or fund code for a symbol, company, fund, option contract or industry on Agah Research.

    Use it first when you only have a name. Stock and option ids are ISINs (use them in br_stock, br_options(option=...)); fund ids
    are national codes such as '14010557380_A01' (use them in br_fund); industry ids are ISINs for br_industries.
    """
    q = norm(query)
    kinds = ["stock", "fund", "option", "industry"] if kind == "any" else [kind]
    lists = await asyncio.gather(
        *(
            {"stock": stock_list, "fund": fund_list, "option": option_list}.get(
                k, lambda k=k: research(f"{k}/all", ttl=http.CACHE_LONG)
            )()
            for k in kinds
        )
    )
    out: list[dict] = []
    for k, rows in zip(kinds, lists, strict=True):
        for r in rows or []:
            name = r.get("symbol") or r.get("name") or ""
            hay = norm(name) + norm(r.get("companyName", "")) + norm(r.get("nickName", "")) + norm(r.get("name", ""))
            if q in hay:
                code = r.get("isin") or r.get("nationalCode")
                item = {"kind": k, "id": code, "name": name}
                if r.get("companyName"):
                    item["company"] = r["companyName"]
                if k == "fund":
                    item |= {"fund_name": r["name"], "type": r.get("fundType"), "etf": r.get("isEtf")}
                out.append(item)
    exact = [r for r in out if norm(r["name"]) == q]
    out = exact + [r for r in out if r not in exact]
    return {"count": len(out), "results": out[:limit]}


async def market_pulse(
    top: Annotated[int, Field(description="How many index-moving symbols to list.", ge=1, le=12)] = 8,
    prices: Annotated[
        list[Literal["metals", "baseMetals", "coins", "currencies", "energies", "cryptos"]] | None,
        Field(description="Global/local price groups to include; default metals, coins and currencies."),
    ] = None,
) -> dict[str, Any]:
    """Show today's Tehran Stock Exchange index, net money flow, the symbols moving the index most, and global and local prices.

    Use it for "how is the market today" and for dollar, gold coin, ounce, oil or crypto prices as Agah shows them.
    Money is Rial. The index value is the total index. Individual investors' buy and sell capital is for the day.
    """
    base, glob = await asyncio.gather(research("landing/baseInfo"), research("landing/globalPrices"))
    groups = prices or ["metals", "coins", "currencies"]
    return {
        "total_index": base.get("bourseIndexToday"),
        "net_money_flow": base.get("cashFlow"),
        "individual_buy_capital": base.get("individualBuyCapital"),
        "individual_sell_capital": base.get("individualSellCapital"),
        "top_index_movers": [
            {
                k: a.get(s)
                for k, s in (
                    ("symbol", "symbol"),
                    ("last", "lastPrice"),
                    ("change_pct", "lastPriceChangePercent"),
                    ("index_effect", "affect"),
                )
            }
            for a in base.get("topIndexAffects", [])[:top]
        ],
        "prices": {
            g: [
                {
                    "code": p["code"].split(":")[-1],
                    "name": p["nameFa"],
                    "price": p["closingPrice"],
                    "change_pct": p.get("priceChangePercent"),
                    "at": p.get("dateTime"),
                }
                for p in glob.get(g, [])
            ]
            for g in groups
        },
    }


@tool("Stock market watch with sorting")
async def br_stocks(
    sort: Annotated[Literal[tuple(SORTS)], Field(description="Field to rank by.")] = "value",  # type: ignore[valid-type]
    ascending: Annotated[bool, Field(description="Lowest first (default highest first).")] = False,
    limit: Annotated[int, Field(ge=1, le=100)] = 20,
    market: Annotated[
        Literal["بورس", "فرابورس"] | None,
        Field(description="Only Tehran Stock Exchange (بورس) or Fara Bourse (فرابورس)."),
    ] = None,
    min_value: Annotated[int, Field(description="Minimum traded value today, Rial.", ge=0)] = 0,
    scale: Annotated[
        Literal["سهم بزرگ", "سهم متوسط", "سهم کوچک"] | None, Field(description="Large, medium or small cap.")
    ] = None,
) -> dict[str, Any]:
    """Rank all listed stocks (about 750) by today's change, traded value, money flow, buyer power, queue or past returns.

    Source: Agah only. Mofid's equivalents are the br_market_top rankings. Then call br_stock for one symbol.

    Use it for "biggest gainers today", "most traded", "where is money entering" questions. Money is Rial, prices Rial.
    net_money_flow is the net money moving in (positive) or out (negative). buyer_power is the individual buyer vs seller
    average-size ratio. Then call br_stock for one symbol.
    """
    rows = data(await http.fetch(f"{http.RESEARCH}/market/instrumentInfo")) or []
    key = SORTS[sort]
    rows = [
        s
        for s in rows
        if (not market or s.get("marketName") == market)
        and (not scale or s.get("scaleTitle") == scale)
        and (s.get("tradeCapital") or 0) >= min_value
        and s.get(key) is not None
    ]
    rows.sort(key=lambda s: s[key], reverse=not ascending)
    return {"matching": len(rows), "stocks": [_row(s) for s in rows[:limit]]}


async def stock(
    stock: Annotated[str, Field(description="Symbol (فولاد), part of the company name, or ISIN (IRO1FOLD0001).")],
) -> dict[str, Any]:
    """Show one stock: price, trading status, ratios (P/E, EPS, ROE), returns, volume averages, real vs legal flows and order book.

    Money is Rial. Returns are percent over 1/7/30/90/180/365 days, 3 and 5 years, for the stock, its industry, the
    equal-weight index and the total index. Use br_history for daily candles and br_stock_events for dividends.
    """
    isin = await stock_isin(stock)
    base, over, ret = await asyncio.gather(
        research("stockDetails/baseInfo", {"isin": isin}),
        research("stockDetails/overview", {"isin": isin}),
        research("stockDetails/returns", {"isin": isin}),
    )
    top = base.get("topInfo", {})
    day_ = over.get("todayPrice", {})
    t = over.get("todayTechnicalState") or {}
    return {
        "isin": isin,
        "symbol": top.get("symbol"),
        "company": top.get("companyName"),
        "market": top.get("marketName"),
        "status": (top.get("status") or {}).get("title"),
        "last": top.get("lastPrice"),
        "last_change_pct": top.get("lastPriceChangePercent"),
        "close": top.get("closingPriceToday"),
        "close_change_pct": top.get("closingPriceTodayChangePercent"),
        "yesterday_close": day_.get("closingPriceYesterday"),
        "low": day_.get("lowestPrice"),
        "high": day_.get("highestPrice"),
        "price_limits": [day_.get("lowPriceThreshold"), day_.get("highPriceThreshold")],
        "volume": top.get("tradeQuantity"),
        "value": top.get("tradeCapital"),
        "trades": top.get("tradeCount"),
        "market_cap": top.get("marketCapital"),
        "index_effect": top.get("affect"),
        "info": over.get("baseInfo"),
        "ratios": over.get("financialRatios"),
        "margins": over.get("profitMargins"),
        "volume_avg": over.get("volumeAvg"),
        "value_avg": over.get("capitalAvg"),
        "flows": {
            k: day_.get(k)
            for k in (
                "individualBuyQuantity",
                "individualSellQuantity",
                "individualBuyCapital",
                "individualSellCapital",
                "individualBuyCount",
                "individualSellCount",
                "corporationBuyQuantity",
                "corporationSellQuantity",
                "corporationBuyCapital",
                "corporationSellCapital",
                "corporationBuyCount",
                "corporationSellCount",
            )
        },
        "other_trading": over.get("otherTradingInfo"),
        "order_book": (over.get("bestLimits") or {}).get("bestLimits"),
        "technical_today": {**t, "label": ACTIONS.get(t.get("action"))} if t else None,
        "returns_pct": ret,
        "codal": top.get("codalUrl"),
        "tsetmc": base.get("tsetmcURL"),
    }


async def stock_history(
    stock: Annotated[str, Field(description="Symbol, company name part, or ISIN.")],
    days: Annotated[int, Field(description="Most recent N trading days.", ge=1, le=250)] = 30,
) -> dict[str, Any]:
    """Return daily last price, close, volume, value and real/legal buy-sell flows for the most recent trading days of a stock.

    Rows are newest last. Prices and money are Rial. last is the last trade price, close the closing (weighted average)
    price. Intraday candles are dropped to keep the answer small.
    """
    isin = await stock_isin(stock)
    rows = await research("stockDetails/tradeHistorical", {"isin": isin}) or []
    rows = sorted(rows, key=lambda r: r["dateOnly"])
    keep = (
        "dateOnly",
        "lastPrice",
        "lastPriceChangePercent",
        "closePrice",
        "closePriceChangePercent",
        "quantity",
        "tradedCapital",
        "tradedCount",
        "individualBuyQuantity",
        "individualSellQuantity",
        "individualBuyCount",
        "individualSellCount",
        "corporationBuyQuantity",
        "corporationSellQuantity",
    )
    return {"isin": isin, "days": [{k: r.get(k) for k in keep} for r in tail(rows, days)]}


@tool("Dividends, capital changes, notices and major holders of a stock")
async def br_stock_events(
    stock: Annotated[str, Field(description="Symbol, company name part, or ISIN.")],
    holders: Annotated[bool, Field(description="Also list the major shareholders.")] = True,
) -> dict[str, Any]:
    """List a stock's cash dividend history, capital increases, recent exchange notices and major shareholders.

    Source: Agah only. Use it after br_search or br_stock.

    Dividends are per share in Rial with the fiscal year. Shareholders are the latest disclosed, with percent owned.
    """
    isin = await stock_isin(stock)
    ev, add, hold = await asyncio.gather(
        research("stockDetails/events", {"isin": isin}),
        research("stockDetails/additionalData", {"isin": isin}),
        research("stockDetails/holders", {"isin": isin}) if holders else asyncio.sleep(0),
    )
    ev = ev or {}
    out: dict[str, Any] = {
        "isin": isin,
        "cash_dividends": (ev.get("cashProfits") or [])[-10:],  # oldest first; latest ten
        "capital_changes": ev.get("capitalChanges") or (add.get("events") or {}).get("capitalChanges"),
        "notices": [
            {"title": m.get("title") or m.get("description"), "date": m.get("dateTime") or m.get("publishDateTime")}
            for m in (add.get("events") or {}).get("stockMessages", [])[:5]
        ],
        "company": (add.get("company") or {}).get("introCompany"),
    }
    if holders and hold:
        out["holders"] = [
            {"name": h["name"], "shares": h["count"], "percent": h["percent"]}
            for h in (hold.get("stockHolders") or {}).get("stockHolderList", [])[:15]
        ]
    return out


@tool("Technical analysis summary of a stock or industry")
async def br_technical(
    target: Annotated[
        str, Field(description="Stock symbol/name/ISIN, or an industry ISIN (IRX6...) from br_industries.")
    ],
    timeframe: Annotated[
        Literal["oneDay", "oneWeek", "oneMonth"], Field(description="Daily, weekly or monthly signals.")
    ] = "oneDay",
) -> dict[str, Any]:
    """Show Agah's technical-indicator summary (moving averages and oscillators vote, RSI, MACD and others) for a stock or industry.

    Source: Agah only (Mofid has only the coarse Buy/Neutral/Sell rating in br_scores). Use it after br_stock.

    action is 2 strong buy, 1 buy, 0 neutral, -1 sell, -2 strong sell (inferred from the buy/sell counts). It is an
    automatic indicator tally, not advice.
    """
    industry = target.upper().startswith("IRX")
    isin = target.upper() if industry else await stock_isin(target)
    d = await research(f"{'industryTrends' if industry else 'stockTrends'}/indicators", {"isin": isin})
    tf = (d.get("indicators") or {}).get(timeframe) or {}

    def vote(v: dict | None) -> dict | None:
        return {**v, "label": ACTIONS.get(v.get("action"))} if v else None

    return {
        "isin": isin,
        "timeframe": timeframe,
        "summary": vote(tf.get("summary")),
        "moving_averages_vote": vote(tf.get("movingAverageState")),
        "oscillators_vote": vote(tf.get("oscillatorState")),
        "moving_averages": [
            {"name": i["title"], "value": i["value"], "action": i["action"]} for i in tf.get("movingAverages", [])
        ],
        "oscillators": [
            {"name": i["title"], "value": i["value"], "action": i["action"]} for i in tf.get("oscillators", [])
        ],
    }


async def industries(
    industry: Annotated[
        str | None, Field(description="Industry ISIN (IRX6...) or name part for one industry; omit to list all.")
    ] = None,
    sort: Annotated[
        Literal["change", "value", "money_flow", "return_30d", "return_1y"], Field(description="Ranking for the list.")
    ] = "value",
) -> dict[str, Any]:
    """List all industries (groups) with today's change, traded value, money flow and returns, or show one industry with its stocks.

    Money is Rial. With an industry, you also get its return table and constituent stocks with today's change and money flow.
    """
    rows = data(await http.fetch(f"{http.RESEARCH}/market/industryInfo")) or []
    keys = {
        "change": "changePercentageRelativeToPreviousDay",
        "value": "tradeCapital",
        "money_flow": "cashFlow",
        "return_30d": "returnOf30DaysAgo",
        "return_1y": "returnOf365DaysAgo",
    }
    if not industry:
        rows.sort(key=lambda r: r.get(keys[sort]) or 0, reverse=True)
        return {
            "industries": [
                {
                    "name": r["name"],
                    "isin": r["isin"],
                    "change_pct": r.get("changePercentageRelativeToPreviousDay"),
                    "value": r.get("tradeCapital"),
                    "money_flow": r.get("cashFlow"),
                    "market_cap": r.get("marketCapital"),
                    "stocks": r.get("stockCount"),
                    "up": r.get("stockPositiveCount"),
                    "down": r.get("stockNegativeCount"),
                    "return_30d": r.get("returnOf30DaysAgo"),
                    "return_1y": r.get("returnOf365DaysAgo"),
                }
                for r in rows
            ]
        }
    q = norm(industry)
    hit = [r for r in rows if norm(r["isin"]) == q or q in norm(r["name"])]
    if len(hit) != 1:
        raise http.ApiError(
            f"{'No industry' if not hit else 'Several industries'} match '{industry}': "
            + ", ".join(f"{r['name']} ({r['isin']})" for r in hit[:8])
        )
    isin = hit[0]["isin"]
    base, ret, inst = await asyncio.gather(
        research("industryDetails/baseInfo", {"isin": isin}),
        research("industryDetails/returns", {"isin": isin}),
        research("industryDetails/instruments", {"isin": isin}),
    )
    members = (inst or {}).get("coGroupIsins", [])
    return {
        "industry": {
            k: base.get(k)
            for k in (
                "name",
                "isin",
                "last",
                "changePercentageRelativeToPreviousDay",
                "changeAmount",
                "bourseAffect",
                "totalTradeValue",
                "marketCapital",
            )
        },
        "returns_pct": ret,
        "stocks": [
            {
                "symbol": m.get("symbol"),
                "isin": m.get("instrumentIsin"),
                "last_change_pct": m.get("lastPriceChangePercent"),
                "queue_value": m.get("queueValue"),
            }
            for m in members
        ],
    }


async def index_history(
    market: Annotated[
        Literal[0, 1, 2],
        Field(
            description="0, 1 or 2: the three index series on the site (UNCONFIRMED names; 0 has values near 7.8 million)."
        ),
    ] = 0,
    long: Annotated[bool, Field(description="One year (true) or about one month (false) of daily values.")] = False,
) -> dict[str, Any]:
    """Return daily values of one of the three market index series shown on Agah Research (about a month, or a year).

    Series 0 is the total index (values in the millions). Which index series 1 and 2 are is not stated by the site.
    """
    rows = await research("shakhes", {"market": market, "range": int(long)}) or []
    return {"market": market, "points": [{"date": day(d), "value": v} for d, v in rows]}


async def market_overview() -> dict[str, Any]:
    """Show today's whole-market numbers: traded value, money flow per market, market P/E, index returns and the busiest symbols.

    Money is Rial. Use br_market for the quick index and prices, and this for deeper market breadth and top lists.
    """
    d = await research("market/instrumentOverview")
    adv = await research("market/instrumentOverviewAdditional")
    return {
        "total_trade_value": d.get("totalTradeValue"),
        "market_p_to_e": d.get("marketPToE"),
        "top_by_value": d.get("topTradesCapital"),
        "top_by_volume": d.get("topTradesQuantity"),
        "top_suspicious_volume": d.get("topTradesSuspicious"),
        "buyer_power_top": (adv or {}).get("customerPowers"),
    }
