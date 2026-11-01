"""Call the tools of the two original servers (mf_*, ag_*) through their merged br_* replacements.

The provider tests were written against the standalone servers. This maps each old call to the merged tool with
source='mofid' or 'agah' and unwraps that source's block, so the old assertions still check the provider output.
"""

from __future__ import annotations

from typing import Any


def _pick(a: dict, *keys: str) -> dict:
    return {k: a[k] for k in keys if k in a}


def _mf_search(a):
    kind = {"etf": "fund"}.get(a.get("kind"), a.get("kind"))
    return "br_search", {**a, **({"kind": kind} if kind else {}), "source": "mofid"}, "mofid"


def _mf_learn(a):
    if "post_id" in a:
        return "br_learn", {"ref": str(a["post_id"]), "max_chars": a.get("chars", 6000), "source": "mofid"}, "mofid"
    cat = {"category_id": str(a["category_id"])} if "category_id" in a else {}
    return "br_learn", {**_pick(a, "query", "page", "per_page"), **cat, "source": "mofid"}, "mofid"


def _ag_search(a):
    kind = {"any": "all", "industry": "index"}.get(a.get("kind", "any"), a.get("kind"))
    return "br_search", {**a, "kind": kind, "source": "agah"}, "agah"


def _ag_articles(a):
    args = {**_pick(a, "query", "category_id"), "page": a.get("page", 0) + 1, "source": "agah"}
    if "page_size" in a:
        args["per_page"] = a["page_size"]
    return "br_learn", args, "agah"


MF = {
    "mf_search": _mf_search,
    "mf_quote": lambda a: ("br_quotes", a, None),
    "mf_symbol": lambda a: ("br_stock", {"symbol": a["isin"], "source": "mofid"}, "mofid"),
    "mf_history": lambda a: (
        "br_history",
        {**{k: v for k, v in a.items() if k != "isin"}, "symbol": a["isin"], "source": "mofid"},
        "mofid",
    ),
    "mf_market_summary": lambda a: ("br_market", {"source": "mofid"}, "mofid"),
    "mf_index": lambda a: (
        "br_index",
        {
            **{k: v for k, v in a.items() if k != "isin"},
            **({"index": a["isin"]} if "isin" in a else {}),
            "source": "mofid",
        },
        "mofid",
    ),
    "mf_market_top": lambda a: ("br_market_top", a, None),
    "mf_etfs": lambda a: ("br_etfs", {**a, "source": "mofid"}, "mofid"),
    "mf_mofid_funds": lambda a: ("br_mofid_funds", a, None),
    "mf_bonds": lambda a: ("br_bonds", a, None),
    "mf_industries": lambda a: ("br_industries", {**a, "source": "mofid"}, "mofid"),
    "mf_fx_gold": lambda a: ("br_rates", a, None),
    "mf_rate_history": lambda a: (
        "br_rates",
        {
            "query": a["code"],
            "kind": a.get("kind", "currency"),
            "history_days": a.get("days", 60),
            "limit": a.get("limit", 90),
        },
        None,
    ),
    "mf_news": lambda a: ("br_news", a, None),
    "mf_announcements": lambda a: ("br_announcements", a, None),
    "mf_scores": lambda a: ("br_scores", a, None),
    "mf_learn": _mf_learn,
    "mf_learn_categories": lambda a: ("br_learn", {**a, "kind": "categories", "source": "mofid"}, "mofid"),
}

AG = {
    "ag_search": _ag_search,
    "ag_stock": lambda a: ("br_stock", {"symbol": a["stock"], "source": "agah"}, "agah"),
    "ag_stocks": lambda a: ("br_stocks", a, None),
    "ag_stock_history": lambda a: (
        "br_history",
        {"symbol": a["stock"], "limit": a.get("days", 30), "source": "agah"},
        "agah",
    ),
    "ag_screener": lambda a: ("br_screener", a, None),
    "ag_fund": lambda a: ("br_fund", {"funds": [a["fund_query"]]}, None),
    "ag_fund_trend": lambda a: (
        "br_fund",
        {"funds": [a["fund_query"]], "metric": a.get("metric", "nav"), "points": a.get("points", 60)},
        None,
    ),
    "ag_fund_compare": lambda a: ("br_fund", {"funds": a["funds"], **_pick(a, "columns")}, None),
    "ag_options": lambda a: ("br_options", a, None),
    "ag_option": lambda a: ("br_options", {"option": a["option"]}, None),
    "ag_technical": lambda a: ("br_technical", a, None),
    "ag_stock_events": lambda a: ("br_stock_events", a, None),
    "ag_industries": lambda a: ("br_industries", {"limit": 60, **a, "source": "agah"}, "agah"),
    "ag_index_history": lambda a: ("br_index", {"days": 30, "source": "agah"}, "agah"),
    "ag_market_pulse": lambda a: ("br_market", {**a, "source": "agah"}, "agah"),
    "ag_market_overview": lambda a: ("br_market", {"breadth": True, "source": "agah"}, "agah"),
    "ag_articles": _ag_articles,
    "ag_article": lambda a: (
        "br_learn",
        {"ref": a["slug"], "max_chars": a.get("max_chars", 8000), "source": "agah"},
        "agah",
    ),
    "ag_academy": lambda a: ("br_learn", {**a, "kind": "courses", "source": "agah"}, "agah"),
}


class Legacy:
    def __init__(self, client: Any, table: dict):
        self._client, self._table = client, table

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)

    async def call_tool(self, name: str, args: dict | None = None):
        convert = self._table.get(name)
        new, a, key = convert(dict(args or {})) if convert else (name, args or {}, None)
        r = await self._client.call_tool(new, a)
        if key and not r.is_error:
            r.structured_content = r.structured_content[key]
        return r
