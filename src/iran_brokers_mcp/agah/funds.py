"""Fund and options tools: screener (funds and stocks), one fund, fund time series, fund comparison, options chain."""

from __future__ import annotations

import asyncio
from typing import Annotated, Any, Literal

from pydantic import Field

from ..registry import tool
from . import http
from .util import day, fund, norm, research, tail

FUND_TYPES = {
    "4": "درآمد ثابت",
    "6": "سهامی",
    "5": "کالایی",
}  # FundTypeCode; anything else is "سایر" (mixed, gold, other)
# kind -> (base info path, data path, identity columns, default columns)
KINDS = {
    "stock": (
        "stockScreener/baseInfo",
        "stockScreener/data",
        ["Symbol", "InstrumentIsin"],
        [
            "LastPrice",
            "LastPriceChangePercent",
            "PToE",
            "Eps",
            "Roe",
            "MarketCapital",
            "ReturnOf30DaysAgo",
            "ReturnOf365DaysAgo",
            "IndicatorAction",
        ],
    ),
    "etf": (
        "fundScreener/etfBaseInfo",
        "fundScreener/etfData",
        ["Name", "NationalCode", "FundTypeCode"],
        [
            "LastPrice",
            "BubbleLive",
            "Nav",
            "ReturnOf30DaysAgo2",
            "ReturnOf90DaysAgo2",
            "ReturnOf365DaysAgo2",
            "TradeCapital",
            "Manager",
        ],
    ),
    "mutual": (
        "fundScreener/mutualBaseInfo",
        "fundScreener/mutualData",
        ["Name", "NationalCode", "FundTypeCode", "IsEtf"],
        [
            "Nav",
            "PurchaseNavPerShare",
            "SellNavPerShare",
            "ReturnOf30DaysAgo",
            "ReturnOf90DaysAgo",
            "ReturnOf365DaysAgo",
            "Manager",
        ],
    ),
}


async def _screener(kind: str) -> tuple[dict[str, dict], list[dict]]:
    """Column metadata by name and the data rows, each row re-keyed from column ids to column names."""
    base_path, data_path, _, _ = KINDS[kind]
    base, rows = await asyncio.gather(research(base_path, ttl=http.CACHE_LONG), research(data_path, ttl=300))
    cols = {c["name"]: c for c in base["columns"]}
    by_id = {str(c["id"]): c["name"] for c in base["columns"]}
    named = [{by_id.get(k, k): v for k, v in r.items()} for r in rows or []]
    return cols, named


def _clean(d: dict | None) -> dict | None:
    """Drop the site's *Visible display flags."""
    return {k: v for k, v in d.items() if not k.endswith("Visible")} if d else d


def _fund_row(r: dict, cols: list[str]) -> dict:
    out = {k: r.get(k) for k in cols}
    if "FundTypeCode" in out:
        out["FundType"] = FUND_TYPES.get(str(out.pop("FundTypeCode")), "سایر")
    return out


@tool("Screen stocks or funds by any column")
async def br_screener(
    kind: Annotated[
        Literal["stock", "etf", "mutual"],
        Field(
            description="stock = all stocks; etf = exchange-traded funds with market price; mutual = every fund with NAV issue/redeem prices."
        ),
    ],
    columns: Annotated[
        list[str] | None,
        Field(
            description="Column names to return, e.g. ['PToE','Roe'] (stocks), ['Nav','ReturnOf365DaysAgo'] (funds). Omit for a default set.",
            max_length=20,
        ),
    ] = None,
    sort: Annotated[str | None, Field(description="Column to sort by (descending unless ascending=true).")] = None,
    ascending: bool = False,
    name_contains: Annotated[
        str | None, Field(description="Keep rows whose name/symbol contains this (Persian).")
    ] = None,
    fund_type: Annotated[
        Literal["درآمد ثابت", "سهامی", "کالایی", "سایر"] | None,
        Field(description="Funds only: fixed income, equity, commodity or other."),
    ] = None,
    min_values: Annotated[dict[str, float] | None, Field(description="Keep rows where column >= value.")] = None,
    max_values: Annotated[dict[str, float] | None, Field(description="Keep rows where column <= value.")] = None,
    limit: Annotated[int, Field(ge=1, le=100)] = 20,
    list_columns: Annotated[
        bool, Field(description="Return the available column names and titles instead of rows.")
    ] = False,
) -> dict[str, Any]:
    """Filter, sort and compare all stocks or funds on any column, like Agah's screener pages (900 stocks, 330 ETFs, 475 funds).

    Source: Agah only. Use it for "P/E under 6 and ROE over 30" or "best 1-year fixed-income funds"; br_etfs is the plain
    ETF list. Returns are percent; money and fund Nav are Rial. Column names are the site's (see list_columns); for ETFs
    names ending in 2 are NAV returns, the others market-price returns.
    """
    cols, rows = await _screener(kind)
    if list_columns:
        return {
            "columns": [
                {"name": n, "title": c["title"].strip(), "unit": c.get("suffix") or None}
                for n, c in cols.items()
                if c["title"].strip() and not n.startswith("Null")
            ]
        }
    ident = KINDS[kind][2]
    want = [c for c in (columns or KINDS[kind][3]) if c not in ident]
    for c in [*want, *(sort,), *(min_values or {}), *(max_values or {})]:
        if c and c not in cols:
            raise http.ApiError(f"Unknown column '{c}'. Call with list_columns=true to see the names.")
    name_col = ident[0]
    if name_contains:
        rows = [r for r in rows if norm(name_contains) in norm(r.get(name_col, "")) + norm(r.get("CompanyName", ""))]
    if fund_type:
        rows = [r for r in rows if (FUND_TYPES.get(str(r.get("FundTypeCode")), "سایر")) == fund_type]
    for c, v in (min_values or {}).items():
        rows = [r for r in rows if isinstance(r.get(c), int | float) and r[c] >= v]
    for c, v in (max_values or {}).items():
        rows = [r for r in rows if isinstance(r.get(c), int | float) and r[c] <= v]
    if sort:
        has = [r for r in rows if r.get(sort) is not None]
        rows = sorted(has, key=lambda r: r[sort], reverse=not ascending)
    out = [
        _fund_row(r, [*ident, *want]) if kind != "stock" else {k: r.get(k) for k in [*ident, *want]}
        for r in rows[:limit]
    ]
    return {"matching": len(rows), "units": "percent for returns, Rial for money", "rows": out}


async def fund_card(
    fund_query: Annotated[
        str,
        Field(
            description="Fund national code (11 digits), name, or ETF symbol, e.g. 'هستی بخش آگاه' or '14004856587'."
        ),
    ],
) -> dict[str, Any]:
    """Show one investment fund: type, NAV and issue/redemption prices, returns vs index/dollar/gold, asset mix, top holdings and governance.

    Works for ETFs and unit-trust (mutual) funds; find the code with br_search(kind='fund'). Money is Rial, returns percent.
    For an ETF you also get the live bubble (market price vs NAV) and order book. See br_fund(metric=...) for history.
    """
    f = await fund(fund_query)
    code = f["nationalCode"]
    p = "etf" if f.get("isEtf") else "mutual"
    base, add, over, op = await asyncio.gather(
        research(f"fundDetails/{p}BaseInfo", {"nationalCode": code}),
        research(f"fundDetails/{p}BaseInfoAdditional", {"nationalCode": code}),
        research(f"fundDetails/{'etfOverViewData' if p == 'etf' else 'mutualOverView'}", {"nationalCode": code}),
        research("fundDetails/operationTrend", {"nationalCode": code}),
    )
    over = over or {}
    drop = {"cashFlow", "monthlyBubbleTrend", "todayPrice", "buyUrl", "icon"}
    return {
        "code": code,
        "name": f["name"],
        "symbol": f.get("symbol"),
        "type": f.get("fundType"),
        "etf": f.get("isEtf"),
        "info": {k: v for k, v in (base or {}).items() if k not in drop and k != "optionTopInfo"},
        "details": _clean((add or {}).get("furtherInfo")),
        "composition": (add or {}).get("combination"),
        "overview": {k: v for k, v in over.items() if k not in drop},
        "today_price": {k: v for k, v in (over.get("todayPrice") or {}).items() if k != "todayLastPrice"},
        "operation_returns_pct": op,
    }


METRICS = {  # metric -> (endpoint, column labels after the date; None = site gives no labels, UNCONFIRMED)
    "nav": ("navTrend", ["nav"]),
    "bubble": ("bubbleTrend", ["bubble_pct"]),
    "trade_value": ("tradeValue", ["value", "trades"]),
    "nav_gap": ("navDistPercentTrend", ["nav_gap_pct"]),
    "price": ("priceTrend", None),
    "returns": ("returnsTrend", None),
}


async def fund_trend(
    fund_query: Annotated[str, Field(description="Fund national code, name or symbol.")],
    metric: Annotated[
        Literal["nav", "bubble", "trade_value", "nav_gap", "price", "returns"],
        Field(description="Series to read. price and returns have unlabeled columns."),
    ] = "nav",
    points: Annotated[int, Field(description="Most recent N daily points.", ge=1, le=400)] = 60,
) -> dict[str, Any]:
    """Return a fund's daily history: NAV, bubble percent, traded value and count, NAV gap percent, or the raw price/returns series.

    Rows are oldest first. bubble_pct is market price vs NAV for ETFs. price and returns rows are raw numeric columns
    the site does not label (UNCONFIRMED meaning), so use nav, bubble and trade_value for answers.
    """
    f = await fund(fund_query)
    path, labels = METRICS[metric]
    rows = await research(f"fundTrends/{path}", {"nationalCode": f["nationalCode"]}) or []
    out = []
    for r in tail(rows, points):
        vals = r[1:]
        out.append({"date": day(r[0]), **(dict(zip(labels, vals, strict=False)) if labels else {"values": vals})})
    return {"code": f["nationalCode"], "name": f["name"], "metric": metric, "points": out}


async def fund_compare(
    funds: Annotated[list[str], Field(description="Two to six fund codes or names.", min_length=2, max_length=6)],
    columns: Annotated[
        list[str] | None,
        Field(description="Screener column names to compare; omit for returns, NAV, size and manager."),
    ] = None,
) -> dict[str, Any]:
    """Compare two to six funds on returns, NAV, size, manager, asset mix and fees-related columns from the fund screener.

    Use br_screener(kind='mutual', list_columns=true) for every column name (e.g. EarlyExitPenalty, DividendIntervalPeriod).
    Money is Rial, returns percent.
    """
    resolved = [await fund(q) for q in funds]
    cols, rows = await _screener("mutual")
    want = columns or [
        "ReturnOf30DaysAgo",
        "ReturnOf90DaysAgo",
        "ReturnOf180DaysAgo",
        "ReturnOf365DaysAgo",
        "ReturnOf3YearsAgo",
        "Nav",
        "PurchaseNavPerShare",
        "SellNavPerShare",
        "Manager",
        "EarlyExitPenalty",
        "AllAssetsListAsString",
    ]
    for c in want:
        if c not in cols:
            raise http.ApiError(f"Unknown column '{c}'.")
    by_code = {r.get("NationalCode"): r for r in rows}
    out = []
    for f in resolved:
        r = by_code.get(f["nationalCode"])
        out.append({"code": f["nationalCode"], "name": f["name"], **_fund_row(r or {}, ["FundTypeCode", *want])})
    return {"funds": out}


async def options(
    underlying: Annotated[
        str | None, Field(description="Underlying symbol, e.g. 'شپنا' (Persian), or part of the company name.")
    ] = None,
    side: Annotated[
        Literal["call", "put"] | None, Field(description="Call (اختیار خرید) or put (اختیار فروش) only.")
    ] = None,
    expiry: Annotated[str | None, Field(description="Expiry date YYYY-MM-DD (Gregorian) as in the results.")] = None,
    sort: Annotated[Literal["value", "open_interest", "leverage", "days", "strike"], Field()] = "value",
    limit: Annotated[int, Field(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    """List stock-option contracts (about 1,600) with price, strike, expiry, moneyness, open interest and real/legal flows.

    Use it for "options on Shepna expiring soon" or "most traded calls". Prices Rial per underlying share; contract size
    is in each row. ITM/OTM is the moneyness the site shows. Then call br_options(option=...) for one contract.
    """
    names, rows = await asyncio.gather(
        research("optionScreener/baseInfo", ttl=http.CACHE_LONG), research("optionScreener/data", ttl=300)
    )
    recs = [dict(zip(names, r, strict=False)) for r in rows or []]
    if underlying:
        q = norm(underlying)
        recs = [r for r in recs if q in norm(r["Symbol"]) or q in norm(r["CompanyName"])]
    if side:
        recs = [r for r in recs if (r["OptionTypeText"] == "اختیار خرید") == (side == "call")]
    if expiry:
        recs = [r for r in recs if str(r["EndDate"]).startswith(expiry)]
    key = {
        "value": "TransactionValue",
        "open_interest": "OpenPosition",
        "leverage": "ActualLeverage",
        "days": "DayDiff",
        "strike": "StrikePrice",
    }[sort]
    recs.sort(key=lambda r: r.get(key) or 0, reverse=sort in ("value", "open_interest", "leverage"))
    return {
        "matching": len(recs),
        "options": [
            {
                "isin": r["OptionIsin"],
                "name": r["Name"],
                "side": r["OptionTypeText"],
                "moneyness": r["OptionStatusSingleEnglishText"],
                "underlying": r["Symbol"],
                "underlying_price": r["InstrumentLastPrice"],
                "strike": r["StrikePrice"],
                "expiry": str(r["EndDate"])[:10],
                "days_left": r["DayDiff"],
                "last": r["LastPrice"],
                "change_pct": r["LastPriceChangePercent"],
                "value": r["TransactionValue"],
                "quantity": r["TradedQuantity"],
                "open_interest": r["OpenPosition"],
                "leverage": r["ActualLeverage"],
                "contract_size": r["ContractSize"],
            }
            for r in recs[:limit]
        ],
    }


async def option(
    option: Annotated[
        str, Field(description="Option ISIN from br_options, e.g. 'IRO9PNES0A01'.", pattern=r"^IR[A-Z0-9]{10}$")
    ],
) -> dict[str, Any]:
    """Show one option contract: price, strike, expiry, margin, break-even distance, order book and real/legal flows.

    Money is Rial. Margins are per contract. Black-Scholes inputs (volatility, rate) are the site's defaults.
    """
    base, over = await asyncio.gather(
        research("optionDetails/baseInfo", {"isin": option}), research("optionDetails/overviewData", {"isin": option})
    )
    return {
        "info": {k: v for k, v in (base or {}).items() if k not in ("buyUrl", "icon")},
        "trading": over.get("tradingInfo"),
        "underlying": over.get("furtherInfo"),
        "order_book": over.get("bestLimits"),
        "flows": over.get("individualCorporation"),
    }
