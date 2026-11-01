import httpx
import pytest
from conftest import fixture

from iran_brokers_mcp.agah import http as agah_http
from iran_brokers_mcp.mofid import http as mofid_http

pytestmark = pytest.mark.anyio

TOOLS = {
    "br_search", "br_stock", "br_history", "br_market", "br_industries", "br_index", "br_etfs", "br_learn",
    "br_quotes", "br_market_top", "br_bonds", "br_rates", "br_news", "br_announcements", "br_scores", "br_mofid_funds",
    "br_stocks", "br_screener", "br_fund", "br_options", "br_technical", "br_stock_events",
}  # fmt: skip
G = "/gateway"
R = "/backend/"
MSMI = "IRO1MSMI0001"
FOLD = "IRO1FOLD0001"


def q(call: httpx.Request) -> dict[str, str]:
    return dict(call.url.params.multi_items())


async def call(client, name, args=None):
    r = await client.call_tool(name, args or {})
    assert not r.is_error, r.content[0].text
    return r.structured_content


def fund_screener(api, kind, names, rows):
    """Fake the Agah fund screener: column metadata plus rows keyed by column id."""
    cols = [{"id": i, "name": n, "title": n} for i, n in enumerate(names, 1)]
    api[R + f"fundScreener/{kind}BaseInfo"] = {"data": {"columns": cols}}
    api[R + f"fundScreener/{kind}Data"] = {"data": [{str(i): r[n] for i, n in enumerate(names, 1)} for r in rows]}


async def test_all_tools_are_read_only_and_described(client):
    tools = (await client.list_tools()).tools
    assert {t.name for t in tools} == TOOLS and len(tools) == 22
    for t in tools:
        assert t.annotations.read_only_hint is True
        assert t.description and t.description.splitlines()[0].endswith(".")
        assert not t.name.startswith(("mf_", "ag_")) and "mf_" not in t.description and "ag_" not in t.description


async def test_every_host_sends_its_frontends_headers(api):
    """Pinned per host: Mofid's three sites and Agah's three (the Agah test file pins research too)."""
    api[f"{G}/marketSummary"] = []
    api["/api/funds/"] = {"value": []}
    api["/wp-json/wp/v2/posts"] = []
    api[R + "shakhes"] = {"data": []}
    api["/api/v1/contents"] = {"data": {"items": []}}
    api["/api/topic/list"] = {"item": []}
    await mofid_http.fetch("/marketSummary")
    await mofid_http.fetch("/funds/", site=mofid_http.WWW)
    await mofid_http.fetch("/posts", site=mofid_http.LEARN)
    await agah_http.fetch(agah_http.RESEARCH + "/shakhes")
    await agah_http.fetch(agah_http.PEDIA + "/contents")
    await agah_http.fetch(agah_http.ACADEMY + "/topic/list")
    seen = {c.url.host: c.headers for c in api.calls}
    assert set(seen) == {
        "bv.emofid.com", "www.emofid.com", "learning.emofid.com",
        "research.agah.com", "pediaapi.agah.com", "academyapi.agah.com",
    }  # fmt: skip
    json_accept = "application/json, text/plain, */*"
    expected = {  # host -> (referer, accept, sec-fetch-site, origin)
        "bv.emofid.com": ("https://bv.emofid.com/market", json_accept, "same-origin", None),
        "www.emofid.com": ("https://www.emofid.com/fa/funds/", json_accept, "same-origin", None),
        "learning.emofid.com": ("https://learning.emofid.com/", json_accept, "same-origin", None),
        "research.agah.com": ("https://research.agah.com/", "*/*", "same-origin", None),
        "pediaapi.agah.com": ("https://pedia.agah.com/", json_accept, "same-site", "https://pedia.agah.com"),
        "academyapi.agah.com": ("https://academy.agah.com/", json_accept, "same-site", "https://academy.agah.com"),
    }
    for host, (referer, accept, site, origin) in expected.items():
        h = seen[host]
        assert (h["referer"], h["accept"], h["sec-fetch-site"], h.get("origin")) == (referer, accept, site, origin), (
            host
        )
        assert (h["sec-fetch-mode"], h["sec-fetch-dest"], h["priority"]) == ("cors", "empty", "u=1, i"), host
        assert h["accept-language"].startswith("fa-IR"), host
        assert ("accept-info" in h) == (host == "bv.emofid.com"), host  # only the Bourse View gateway wants it
    assert seen["research.agah.com"]["content-type"] == "application/json"


def test_one_proxy_variable_feeds_both_clients_and_the_old_ones_are_ignored(monkeypatch):
    monkeypatch.setenv("IRAN_BROKERS_MCP_PROXY", "http://a:1, http://b:2")
    monkeypatch.setenv("MOFID_MCP_PROXY", "http://old:3")
    monkeypatch.setenv("AGAH_MCP_PROXY", "http://old:4")
    assert mofid_http._proxies() == agah_http._proxies() == ["http://a:1", "http://b:2"]


async def test_search_answers_per_source_and_survives_one_failing(client, api):
    api[f"{G}/stockTickers"] = fixture("tickers.json")
    r = await call(client, "br_search", {"query": "فملي"})  # Arabic ye; agah routes missing -> 404
    assert r["mofid"]["results"][0]["isin"] == MSMI
    assert "404" in r["agah"]["error"] or "nothing" in r["agah"]["error"]
    api[R + "stock/all"] = fixture("stock_all.json")
    r = await call(client, "br_search", {"query": "فولاد"})
    assert r["agah"]["results"][0]["id"] == FOLD and r["mofid"]["count"] == 0
    r = await call(client, "br_search", {"query": "فولاد", "source": "agah"})
    assert set(r) == {"agah"}
    r = await call(client, "br_search", {"query": "اهرم", "kind": "fund", "source": "mofid"})
    assert r["mofid"]["results"][0]["symbol"] == "اهرم"


async def test_search_with_every_source_failing_is_an_error(client, api):
    r = await client.call_tool("br_search", {"query": "x"})
    assert r.is_error and "mofid:" in r.content[0].text and "agah:" in r.content[0].text
    r = await client.call_tool("br_search", {"query": "x", "source": "agah"})
    assert r.is_error and "mofid:" not in r.content[0].text


async def test_stock_pairs_both_cards_and_resolves_a_mofid_symbol(client, api):
    api[f"{G}/stockTickers"] = fixture("tickers.json")
    api[f"{G}/stocksQuotes"] = fixture("stocks_quotes.json")
    api[f"{G}/stockInfo"] = fixture("stock_info.json")
    api[f"{G}/stockBidAsk"] = fixture("bid_ask.json")
    api[R + "stock/all"] = fixture("stock_all.json")
    api[R + "stockDetails/baseInfo"] = fixture("stock_baseinfo.json")
    api[R + "stockDetails/overview"] = fixture("stock_overview.json")
    api[R + "stockDetails/returns"] = fixture("stock_returns.json")
    r = await call(client, "br_stock", {"symbol": FOLD, "order_book": True})
    assert r["mofid"]["isin"] == FOLD and r["mofid"]["order_book"]["levels"][0]["rank"] == 1
    assert r["agah"]["isin"] == FOLD and r["agah"]["ratios"]["pToE"] > 0
    api.calls.clear()
    r = await call(client, "br_stock", {"symbol": "فملي", "source": "mofid"})  # a symbol is resolved to the ISIN first
    assert r["mofid"]["isin"] == MSMI and set(r) == {"mofid"}
    assert q(next(c for c in api.calls if c.url.path.endswith("/stocksQuotes"))) == {"isin": MSMI}
    bad = await client.call_tool("br_stock", {"symbol": "zzzz", "source": "mofid"})
    assert bad.is_error and "No symbol" in bad.content[0].text


async def test_history_defaults_to_mofid_candles_and_agah_is_daily_only(client, api):
    api[f"{G}/quotes/history"] = fixture("history.json")
    api[R + "stock/all"] = fixture("stock_all.json")
    api[R + "stockDetails/tradeHistorical"] = fixture("stock_history.json")
    r = await call(client, "br_history", {"symbol": MSMI, "days": 10, "limit": 2})
    assert set(r) == {"mofid"} and r["mofid"]["candles"][-1]["close"] == 26850
    r = await call(client, "br_history", {"symbol": FOLD, "limit": 2, "source": "all"})
    assert len(r["agah"]["days"]) == 2 and r["mofid"]["isin"] == FOLD
    r = await call(client, "br_history", {"symbol": FOLD, "resolution": "5", "source": "all"})
    assert "daily rows only" in r["agah"]["error"] and r["mofid"]["resolution"] == "5"


async def test_market_pairs_summary_and_pulse_with_optional_breadth(client, api):
    api[f"{G}/marketSummary"] = fixture("market_summary.json")
    api[f"{G}/marketQueueCount"] = fixture("queues.json")
    api[R + "landing/baseInfo"] = {"data": {"bourseIndexToday": {"last": 1}, "cashFlow": 5, "topIndexAffects": []}}
    api[R + "landing/globalPrices"] = {
        "data": {"currencies": [{"code": "x:USD", "nameFa": "دلار", "closingPrice": 100}]}
    }
    r = await call(client, "br_market")
    assert any(m["isin"] == "IREXOVERALLINDEX" for m in r["mofid"]["markets"])
    assert r["agah"]["net_money_flow"] == 5 and r["agah"]["prices"]["currencies"][0]["code"] == "USD"
    assert "breadth" not in r["agah"]
    api[R + "market/instrumentOverview"] = {"data": {"totalTradeValue": 9, "marketPToE": 7}}
    api[R + "market/instrumentOverviewAdditional"] = {"data": {"customerPowers": []}}
    r = await call(client, "br_market", {"breadth": True, "source": "agah"})
    assert r["agah"]["breadth"]["market_p_to_e"] == 7 and set(r) == {"agah"}


async def test_industries_sorts_per_source_and_industry_detail_is_agah_only(client, api):
    api[f"{G}/industry/overview"] = fixture("industry_overview.json")
    rows = [
        {"name": "a", "isin": "IRX6A", "tradeCapital": 5, "changePercentageRelativeToPreviousDay": 1},
        {"name": "b", "isin": "IRX6B", "tradeCapital": 9, "changePercentageRelativeToPreviousDay": 2},
    ]
    api[R + "market/industryInfo"] = {"data": rows}
    r = await call(client, "br_industries", {"limit": 2})
    assert [i["name"] for i in r["agah"]["industries"]] == ["b", "a"] and len(r["mofid"]["industries"]) == 2
    r = await call(client, "br_industries", {"limit": 1, "ascending": True, "source": "agah"})
    assert [i["name"] for i in r["agah"]["industries"]] == ["a"]
    r = await call(client, "br_industries", {"sort": "p2e", "limit": 1})
    assert r["mofid"]["industries"] and "cannot sort industries by p2e" in r["agah"]["error"]
    r = await call(client, "br_industries", {"sort": "money_flow", "limit": 1})
    assert "cannot sort" in r["mofid"]["error"] and r["agah"]["industries"]
    r = await client.call_tool("br_industries", {"industry": "IRX6A", "source": "mofid"})
    assert r.is_error and "br_index" in r.content[0].text


async def test_index_card_history_and_agah_series(client, api):
    api[f"{G}/overall/indice/detail/IREXOVERALLINDEX"] = fixture("index_detail.json")
    api[f"{G}/overall/indice/financialSummary/IREXOVERALLINDEX"] = fixture("index_fin.json")
    api[f"{G}/marketPriceMeanLive"] = fixture("index_mean.json")
    api[f"{G}/quotes/history"] = fixture("history.json")
    api[R + "shakhes"] = {"data": [[739920, 7800000.0], [739921, 7810000.0]]}
    r = await call(client, "br_index")
    assert set(r) == {"mofid"} and r["mofid"]["isin"] == "IREXOVERALLINDEX" and "history" not in r["mofid"]
    r = await call(client, "br_index", {"days": 30})
    assert r["mofid"]["history"]["candles"] and r["mofid"]["ratios"]
    assert r["agah"]["points"] == [
        {"date": "2026-10-31", "value": 7800000.0},
        {"date": "2026-11-01", "value": 7810000.0},
    ]
    assert q(next(c for c in api.calls if c.url.path.endswith("/shakhes"))) == {"market": "0", "range": "0"}
    api[f"{G}/overall/indice/detail/IRX6XTPI0006"] = fixture("index_detail.json")
    r = await call(client, "br_index", {"index": "IRX6XTPI0006", "days": 30, "source": "all"})
    assert "total index" in r["agah"]["error"]
    bad = await client.call_tool("br_index", {"source": "agah"})
    assert bad.is_error and "set days" in bad.content[0].text


async def test_etfs_maps_sorts_and_types_for_agah(client, api):
    api[f"{G}/marketEtf"] = fixture("etfs.json")
    names = ["Name", "NationalCode", "FundTypeCode", "TradeCapital", "BubbleLive", "LastPrice"]
    names += ["LastPriceChangePercent", "Nav", "ReturnOf30DaysAgo2", "ReturnOf365DaysAgo2", "Manager"]
    row = dict.fromkeys(names)
    rows = [
        {**row, "Name": "x", "FundTypeCode": "4", "TradeCapital": 5, "BubbleLive": 0.1},
        {**row, "Name": "y", "FundTypeCode": "4", "TradeCapital": 9, "BubbleLive": -0.2},
        {**row, "Name": "z", "FundTypeCode": "6", "TradeCapital": 99, "BubbleLive": 0.3},
    ]
    fund_screener(api, "etf", names, rows)
    r = await call(client, "br_etfs", {"type": "fixedincome", "source": "agah", "sort": "nav_gap"})
    assert [f["Name"] for f in r["agah"]["rows"]] == ["x", "y"] and r["agah"]["rows"][0]["FundType"] == "درآمد ثابت"
    r = await call(client, "br_etfs", {"source": "all", "limit": 3})
    assert r["mofid"]["funds"] and [f["Name"] for f in r["agah"]["rows"]] == ["z", "y", "x"]
    r = await call(client, "br_etfs", {"source": "all", "sort": "market_cap", "type": "mixed"})
    assert r["mofid"]["count"] >= 0 and "market-cap" in r["agah"]["error"]
    r = await call(client, "br_etfs", {"source": "all", "type": "mixed"})
    assert "Mofid only" in r["agah"]["error"]
    assert set(await call(client, "br_etfs", {})) == {"mofid"}  # default source


async def test_learn_searches_both_and_routes_ref_by_shape(client, api):
    api["/wp-json/wp/v2/posts"] = lambda r: httpx.Response(
        200, json=[{"id": 7, "date": "2026-10-31", "title": {"rendered": "T"}, "excerpt": {"rendered": "s"}}],
        headers={"X-WP-Total": "1", "X-WP-TotalPages": "1"},
    )  # fmt: skip
    api["/wp-json/wp/v2/posts/7"] = {"id": 7, "title": {"rendered": "T"}, "content": {"rendered": "<p>body</p>"}}
    api["/wp-json/wp/v2/categories"] = [{"id": 1, "name": "c", "count": 2}]
    api["/api/v1/contents/search"] = fixture("pedia_list.json")
    api["/api/v1/contents/return-on-equity"] = fixture("pedia_article.json")
    api["/api/topic/list"] = {"item": [{"id": 1, "title": " t ", "slug": "t"}]}
    r = await call(client, "br_learn", {"query": "صندوق طلا", "page": 2})
    assert r["mofid"]["articles"][0]["id"] == 7 and r["agah"]["articles"]
    assert {c.url.params.get("page") for c in api.calls if c.url.path.endswith("/posts")} == {"2"}
    assert {c.url.params.get("pageIndex") for c in api.calls if c.url.path.endswith("/search")} == {
        "1"
    }  # 0-based there
    assert (await call(client, "br_learn", {"ref": "7"}))["mofid"]["text"] == "body"
    assert (await call(client, "br_learn", {"ref": "return-on-equity"}))["agah"]["summary"]
    assert (await call(client, "br_learn", {"kind": "categories"}))["mofid"]["categories"][0]["id"] == 1
    assert (await call(client, "br_learn", {"kind": "courses"}))["agah"]["topics"][0]["title"] == "t"
    r = await call(client, "br_learn", {"query": "abc"})  # Pedia search wants 4+ characters
    assert "4 characters" in r["agah"]["error"] and "articles" in r["mofid"]
    for bad in ({"category_id": "3"}, {"ref": "a b"}, {"kind": "categories", "source": "agah"}):
        assert (await client.call_tool("br_learn", bad)).is_error


async def test_pedia_outside_iran_keeps_the_clear_error_next_to_mofid_articles(client, api):
    api["/wp-json/wp/v2/posts"] = lambda r: httpx.Response(200, json=[], headers={"X-WP-Total": "0"})

    def timeout(request):
        raise httpx.ReadTimeout("slow")

    api["/api/v1/contents/search"] = timeout
    r = await call(client, "br_learn", {"query": "صندوق طلا"})
    assert (
        "only from Iran" in r["agah"]["error"]
        and "articles" in r["mofid"]
        or r["mofid"] == {"total": 0, "pages": 1, "page": 1}
    )


async def test_fund_detail_history_and_comparison(client, api):
    api[R + "fund/all"] = fixture("fund_all.json")
    api[R + "fundTrends/navTrend"] = {"data": [[739920, 100.0], [739921, 101.0]]}
    for p in ("etfBaseInfo", "etfBaseInfoAdditional", "etfOverViewData"):
        api[R + f"fundDetails/{p}"] = {"data": {}}
    api[R + "fundDetails/operationTrend"] = {"data": {}}
    r = await call(client, "br_fund", {"funds": ["14004856587"]})
    assert r["code"] == "14004856587" and r["etf"] is True
    r = await call(client, "br_fund", {"funds": ["هستی بخش آگاه"], "metric": "nav", "points": 1})
    assert r["points"] == [{"date": "2026-11-01", "nav": 101.0}]
    codes = [f["nationalCode"] for f in fixture("fund_all.json")["data"]][:2]
    fund_screener(api, "mutual", ["Name", "NationalCode", "FundTypeCode", "Nav"], [
        {"Name": n, "NationalCode": c, "FundTypeCode": "4", "Nav": i} for i, (n, c) in enumerate(zip("ab", codes, strict=False), 1)
    ])  # fmt: skip
    r = await call(client, "br_fund", {"funds": codes, "columns": ["Nav"]})
    assert [f["Nav"] for f in r["funds"]] == [1, 2]
    assert (await client.call_tool("br_fund", {"funds": codes, "metric": "nav"})).is_error


async def test_options_chain_and_one_contract(client, api):
    api[R + "optionScreener/baseInfo"] = fixture("option_baseinfo.json")
    api[R + "optionScreener/data"] = fixture("option_data.json")
    api[R + "optionDetails/baseInfo"] = {"data": {"optionIsin": "IRO9PNES0A01", "buyUrl": "x"}}
    api[R + "optionDetails/overviewData"] = {"data": {"tradingInfo": {"last": 1}}}
    r = await call(client, "br_options", {"underlying": "شپنا", "limit": 2})
    assert 1 <= len(r["options"]) <= 2
    r = await call(client, "br_options", {"option": "IRO9PNES0A01"})
    assert r["info"] == {"optionIsin": "IRO9PNES0A01"} and r["trading"] == {"last": 1}
    assert q(next(c for c in api.calls if c.url.path.endswith("overviewData"))) == {"isin": "IRO9PNES0A01"}


async def test_rates_live_prices_and_history(client, api):
    api[f"{G}/currencyGoldLive"] = fixture("currency_gold_live.json")
    api[f"{G}/currencyQuotes/history"] = fixture("currency_history.json")
    r = await call(client, "br_rates", {"kind": "currency", "query": "usd"})
    assert [i["code"] for i in r["items"]] == ["USD"]
    r = await call(client, "br_rates", {"kind": "currency", "query": "USD", "history_days": 3650, "limit": 3})
    assert r["code"] == "USD" and len(r["days"]) == 3
    assert (await client.call_tool("br_rates", {"history_days": 30})).is_error
    assert (await client.call_tool("br_rates", {"query": "US D", "history_days": 30})).is_error
