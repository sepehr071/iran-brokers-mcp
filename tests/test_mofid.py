import time

import httpx
import pytest
from conftest import fixture
from legacy import MF, Legacy

from iran_brokers_mcp.mofid import http

pytestmark = pytest.mark.anyio

G = "/gateway"


@pytest.fixture
async def client(client):
    return Legacy(client, MF)


def q(call: httpx.Request) -> dict[str, str]:
    return dict(call.url.params.multi_items())


async def test_search_ranks_exact_first_folds_arabic_letters_and_filters_kind(client, api):
    api[f"{G}/stockTickers"] = fixture("tickers.json")
    r = (await client.call_tool("mf_search", {"query": "فملي"})).structured_content  # Arabic ye
    assert r["results"][0]["isin"] == "IRO1MSMI0001" and r["count"] == 1, r
    r = (await client.call_tool("mf_search", {"query": "IRT1AHRM"})).structured_content
    assert r["count"] == 0, r  # the default kind is stock
    r = (await client.call_tool("mf_search", {"query": "IRT1AHRM", "kind": "etf"})).structured_content
    assert r["results"][0]["symbol"] == "اهرم", r
    r = (await client.call_tool("mf_search", {"query": "اختیار", "kind": "option"})).structured_content
    assert r["count"] == 2, r
    assert len([c for c in api.calls if c.url.path.endswith("stockTickers")]) == 1  # cached for an hour


async def test_quote_joins_isins_and_converts_fractions_to_percent(client, api):
    api[f"{G}/symbolLatest"] = fixture("quotes.json")
    r = (await client.call_tool("mf_quote", {"isins": ["IRO1MSMI0001", "IRO1PNES0001"]})).structured_content
    assert q(api.calls[0])["isin"] == "IRO1MSMI0001,IRO1PNES0001"
    one = r["quotes"][0]
    assert one["symbol"] == "فملی" and one["last"] == 26850 and one["last_change_pct"] == -3.0
    assert one["returns_pct"]["1w"] == -2.68 and one["bid"] == 26850


async def test_symbol_combines_calls_and_survives_a_failed_section(client, api):
    api[f"{G}/stocksQuotes"] = fixture("stocks_quotes.json")
    api[f"{G}/stockInfo"] = fixture("stock_info.json")
    api[f"{G}/stockIndInst"] = fixture("ind_inst.json")
    api[f"{G}/overall/stock/IRO1MSMI0001"] = fixture("overall_stock.json")
    # stockEvaluation is not routed: 404 -> scores omitted
    r = (await client.call_tool("mf_symbol", {"isin": "IRO1MSMI0001"})).structured_content
    assert r["quote"]["symbol"] == "فملی" and r["quote"]["p2e"] == 12.95 and r["quote"]["eps"] == 2073
    assert r["profile"]["industry"] and r["financials"]["peTtm"] and r["performance"]
    assert r["individual_vs_institutional"]["individual_buyers"] > 0 and "scores" not in r
    overall = next(c for c in api.calls if "/overall/stock/" in c.url.path)
    assert q(overall)["timeFrame"] == "daily" and q(overall)["start_date"] == q(overall)["end_date"]


async def test_symbol_with_everything_failing_raises(client, api):
    r = await client.call_tool("mf_symbol", {"isin": "IRO1MSMI0001"})
    assert r.is_error


async def test_depth_orders_levels(client, api):
    api[f"{G}/stockBidAsk"] = fixture("bid_ask.json")
    api[f"{G}/stocksQuotes"] = fixture("stocks_quotes.json")
    api[f"{G}/stockInfo"] = fixture("stock_info.json")
    api[f"{G}/stockIndInst"] = fixture("ind_inst.json")
    api[f"{G}/overall/stock/IRO1MSMI0001"] = fixture("overall_stock.json")
    args = {"symbol": "IRO1MSMI0001", "order_book": True, "source": "mofid"}
    r = (await client.call_tool("br_stock", args)).structured_content["mofid"]["order_book"]
    assert q(next(c for c in api.calls if c.url.path.endswith("/stockBidAsk"))) == {"symbol": "IRO1MSMI0001"}
    assert [lv["rank"] for lv in r["levels"]] == [1, 2, 3, 4, 5] and r["levels"][0]["bid_price"] == 26850
    assert r["total_bid_volume"] == 8137482


async def test_history_converts_epoch_to_tehran_dates(client, api):
    api[f"{G}/quotes/history"] = fixture("history.json")
    r = (await client.call_tool("mf_history", {"isin": "IRO1MSMI0001", "days": 10, "limit": 2})).structured_content
    p = q(api.calls[0])
    assert p["resolution"] == "1D" and p["adjusted"] == "true" and int(p["to_time"]) - int(p["from_time"]) == 10 * 86400
    assert [c["time"] for c in r["candles"]] == ["2026-10-31", "2026-11-01"] and r["candles"][-1]["close"] == 26850
    assert r["count"] == 3
    api[f"{G}/quotes/history"] = {"s": "no_data"}
    r = (await client.call_tool("mf_history", {"isin": "IRO1MSMI0001"})).structured_content
    assert r["count"] == 0 and r["note"] == "no_data"


async def test_market_summary_and_index(client, api):
    api[f"{G}/marketSummary"] = fixture("market_summary.json")
    api[f"{G}/marketQueueCount"] = fixture("queues.json")
    r = (await client.call_tool("mf_market_summary")).structured_content
    assert q(api.calls[1]) == {"isin": "Stock"}
    overall = next(m for m in r["markets"] if m["isin"] == "IREXOVERALLINDEX")
    assert overall["name"] and isinstance(overall["change_pct"], float)
    assert set(r["queues"]) == {"askqueue", "bidqueue"}
    api[f"{G}/overall/indice/detail/IREXOVERALLINDEX"] = fixture("index_detail.json")
    api[f"{G}/overall/indice/financialSummary/IREXOVERALLINDEX"] = fixture("index_fin.json")
    api[f"{G}/marketPriceMeanLive"] = fixture("index_mean.json")
    r = (await client.call_tool("mf_index", {"members": 2})).structured_content
    assert r["isin"] == "IREXOVERALLINDEX" and r["member_count"] == 3 and len(r["members"]) == 2
    assert "peTtm" in r["ratios"] and "vs_last_trade_price" in r["intraday_mean_change_pct"]


async def test_market_top_routes_each_board_to_its_endpoint(client, api):
    api[f"{G}/marketTopValue"] = {"mostvalue": fixture("market_top.json")["mostvalue"]}
    api[f"{G}/marketTop"] = fixture("market_top.json")
    api[f"{G}/marketTopReturn"] = fixture("market_top_return.json")
    api[f"{G}/marketValues"] = fixture("market_values.json")
    r = (await client.call_tool("mf_market_top", {"board": "mostvalue", "limit": 2})).structured_content
    assert api.calls[-1].url.path.endswith("/marketTopValue") and q(api.calls[-1]) == {"top_count": "20"}
    assert r["rows"][0]["symbol"] == "فملی" and r["rows"][0]["traded_value"] and len(r["rows"]) == 2
    r = (await client.call_tool("mf_market_top", {"board": "netindividual", "limit": 1})).structured_content
    assert api.calls[-1].url.path.endswith("/marketTop") and r["count"] == 1
    r = (await client.call_tool("mf_market_top", {"board": "return1W_desc", "limit": 3})).structured_content
    assert api.calls[-1].url.path.endswith("/marketTopReturn") and not api.calls[-1].url.params
    assert r["rows"][0]["change_pct"] is not None
    r = (await client.call_tool("mf_market_top", {"board": "bidQueue", "limit": 1})).structured_content
    assert r["rows"][0]["bid_queue_value"] > 0
    assert (await client.call_tool("mf_market_top", {"board": "nope"})).is_error


async def test_etfs_filter_and_sort(client, api):
    api[f"{G}/marketEtf"] = fixture("etfs.json")
    r = (await client.call_tool("mf_etfs", {"type": "fixedincome", "sort": "change", "limit": 5})).structured_content
    changes = [f["change_pct"] for f in r["funds"]]
    assert len(changes) == 5 and changes == sorted(changes, reverse=True)
    assert all(f["type"] == "fixedincome" for f in r["funds"])
    r = (await client.call_tool("mf_etfs", {"query": "سافرون"})).structured_content
    assert r["funds"] and all("سافرون" in f["symbol"] for f in r["funds"])


async def test_mofid_funds_uses_the_marketing_site(client, api):
    api["/api/funds/"] = fixture("mofid_funds.json")
    r = (await client.call_tool("mf_mofid_funds")).structured_content
    call = api.calls[0]
    assert call.url.host == "www.emofid.com" and call.headers["referer"] == "https://www.emofid.com/fa/funds/"
    assert r["count"] == 2 and r["funds"][0]["asset_mix_pct"] and r["funds"][0]["risk"]


async def test_bonds_dedup_min_days_and_sort(client, api):
    b = fixture("bonds.json")
    dup = dict(b["TreasuryBills"][0], quotes=[{"closing": 900000, "closingYTM": 30.5, "volume": 1}], daysToMaturity=400)
    b["TreasuryBills"] = [dup]
    b["NonCouponBonds"] = [dup, dict(dup, isin="X2", symbolFa="y", daysToMaturity=5)]
    api[f"{G}/bonds"] = b
    r = (await client.call_tool("mf_bonds", {})).structured_content
    isins = [x["isin"] for x in r["bonds"]]
    assert isins.count(dup["isin"]) == 1 and "X2" not in isins  # deduped; 5-day bill hidden
    assert r["bonds"][0]["ytm"] == 30.5 or r["bonds"][0]["ytm"] >= r["bonds"][-1]["ytm"]
    r = (await client.call_tool("mf_bonds", {"min_days": 0, "query": "y"})).structured_content
    assert [x["isin"] for x in r["bonds"]] == ["X2"]


async def test_industries_sort_and_percent(client, api):
    api[f"{G}/industry/overview"] = fixture("industry_overview.json")
    r = (await client.call_tool("mf_industries", {"sort": "return_1y", "limit": 3})).structured_content
    ys = [i["returns_pct"]["1y"] for i in r["industries"]]
    assert ys == sorted(ys, reverse=True) and r["industries"][0]["industry"]


async def test_fx_gold_and_rate_history(client, api):
    api[f"{G}/currencyGoldLive"] = fixture("currency_gold_live.json")
    api[f"{G}/commodityLive"] = fixture("commodity_live.json")
    r = (await client.call_tool("mf_fx_gold", {"kind": "currency", "query": "usd"})).structured_content
    assert [i["code"] for i in r["items"]] == ["USD"] and r["items"][0]["price"] > 1_000_000
    r = (await client.call_tool("mf_fx_gold", {"kind": "gold_coin"})).structured_content
    assert r["items"][0]["code"] == "IMCOIN" and "low_30d" in r["items"][0]
    r = (await client.call_tool("mf_fx_gold", {"kind": "commodity"})).structured_content
    assert r["items"][0]["code"] == "WTI" and "1d" in r["items"][0]["change_pct"]
    hist = fixture("currency_history.json")
    api[f"{G}/currencyQuotes/history"] = hist
    r = (await client.call_tool("mf_rate_history", {"code": "USD", "days": 3650, "limit": 5})).structured_content
    assert q(api.calls[-1]) == {"abbreviation": "USD"} and r["count"] == 12 and len(r["days"]) == 5
    assert r["days"][-1]["date"] == hist[-1]["dateTimeLocal"]
    api[f"{G}/commodityHistory"] = [
        {
            "abbr": "Gold18",
            "history": [{"dateTimeLocal": "2026-11-01", "close": 3}, {"dateTimeLocal": "2026-10-31", "close": 2}],
        }
    ]
    r = (
        await client.call_tool("mf_rate_history", {"code": "Gold18", "kind": "gold_coin", "days": 3650})
    ).structured_content
    assert q(api.calls[-1]) == {"abbreviations": "Gold18"} and [d["close"] for d in r["days"]] == [2, 3]


async def test_news_kinds(client, api):
    api[f"{G}/stockMessages"] = fixture("notices.json")
    api[f"{G}/stockFilings"] = fixture("filings.json")
    api[f"{G}/telegramMessages"] = fixture("telegram.json")
    api[f"{G}/mofidAnalysisMessages"] = fixture("research.json")
    notices = fixture("notices.json")
    sym = notices[0]["tickerfa"]
    r = (await client.call_tool("mf_news", {"kind": "notices", "symbol": sym, "chars": 40})).structured_content
    assert q(api.calls[0]) == {"count": "30"} and r["count"] >= 1 and all(len(i["text"]) <= 43 for i in r["items"])
    r = (await client.call_tool("mf_news", {"kind": "filings"})).structured_content
    assert r["items"][0]["pdf_url"].startswith("https://")
    r = (await client.call_tool("mf_news", {"kind": "telegram"})).structured_content
    assert q(api.calls[-1]) == {"channelId": "0", "count": "30"} and r["items"][0]["text"]
    r = (await client.call_tool("mf_news", {"kind": "research", "count": 1})).structured_content
    assert r["count"] == 1 and r["items"][0]["pdf_url"]


async def test_announcements_resolves_company_and_type_keys(client, api):
    api[f"{G}/filing/companies"] = fixture("companies.json")
    api[f"{G}/filing/types"] = fixture("filing_types.json")
    api[f"{G}/filing/"] = fixture("announcements.json")
    r = (
        await client.call_tool(
            "mf_announcements",
            {
                "symbol": "فملي",
                "report_type": fixture("filing_types.json")[1]["nameFa"],
                "from_date": "2026-10-30",
                "to_date": "2026-11-01",
                "limit": 3,
            },
        )
    ).structured_content
    call = next(c for c in api.calls if c.url.path == f"{G}/filing/")
    p = q(call)
    assert (
        p["companyKey"] == "1061" and p["typeKey"] and p["publishDate"] == "[20261030,20261101]" and p["limit"] == "3"
    )
    assert "%5B" in str(call.url)  # brackets are percent-encoded like the web app
    assert r["count"] == 3 and r["announcements"][0]["title"] and r["announcements"][0]["published"]
    await client.call_tool("mf_announcements", {"limit": 5, "offset": 10})
    assert q(api.calls[-1])["offset"] == "2"  # the gateway pages by index, not by rows skipped
    bad = await client.call_tool("mf_announcements", {"symbol": "ناموجود"})
    assert bad.is_error


async def test_scores_three_modes(client, api):
    api[f"{G}/stockEvaluation"] = fixture("evaluation_one.json")
    api[f"{G}/evaluation/evaluationTotal"] = fixture("evaluation_total.json")
    api[f"{G}/evaluation/evaluationFundamental/Profitability"] = fixture("evaluation_section.json")
    r = (await client.call_tool("mf_scores", {"isins": ["IRO1MSMI0001"]})).structured_content
    assert q(api.calls[0]) == {"tickers": "IRO1MSMI0001"} and r["scores"][0]["technical"] == "Neutral"
    r = (await client.call_tool("mf_scores", {"section": "Profitability"})).structured_content
    totals = [s["total_score"] for s in r["scores"]]
    assert totals == sorted(totals, reverse=True)
    r = (await client.call_tool("mf_scores", {"min_fundamental": 7})).structured_content
    assert all(s["fundamental_of_10"] >= 7 for s in r["scores"])


async def test_learn_search_post_and_categories(client, api):
    posts = [
        {
            "id": 7,
            "date": "2026-10-31T09:40:40",
            "link": "https://learning.emofid.com/x/",
            "title": {"rendered": "A &amp; B"},
            "excerpt": {"rendered": "<p>hello <b>world</b></p>"},
        }
    ]
    api["/wp-json/wp/v2/posts"] = lambda r: httpx.Response(
        200, json=posts, headers={"X-WP-Total": "41", "X-WP-TotalPages": "5"}
    )
    api["/wp-json/wp/v2/posts/7"] = {
        "id": 7,
        "date": "2026-10-31T09:40:40",
        "link": "l",
        "title": {"rendered": "T"},
        "content": {"rendered": "<p>" + "word " * 500 + "</p>"},
    }
    api["/wp-json/wp/v2/categories"] = [
        {"id": 1, "name": "A &amp; B", "count": 3},
        {"id": 2, "name": "empty", "count": 0},
    ]
    r = (await client.call_tool("mf_learn", {"query": "gold", "category_id": 9, "per_page": 1})).structured_content
    assert q(api.calls[0])["search"] == "gold" and q(api.calls[0])["categories"] == "9"
    assert (
        (r["total"], r["pages"]) == (41, 5)
        and r["articles"][0]["title"] == "A & B"
        and r["articles"][0]["summary"] == "hello world"
    )
    assert (
        api.calls[0].headers["referer"] == "https://learning.emofid.com/" and "accept-info" not in api.calls[0].headers
    )
    one = (await client.call_tool("mf_learn", {"post_id": 7, "chars": 500})).structured_content
    assert one["title"] == "T" and one["text"].endswith("...") and len(one["text"]) <= 503
    r = (await client.call_tool("mf_learn_categories")).structured_content
    assert r["categories"] == [{"id": 1, "name": "A & B", "articles": 3}]


async def test_request_headers_match_the_sites_frontend(api):
    api[f"{G}/marketSummary"] = []
    await http.fetch("/marketSummary")
    h = api.calls[-1].headers
    assert h["accept"] == "application/json, text/plain, */*" and h["referer"] == "https://bv.emofid.com/market"
    assert "origin" not in h and (h["sec-fetch-site"], h["sec-fetch-mode"], h["sec-fetch-dest"]) == (
        "same-origin",
        "cors",
        "empty",
    )
    assert h["priority"] == "u=1, i" and h["accept-language"].startswith("fa-IR")
    # accept-info: server epoch in ms * 4564567, then a fixed suffix (bundle getInfo()).
    left, suffix = h["accept-info"].split(".", 1)
    assert suffix == "3094776426" and abs(int(left) // 4564567 - time.time() * 1000) < 5000


async def test_clock_skew_401_resyncs_with_epoch_and_retries(api):
    skew = 3_600_000  # the server clock is one hour ahead
    seen = []

    def gateway(request):
        seen.append(request.headers["accept-info"])
        left = int(request.headers["accept-info"].split(".")[0]) // 4564567
        ok = abs(left - (time.time() * 1000 + skew)) < 5000
        return httpx.Response(200, json={"ok": True}) if ok else httpx.Response(401, text="Unauthorized")

    api[f"{G}/marketSummary"] = gateway
    api[f"{G}/epoch"] = lambda r: httpx.Response(200, text=str(int(time.time() * 1000 + skew)))
    assert await http.fetch("/marketSummary") == {"ok": True}
    assert len(seen) == 2 and seen[0] != seen[1]


async def test_401_without_a_fix_is_a_clear_error(client, api):
    api[f"{G}/marketSummary"] = lambda r: httpx.Response(401, text="Unauthorized")
    api[f"{G}/epoch"] = lambda r: httpx.Response(200, text=str(int(time.time() * 1000)))
    r = await client.call_tool("mf_market_summary")
    assert r.is_error and "401" in r.content[0].text


async def test_400_shows_the_gateway_message(api):
    api[f"{G}/symbolLatest"] = lambda r: httpx.Response(
        400, json={"message": {"message": ["isin should not be empty"]}}
    )
    with pytest.raises(http.ApiError, match="isin should not be empty"):
        await http.fetch("/symbolLatest")


async def test_cache_and_retry(api):
    n = {"c": 0}

    def flaky(request):
        n["c"] += 1
        return httpx.Response(503) if n["c"] == 1 else httpx.Response(200, json=[1])

    api[f"{G}/indexQuotes"] = flaky
    assert await http.fetch("/indexQuotes") == [1] and n["c"] == 2
    assert await http.fetch("/indexQuotes") == [1] and n["c"] == 2  # served from cache
