import pytest

pytestmark = [pytest.mark.live, pytest.mark.anyio]


async def call(client, name, args=None):
    r = await client.call_tool(name, args or {})
    assert not r.is_error, r.content[0].text
    return r.structured_content


async def test_live_search_stock_history_both_sources(client):
    r = await call(client, "br_search", {"query": "فولاد"})
    assert r["mofid"]["results"][0]["symbol"] == "فولاد" and r["agah"]["results"][0]["name"] == "فولاد", r
    s = await call(client, "br_stock", {"symbol": "فولاد"})  # a symbol: Mofid resolves the ISIN itself
    assert s["mofid"]["quote"]["symbol"] == "فولاد" and s["agah"]["ratios"]["pToE"] > 0, s
    h = await call(client, "br_history", {"symbol": "فولاد", "days": 20, "limit": 5, "source": "all"})
    assert h["mofid"]["count"] > 3 and h["agah"]["days"], h
    e = await call(client, "br_stock", {"symbol": "اهرم", "source": "mofid", "order_book": True})
    assert e["mofid"]["order_book"]["levels"], e


async def test_live_market_industries_index_etfs(client):
    m = await call(client, "br_market")
    assert m["mofid"]["markets"] and m["agah"]["total_index"], m
    i = await call(client, "br_industries", {"limit": 3})
    assert i["mofid"]["industries"] and i["agah"]["industries"], i
    x = await call(client, "br_index", {"days": 30})
    assert x["mofid"]["history"]["candles"] and x["agah"]["points"] and x["mofid"]["member_count"] > 300, x
    f = await call(client, "br_etfs", {"type": "fixedincome", "source": "all", "limit": 3})
    assert f["mofid"]["funds"] and f["agah"]["rows"], f
    g = await call(client, "br_etfs", {"type": "goldetf", "sort": "nav_gap", "source": "all", "limit": 3})
    assert g["mofid"]["funds"] and g["agah"]["rows"], g


async def test_live_learn_keeps_mofid_when_pedia_is_unreachable(client):
    r = await call(client, "br_learn", {"query": "صندوق طلا", "per_page": 3})
    assert r["mofid"]["articles"], r
    agah = r["agah"]
    assert agah.get("articles") or "only from Iran" in agah.get("error", ""), agah
    if "articles" in agah:  # on the Iranian line the Pedia article reads too
        a = await call(client, "br_learn", {"ref": agah["articles"][0]["slug"], "max_chars": 600})
        assert a["agah"]["text"]


async def test_live_agah_only_tools_through_the_merged_names(client):
    f = await call(client, "br_fund", {"funds": ["هستی بخش آگاه"]})
    assert f["code"] == "14004856587", f
    o = await call(client, "br_options", {"underlying": "شپنا", "limit": 2})
    assert o["options"] and (await call(client, "br_options", {"option": o["options"][0]["isin"]}))["info"]
    r = await call(client, "br_rates", {"kind": "currency", "query": "USD", "history_days": 30})
    assert r["days"], r
