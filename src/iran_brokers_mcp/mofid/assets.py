"""Funds, bonds, industries, currency, gold and commodity tools."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any, Literal

from pydantic import Field

from ..registry import tool
from .http import CACHE_LONG, WWW, fetch
from .util import norm, pct, pick, rows

ETF_TYPES = Literal[
    "stock", "fixedincome", "goldetf", "venturecapital", "silveretf", "mixed", "leveraged", "realstates",
    "privateequity", "fundoffunds", "agricultureetf", "projects",
]  # fmt: skip


async def etfs(
    type: Annotated[
        ETF_TYPES | None,
        Field(description="Fund family; omit for all 375. fixedincome = income funds, goldetf = gold-backed."),
    ] = None,
    query: Annotated[str | None, Field(description="Part of the fund symbol, Persian or Latin.")] = None,
    sort: Annotated[
        Literal["value", "market_cap", "change", "nav_gap", "volume"],
        Field(
            description="value = traded value today, change = price change %, nav_gap = market price above(+)/below(-) NAV in %."
        ),
    ] = "value",
    limit: Annotated[int, Field(description="Rows to return.", ge=1, le=100)] = 20,
    ascending: bool = False,
) -> dict[str, Any]:
    """List exchange-traded funds with price, NAV and the gap between them, traded value and market cap.

    Covers equity, fixed-income, gold, leveraged and other ETFs traded on the Tehran exchange. Open one with br_stock, br_stock(order_book=true) or br_history using its ISIN.
    """
    keys = {
        "value": "value",
        "market_cap": "marketCap",
        "change": "changePercent",
        "nav_gap": "navPercent",
        "volume": "volume",
    }
    data = rows(await fetch("/marketEtf"))
    q = norm(query)
    data = [r for r in data if (not type or r.get("type") == type) and (not q or q in norm(r.get("name")))]
    k = keys[sort]
    data.sort(key=lambda r: (r.get(k) is None, (r.get(k) or 0) * (1 if ascending else -1)))
    return {
        "count": len(data),
        "funds": [
            pick(
                r,
                {
                    "name": "symbol",
                    "isin": "isin",
                    "type": "type",
                    "close": "close",
                    "navClose": "nav",
                    "navPercent": "nav_gap_pct",
                    "changePercent": "change_pct",
                    "value": "value",
                    "volume": "volume",
                    "marketCap": "market_cap",
                    "signalState": "signal",
                },
            )  # fmt: skip
            for r in data[:limit]
        ],
    }


@tool("Mofid's own funds")
async def br_mofid_funds() -> dict[str, Any]:
    """List the 19 investment funds managed under the Mofid brand with type, risk level, dividend policy and asset mix.

    Source: Mofid only. A catalogue without prices or NAV: use br_etfs or br_stock for those.
    """
    d = await fetch("/funds/", site=WWW, ttl=CACHE_LONG)
    funds = d.get("value") or []
    return {
        "count": len(funds),
        "funds": [
            {
                **pick(
                    f,
                    {
                        "code": "code",
                        "fullTitle": "name",
                        "fundType": "type",
                        "type": "issue_redeem",
                        "riskLevel": "risk",
                        "dividendPolicy": "dividends",
                        "website": "website",
                        "prospectusUrl": "prospectus_url",
                        "property": "features",
                        "learningWebsite": "learn_url",
                    },
                ),  # fmt: skip
                "asset_mix_pct": {a["title"]: a["percent"] for a in f.get("assetCompositions") or []},
            }
            for f in funds
        ],
    }


@tool("Bonds and sukuk")
async def br_bonds(
    group: Annotated[
        Literal["TreasuryBills", "GAMBills", "CouponBonds", "NonCouponBonds"] | None,
        Field(description="TreasuryBills = T-bills, GAMBills = Gam, CouponBonds = coupon sukuk/bonds, NonCouponBonds."),
    ] = None,
    query: Annotated[str | None, Field(description="Part of the symbol or name.")] = None,
    sort: Annotated[
        Literal["ytm", "days_to_maturity", "volume"],
        Field(description="ytm = yield to maturity of the last closing trade, highest first."),
    ] = "ytm",
    traded_only: Annotated[bool, Field(description="Hide bonds without a price.")] = True,
    min_days: Annotated[
        int, Field(description="Hide bonds maturing in fewer days (very short bills show absurd yields).", ge=0)
    ] = 30,
    limit: Annotated[int, Field(description="Rows to return.", ge=1, le=100)] = 20,
) -> dict[str, Any]:
    """List exchange-traded bonds, sukuk and treasury bills with maturity, coupon, price and yield to maturity.

    Source: Mofid only; bonds are not in br_search. Prices are Rial per 1,000,000 par. `ytm` is percent per year;
    `coupon_rate` is a fraction (0.18 = 18%).
    """
    d = await fetch("/bonds", ttl=300)
    q = norm(query)
    out, seen = [], set()
    for g, items in d.items():
        if group and g != group:
            continue
        for b in items:
            quote = (b.get("quotes") or [{}])[0]
            if (
                (traded_only and not quote.get("closing"))
                or (b.get("daysToMaturity") or 0) < min_days
                or b.get("isin") in seen
            ):
                continue
            seen.add(b.get("isin"))  # the same bill can sit in two groups
            if q and q not in norm(b.get("symbolFa")) and q not in norm(b.get("nameFa")):
                continue
            out.append(
                {
                    "group": g,
                    **pick(
                        b,
                        {
                            "isin": "isin",
                            "symbolFa": "symbol",
                            "nameFa": "name",
                            "type": "type",
                            "issuerClass": "issuer_class",
                            "maturity": "maturity",
                            "daysToMaturity": "days_to_maturity",
                            "parValue": "par",
                            "couponRate": "coupon_rate",
                            "numOfPayments": "payments_per_year",
                            "callable": "callable",
                        },
                    ),  # fmt: skip
                    "close": quote.get("closing"),
                    "ytm": quote.get("closingYTM"),
                    "volume": quote.get("volume"),
                }
            )
    keys = {"ytm": ("ytm", True), "days_to_maturity": ("days_to_maturity", False), "volume": ("volume", True)}
    k, desc = keys[sort]
    out.sort(key=lambda r: (r.get(k) is None, -(r.get(k) or 0) if desc else (r.get(k) or 0)))
    return {"count": len(out), "bonds": out[:limit]}


async def industries(
    sort: Annotated[
        Literal["market_cap", "return_1w", "return_1m", "return_3m", "return_1y", "p2e", "dividend_yield", "value"],
        Field(description="Sort key; value = traded value today."),
    ] = "market_cap",
    limit: Annotated[int, Field(description="Rows to return.", ge=1, le=60)] = 15,
    ascending: bool = False,
) -> dict[str, Any]:
    """Rank the 53 bourse industries by market cap, return, P/E, dividend yield or traded value, with margins and valuation ratios.

    Returns are percent over the period; ratios are plain numbers.
    """
    keys = {"market_cap": "marketCap", "return_1w": "return1W", "return_1m": "return1M", "return_3m": "return3M", "return_1y": "return1Y",
            "p2e": "p2e", "dividend_yield": "dividendYield", "value": "value"}  # fmt: skip
    data = rows(await fetch("/industry/overview"))
    k = keys[sort]
    data.sort(key=lambda r: (r.get(k) is None, (r.get(k) or 0) * (1 if ascending else -1)))
    return {
        "count": len(data),
        "industries": [
            {
                **pick(
                    r,
                    {
                        "industryFA": "industry",
                        "isin": "isin",
                        "marketCap": "market_cap",
                        "value": "value",
                        "p2e": "p2e",
                        "p2b": "p2b",
                        "p2s": "p2s",
                        "roe": "roe",
                        "npMargin": "net_margin",
                        "dividendYield": "dividend_yield",
                        "beta": "beta",
                    },
                ),  # fmt: skip
                "returns_pct": {p: pct(r.get(f"return{p.upper()}")) for p in ("1w", "1m", "3m", "6m", "1y", "3y")},
            }
            for r in data[:limit]
        ],
    }


async def fx_gold(
    kind: Annotated[
        Literal["currency", "gold_coin", "commodity"],
        Field(
            description="currency = free-market currencies and crypto (Rial), gold_coin = coins, 18k gold, ounce and 1-day coin certificates (Rial), commodity = oil, metals (USD)."
        ),
    ] = "gold_coin",
    query: Annotated[
        str | None, Field(description="Filter by code (USD, EUR, USDT, BTC, IMCOIN, Gold18, WTI, Cu) or Persian name.")
    ] = None,
) -> dict[str, Any]:
    """Return live free-market prices: USD and other currencies, crypto, Emami and other coins, 18k gold, global ounce, oil and metals.

    Each row has the price, change, time and 7/30/365-day low and high where the site provides them. Rial prices: divide by 10 for Toman.
    """
    q = norm(query)
    if kind == "commodity":
        data = rows(await fetch("/commodityLive"))
        return {
            "kind": kind,
            "items": [
                {
                    **pick(
                        r,
                        {
                            "abbr": "code",
                            "name": "name",
                            "catFa": "category",
                            "close": "price",
                            "lastDate": "date",
                            "updateTime": "time",
                        },
                    ),
                    "change_pct": {
                        "1d": r.get("pct1d"),
                        "1m": r.get("pct1m"),
                        "3m": r.get("pct3m"),
                        "6m": r.get("pct6m"),
                        "1y": r.get("pct1y"),
                    },
                }
                for r in data
                if not q or q in norm(r.get("abbr")) or q in norm(r.get("name"))
            ],
        }
    d = await fetch("/currencyGoldLive")
    data = rows(d.get("market_currency_live" if kind == "currency" else "market_gold_coin_live"))
    return {
        "kind": kind,
        "items": [
            {
                **pick(
                    r,
                    {
                        "abbr": "code",
                        "nameFa": "name",
                        "catFa": "category",
                        "close": "price",
                        "chperc": "change_pct",
                        "chval": "change",
                        "time": "time",
                        "min_30": "low_30d",
                        "max_30": "high_30d",
                        "min_365": "low_1y",
                        "max_365": "high_1y",
                    },
                ),  # fmt: skip
            }
            for r in data
            if not q or q in norm(r.get("abbr")) or q in norm(r.get("nameFa"))
        ],
    }


async def rate_history(
    code: Annotated[
        str,
        Field(
            description="Code from br_rates: USD, EUR, USDT, BTC (currency); IMCOIN, BACOIN, Gold18 (gold/coin); WTI, BRENT, Cu, XAUUSD (commodity).",
            min_length=2,
            max_length=20,
            pattern=r"^[A-Za-z0-9]+$",
        ),
    ],
    kind: Annotated[
        Literal["currency", "gold_coin", "commodity"], Field(description="Same meaning as in br_rates.")
    ] = "currency",
    days: Annotated[int, Field(description="How many calendar days back.", ge=1, le=3650)] = 60,
    limit: Annotated[int, Field(description="Return only the newest N days.", ge=1, le=400)] = 90,
) -> dict[str, Any]:
    """Return the daily price history (close, change %) of a currency, coin, gold or commodity code, oldest first.

    Currency and coin prices are Rial, commodities USD. Currency and commodity history reaches back to 2019-2021; gold/coin rows also carry open, high and low.
    """
    if kind == "currency":
        d = rows(await fetch("/currencyQuotes/history", {"abbreviation": code}, ttl=300))
    elif kind == "commodity":
        d = rows(await fetch("/commodityQuotes/history", {"abbreviation": code}, ttl=300))
    else:
        d = (rows(await fetch("/commodityHistory", {"abbreviations": code}, ttl=300)) or [{}])[0].get("history") or []
    cut = date.fromordinal(date.today().toordinal() - days).isoformat()
    out = sorted(
        (
            pick(
                r,
                {
                    "dateTimeLocal": "date",
                    "open": "open",
                    "high": "high",
                    "low": "low",
                    "close": "close",
                    "changePercent": "change_pct",
                },
            )
            for r in d
        ),
        key=lambda r: r.get("date", ""),
    )
    out = [r for r in out if r.get("date", "") >= cut]
    return {"code": code, "kind": kind, "count": len(out), "days": out[-limit:]}
