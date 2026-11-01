import pytest
from legacy import MF, Legacy

pytestmark = [pytest.mark.live, pytest.mark.anyio]


@pytest.fixture
async def client(client):
    return Legacy(client, MF)


MSMI = "IRO1MSMI0001"  # فملی


async def test_live_search_quote_symbol(client):
    r = (await client.call_tool("mf_search", {"query": "فملی"})).structured_content
    assert r["results"][0]["isin"] == MSMI, r
    r = (await client.call_tool("mf_search", {"query": "اهرم", "kind": "etf"})).structured_content
    assert any(x["isin"] == "IRT1AHRM0001" for x in r["results"]), r
    r = (await client.call_tool("mf_quote", {"isins": [MSMI, "IREXOVERALLINDEX"]})).structured_content
    assert {q["isin"] for q in r["quotes"]} == {MSMI, "IREXOVERALLINDEX"}, r
    assert all(q["last"] and "1w" in q["returns_pct"] for q in r["quotes"]), r
    r = (await client.call_tool("mf_symbol", {"isin": MSMI})).structured_content
    assert r["quote"]["symbol"] == "فملی" and r["profile"]["industry"] and r["financials"], r


async def test_live_depth_and_history(client):
    r = (
        await client.call_tool("br_stock", {"symbol": MSMI, "order_book": True, "source": "mofid"})
    ).structured_content["mofid"]["order_book"]
    assert 1 <= len(r["levels"]) <= 5 and r["levels"][0]["rank"] == 1, r
    r = (await client.call_tool("mf_history", {"isin": MSMI, "days": 30})).structured_content
    assert r["count"] > 5 and r["candles"][-1]["close"] > 0, r
    r = (
        await client.call_tool("mf_history", {"isin": "IREXOVERALLINDEX", "resolution": "5", "days": 3})
    ).structured_content
    assert r["count"] > 10, r


async def test_live_market(client):
    r = (await client.call_tool("mf_market_summary")).structured_content
    assert any(m["isin"] == "IREXOVERALLINDEX" for m in r["markets"]), r
    r = (await client.call_tool("mf_market_top", {"board": "mostvalue", "limit": 5})).structured_content
    assert r["count"] == 5 and r["rows"][0]["isin"], r
    r = (await client.call_tool("mf_market_top", {"board": "return1W_desc", "limit": 3})).structured_content
    assert r["count"] == 3 and r["rows"][0]["symbol"], r
    r = (await client.call_tool("mf_index", {"members": 3})).structured_content
    assert r["member_count"] > 300 and len(r["members"]) == 3 and r["ratios"], r
    r = (await client.call_tool("mf_industries", {"limit": 5})).structured_content
    assert r["count"] > 40 and r["industries"][0]["market_cap"], r


async def test_live_funds_bonds_rates(client):
    r = (await client.call_tool("mf_etfs", {"type": "goldetf", "limit": 5})).structured_content
    assert r["count"] > 20 and r["funds"][0]["isin"], r
    r = (await client.call_tool("mf_mofid_funds")).structured_content
    assert r["count"] >= 15 and r["funds"][0]["asset_mix_pct"], r
    r = (await client.call_tool("mf_bonds", {"limit": 5})).structured_content
    assert r["count"] > 20 and r["bonds"][0]["ytm"], r
    r = (await client.call_tool("mf_fx_gold", {"kind": "currency", "query": "USD"})).structured_content
    assert r["items"][0]["code"] == "USD" and r["items"][0]["price"] > 100_000, r
    r = (await client.call_tool("mf_fx_gold", {"kind": "gold_coin"})).structured_content
    assert any(i["code"] == "Gold18" for i in r["items"]), r
    r = (await client.call_tool("mf_fx_gold", {"kind": "commodity", "query": "WTI"})).structured_content
    assert r["items"][0]["price"] > 0, r
    for kind, code in [("currency", "USD"), ("gold_coin", "Gold18"), ("commodity", "WTI")]:
        r = (await client.call_tool("mf_rate_history", {"code": code, "kind": kind, "days": 30})).structured_content
        assert r["count"] > 5 and r["days"][-1]["close"] > 0, r


async def test_live_news_announcements_scores(client):
    for kind in ("notices", "filings", "telegram", "research"):
        r = (await client.call_tool("mf_news", {"kind": kind, "count": 5})).structured_content
        assert r["count"] > 0, (kind, r)
    r = (await client.call_tool("mf_announcements", {"limit": 5, "from_date": "2026-10-25"})).structured_content
    assert r["count"] == 5 and all(a["title"] and a["published"] for a in r["announcements"]), r
    r = (
        await client.call_tool("mf_announcements", {"symbol": "فملی", "from_date": "2025-01-01", "limit": 5})
    ).structured_content
    assert r["announcements"] and all(a["symbol"] == "فملی" for a in r["announcements"]), r
    r = (await client.call_tool("mf_scores", {"isins": [MSMI]})).structured_content
    assert r["scores"][0]["technical"], r
    r = (await client.call_tool("mf_scores", {"section": "Profitability", "limit": 3})).structured_content
    assert r["count"] > 100 and r["scores"][0]["total_score"] is not None, r
    r = (await client.call_tool("mf_scores", {"limit": 3})).structured_content
    assert r["scores"][0]["fundamental_of_10"] >= r["scores"][-1]["fundamental_of_10"], r


async def test_live_learn(client):
    r = (await client.call_tool("mf_learn", {"query": "صندوق طلا", "per_page": 3})).structured_content
    assert r["total"] > 0 and r["articles"][0]["url"], r
    one = (await client.call_tool("mf_learn", {"post_id": r["articles"][0]["id"], "chars": 500})).structured_content
    assert one["text"] and one["title"], one
    r = (await client.call_tool("mf_learn_categories")).structured_content
    assert r["count"] > 10, r
