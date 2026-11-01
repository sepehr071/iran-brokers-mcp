import pytest
from legacy import AG, Legacy

pytestmark = [pytest.mark.live, pytest.mark.anyio]


@pytest.fixture
async def client(client):
    return Legacy(client, AG)


async def call(client, name, args=None):
    r = await client.call_tool(name, args or {})
    assert not r.is_error, r.content[0].text
    return r.structured_content


async def iran_only(client, name, args):
    """Pedia and academy answer only from an Iranian network: skip elsewhere."""
    r = await client.call_tool(name, args)
    if r.is_error and "only from Iran" in r.content[0].text:
        pytest.skip("pediaapi/academyapi.agah.com unreachable from this network")
    assert not r.is_error, r.content[0].text
    return r.structured_content


async def test_live_search_and_stock(client):
    r = await call(client, "ag_search", {"query": "فولاد", "kind": "stock"})
    assert r["results"][0]["id"] == "IRO1FOLD0001"
    s = await call(client, "ag_stock", {"stock": "فولاد"})
    assert s["isin"] == "IRO1FOLD0001" and s["ratios"]["pToE"] > 0 and s["last"] > 0
    h = await call(client, "ag_stock_history", {"stock": "IRO1FOLD0001", "days": 5})
    assert 1 <= len(h["days"]) <= 5
    t = await call(client, "ag_technical", {"target": "فولاد"})
    assert t["summary"]["label"]


async def test_live_market(client):
    p = await call(client, "ag_market_pulse")
    assert p["total_index"]["last"] > 100_000 and p["prices"]["currencies"]
    s = await call(client, "ag_stocks", {"sort": "change", "limit": 5})
    assert s["matching"] > 300 and len(s["stocks"]) == 5
    i = await call(client, "ag_industries")
    assert len(i["industries"]) > 20
    one = await call(client, "ag_industries", {"industry": i["industries"][0]["isin"]})
    assert one["stocks"]
    assert (await call(client, "ag_index_history", {"market": 0}))["points"]
    assert (await call(client, "ag_market_overview"))["breadth"]["total_trade_value"] > 0


async def test_live_screener_funds_options(client):
    r = await call(client, "ag_screener", {"kind": "stock", "max_values": {"PToE": 8}, "sort": "Roe", "limit": 3})
    assert r["rows"] and all(x["PToE"] <= 8 for x in r["rows"])
    f = await call(
        client, "ag_screener", {"kind": "mutual", "fund_type": "درآمد ثابت", "sort": "ReturnOf365DaysAgo", "limit": 3}
    )
    assert f["rows"] and all(x["FundType"] == "درآمد ثابت" for x in f["rows"])
    fund = await call(client, "ag_fund", {"fund_query": "هستی بخش آگاه"})
    assert fund["code"] == "14004856587" and fund["etf"] is True
    tr = await call(client, "ag_fund_trend", {"fund_query": "14004856587", "metric": "nav", "points": 3})
    assert len(tr["points"]) == 3 and tr["points"][0]["nav"] > 0
    cmp_ = await call(client, "ag_fund_compare", {"funds": ["14004856587", "14010078924"]})
    assert len(cmp_["funds"]) == 2
    o = await call(client, "ag_options", {"underlying": "شپنا", "limit": 3})
    assert o["options"]
    one = await call(client, "ag_option", {"option": o["options"][0]["isin"]})
    assert one["info"]["optionIsin"] == o["options"][0]["isin"]


async def test_live_stock_events(client):
    e = await call(client, "ag_stock_events", {"stock": "فولاد"})
    assert e["cash_dividends"] and e["holders"]


async def test_live_education(client):
    r = await iran_only(client, "ag_articles", {"query": "صندوق طلا", "page_size": 3})
    assert r["articles"]
    a = await iran_only(client, "ag_article", {"slug": r["articles"][0]["slug"], "max_chars": 800})
    assert a["text"] and "<" not in a["text"]
    assert (await iran_only(client, "ag_academy", {}))["topics"]
    assert (await iran_only(client, "ag_academy", {"query": "صندوق"}))["results"]
