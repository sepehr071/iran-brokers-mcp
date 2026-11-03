import httpx
import pytest
from conftest import fixture
from legacy import AG, Legacy

from iran_brokers_mcp.agah import http
from iran_brokers_mcp.agah.util import day, norm

pytestmark = pytest.mark.anyio

R = "/backend/"
FOLD = "IRO1FOLD0001"


@pytest.fixture
async def client(client):
    return Legacy(client, AG)


def q(call: httpx.Request) -> dict[str, str]:
    return dict(call.url.params.multi_items())


def test_helpers():
    assert day(739920) == "2026-10-31"  # ordinals in series are date.toordinal()
    assert norm("فولاد ") == norm("فولاد") and norm("كاشي") == norm("کاشی")


async def test_request_headers_match_the_sites_frontend(api):
    api[R + "shakhes"] = {"data": []}
    api["/api/v1/contents"] = {"data": {"items": []}}
    api["/api/topic/list"] = {"item": []}
    await http.fetch(http.RESEARCH + "/shakhes", {"market": 0, "range": 0})
    h = api.calls[-1].headers
    assert h["accept"] == "*/*" and h["referer"] == "https://research.agah.com/" and "origin" not in h
    assert h["content-type"] == "application/json" and h["priority"] == "u=1, i"
    assert (h["sec-fetch-site"], h["sec-fetch-mode"], h["sec-fetch-dest"]) == ("same-origin", "cors", "empty")
    assert h["accept-language"].startswith("fa-IR")
    await http.fetch(http.PEDIA + "/contents", {"pageIndex": 0, "pageSize": 1})
    h = api.calls[-1].headers
    assert h["accept"] == "application/json, text/plain, */*" and h["origin"] == "https://pedia.agah.com"
    assert h["referer"] == "https://pedia.agah.com/" and h["sec-fetch-site"] == "same-site"
    await http.fetch(http.ACADEMY + "/topic/list")
    assert api.calls[-1].headers["origin"] == "https://academy.agah.com"


async def test_unexpected_host_is_refused(api):
    with pytest.raises(http.ApiError, match="unexpected host"):
        await http.fetch("https://example.com/x")


async def test_http_errors_become_messages(client, api):
    api[R + "stockDetails/baseInfo"] = lambda r: httpx.Response(400, json={"errors": {"isin": ["required"]}})
    api[R + "stock/all"] = fixture("stock_all.json")
    r = await client.call_tool("ag_stock", {"stock": FOLD})
    assert r.is_error and "HTTP 400" in r.content[0].text and "required" in r.content[0].text
    api[R + "shakhes"] = lambda r: httpx.Response(200, text="<html>checking</html>")
    r = await client.call_tool("ag_index_history", {})
    assert r.is_error and "non-JSON" in r.content[0].text
    api[R + "shakhes"] = lambda r: httpx.Response(500)
    r = await client.call_tool("ag_index_history", {})
    assert r.is_error and "server error" in r.content[0].text


async def test_search_matches_arabic_spelling_and_ranks_exact_first(client, api):
    api[R + "stock/all"] = fixture("stock_all.json")
    api[R + "fund/all"] = fixture("fund_all.json")
    api[R + "optionScreener/baseInfo"] = fixture("option_baseinfo.json")
    api[R + "optionScreener/data"] = fixture("option_data.json")
    api[R + "industry/all"] = {"data": []}
    r = (await client.call_tool("ag_search", {"query": "شپنا", "kind": "option"})).structured_content
    assert r["results"][0]["kind"] == "option" and r["results"][0]["id"] == "IRO9PNES0A01"  # found by underlying symbol
    r = (await client.call_tool("ag_search", {"query": "فولاد"})).structured_content
    assert r["results"][0] == {"kind": "stock", "id": FOLD, "name": "فولاد", "company": "فولاد مبارکه اصفهان"}
    r = (await client.call_tool("ag_search", {"query": "آگاس", "kind": "fund"})).structured_content
    assert r["results"][0]["id"] == "14004856587" and r["results"][0]["etf"] is True


async def test_stock_resolves_symbol_and_merges_three_calls(client, api):
    api[R + "stock/all"] = fixture("stock_all.json")
    api[R + "stockDetails/baseInfo"] = fixture("stock_baseinfo.json")
    api[R + "stockDetails/overview"] = fixture("stock_overview.json")
    api[R + "stockDetails/returns"] = fixture("stock_returns.json")
    r = (await client.call_tool("ag_stock", {"stock": "فولاد"})).structured_content
    assert r["isin"] == FOLD and r["company"] == "فولاد مبارکه اصفهان" and r["ratios"]["pToE"] > 0
    assert r["technical_today"]["label"] == "buy" and r["price_limits"][0] < r["price_limits"][1]
    assert "instrument" in r["returns_pct"]
    detail = [c for c in api.calls if "stockDetails" in c.url.path]
    assert len(detail) == 3 and all(q(c) == {"isin": FOLD} for c in detail)


async def test_stock_ambiguous_or_unknown_name_lists_candidates(client, api):
    api[R + "stock/all"] = fixture("stock_all.json")
    r = await client.call_tool("ag_stock", {"stock": "ا"})
    assert r.is_error and "شپنا" in r.content[0].text
    r = await client.call_tool("ag_stock", {"stock": "zzz"})
    assert r.is_error and "No stock" in r.content[0].text


async def test_stocks_rank_and_filter(client, api):
    api[R + "market/instrumentInfo"] = fixture("market_instrumentinfo.json")
    r = (await client.call_tool("ag_stocks", {"sort": "value", "limit": 2})).structured_content
    values = [s["value"] for s in r["stocks"]]
    assert values == sorted(values, reverse=True) and len(values) == 2 and r["matching"] == 4
    r = (
        await client.call_tool("ag_stocks", {"sort": "change", "ascending": True, "min_value": 10**13})
    ).structured_content
    assert all(s["value"] >= 10**13 for s in r["stocks"])


async def test_stock_history_is_oldest_first_without_candles(client, api):
    api[R + "stock/all"] = fixture("stock_all.json")
    api[R + "stockDetails/tradeHistorical"] = fixture("stock_history.json")
    r = (await client.call_tool("ag_stock_history", {"stock": FOLD, "days": 2})).structured_content
    dates = [d["dateOnly"] for d in r["days"]]
    assert len(dates) == 2 and dates == sorted(dates) and "todayCandles" not in r["days"][0]


async def test_screener_filters_sorts_and_maps_column_ids(client, api):
    api[R + "stockScreener/baseInfo"] = fixture("stock_screener_baseinfo.json")
    api[R + "stockScreener/data"] = fixture("stock_screener_data.json")
    r = (
        await client.call_tool(
            "ag_screener", {"kind": "stock", "columns": ["PToE", "Roe"], "max_values": {"PToE": 6}, "sort": "Roe"}
        )
    ).structured_content
    assert r["rows"] and all(row["PToE"] <= 6 for row in r["rows"])
    roes = [row["Roe"] for row in r["rows"]]
    assert roes == sorted(roes, reverse=True) and set(r["rows"][0]) == {"Symbol", "InstrumentIsin", "PToE", "Roe"}
    r = await client.call_tool("ag_screener", {"kind": "stock", "columns": ["Nope"]})
    assert r.is_error and "list_columns" in r.content[0].text
    r = (await client.call_tool("ag_screener", {"kind": "stock", "list_columns": True})).structured_content
    assert {"name": "PToE", "title": "P/E ttm"} in r["columns"]


async def test_fund_screener_adds_fund_type_label(client, api):
    api[R + "fundScreener/mutualBaseInfo"] = {
        "data": {
            "columns": [{"id": 1, "name": "Name", "title": "نام"}, {"id": 4, "name": "FundTypeCode", "title": "نوع"}]
        }
    }
    api[R + "fundScreener/mutualData"] = {"data": [{"1": "الف", "4": "4"}, {"1": "ب", "4": "6"}, {"1": "ج", "4": "17"}]}
    r = (
        await client.call_tool("ag_screener", {"kind": "mutual", "columns": ["Name"], "fund_type": "سایر"})
    ).structured_content
    assert r["rows"] == [{"Name": "ج", "FundType": "سایر"}]


async def test_fund_trend_decodes_dates_and_labels(client, api):
    api[R + "fund/all"] = fixture("fund_all.json")
    api[R + "fundTrends/bubbleTrend"] = fixture("fund_bubble.json")
    api[R + "fundTrends/tradeValue"] = fixture("fund_trade_value.json")
    r = (
        await client.call_tool("ag_fund_trend", {"fund_query": "هستی بخش آگاه", "metric": "bubble", "points": 2})
    ).structured_content
    assert len(r["points"]) == 2 and set(r["points"][0]) == {"date", "bubble_pct"}
    assert q(api.calls[-1]) == {"nationalCode": "14004856587"}
    r = (
        await client.call_tool("ag_fund_trend", {"fund_query": "14004856587", "metric": "trade_value"})
    ).structured_content
    assert set(r["points"][0]) == {"date", "value", "trades"}


async def test_options_filter_by_underlying_and_side(client, api):
    api[R + "optionScreener/baseInfo"] = fixture("option_baseinfo.json")
    api[R + "optionScreener/data"] = fixture("option_data.json")
    r = (
        await client.call_tool("ag_options", {"underlying": "شپنا", "side": "call", "sort": "days"})
    ).structured_content
    assert r["options"] and all(o["underlying"] == "شپنا" and o["side"] == "اختیار خرید" for o in r["options"])
    days = [o["days_left"] for o in r["options"]]
    assert days == sorted(days)


async def test_articles_search_and_browse_params(client, api):
    api["/api/v1/contents/search"] = fixture("pedia_list.json")
    api["/api/v1/contents"] = fixture("pedia_list.json")
    r = (await client.call_tool("ag_articles", {"query": "صندوق طلا", "page": 1, "page_size": 5})).structured_content
    assert q(api.calls[-1]) == {"searchText": "صندوق طلا", "pageIndex": "1", "pageSize": "5"}
    assert r["articles"][0]["url"].startswith("https://pedia.agah.com/article/") and r["total"] == 585
    await client.call_tool("ag_articles", {"category_id": "abc"})
    assert q(api.calls[-1])["categoryId"] == "abc" and api.calls[-1].url.path == "/api/v1/contents"


async def test_article_html_becomes_text(client, api):
    api["/api/v1/contents/return-on-equity"] = fixture("pedia_article.json")
    r = (await client.call_tool("ag_article", {"slug": "return-on-equity", "max_chars": 500})).structured_content
    assert "<" not in r["text"] and len(r["text"]) <= 500 and r["truncated"] is True and r["summary"]


async def test_no_content_204_is_empty_not_an_error(client, api):
    api["/api/v1/contents"] = lambda r: httpx.Response(204)
    r = (await client.call_tool("ag_articles", {"page": 99})).structured_content
    assert r == {}  # empty values are dropped below the top level


async def test_academy_adds_course_urls(client, api):
    api["/api/layout/searchterm-autocomplete"] = {
        "item": [{"title": "courses", "items": [{"id": 4, "name": "x", "slug": "how-invest-funds", "imagePath": "i"}]}]
    }
    r = (await client.call_tool("ag_academy", {"query": "صندوق"})).structured_content
    assert r["results"][0]["items"][0] == {
        "id": 4,
        "name": "x",
        "slug": "how-invest-funds",
        "url": "https://academy.agah.com/course/4/how-invest-funds",
    }
