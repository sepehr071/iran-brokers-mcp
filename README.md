<!-- mcp-name: io.github.sepehr071/iran-brokers-mcp -->

# iran-brokers-mcp: Mofid and Agah Iran broker market data MCP server for AI agents

**iran-brokers-mcp is an unofficial, read-only MCP server that gives AI agents the public market data of two Iranian brokers, Mofid Securities (emofid.com, Bourse View) and Agah (agah.com, Agah Research), in one server: Tehran stock exchange quotes, candles, ETFs and funds, options, bonds, gold and currency prices, screeners, Codal announcements and education articles.**<br>
Its 22 tools replace the 38 tools of the separate mofid-mcp and agah-mcp servers. Where both brokers offer the same data (symbol search, stock detail, price history, market overview, industries, index history, ETF list, education articles) one tool takes `source` (`mofid`, `agah` or `all`) and answers per source; the rest keep a single source.<br>
No API key or broker account is needed. It works in Claude, Cursor, Copilot and any other MCP client.

[For AI agents](#for-ai-agents) · [Quick start](#quick-start) · [Tools](#tools) · [Limits](#limits) · [FAQ](#faq)

---

## For AI agents

- **What:** unofficial MCP server (model context protocol) for Mofid and Agah: symbol search, stock cards, OHLC candles and daily history, market snapshot, 36 Mofid rankings, Agah market watch and screener, ETFs with price vs NAV, fund detail/history/comparison, options chain and contract, bonds and T-bills, currency/coin/gold/commodity prices and history, Codal filings, valuation scores, technical tally, education articles and courses.
- **Install:** `uvx iran-brokers-mcp` or `claude mcp add iran-brokers -- uvx iran-brokers-mcp`. Transport: stdio. Needs [uv](https://docs.astral.sh/uv/getting-started/installation/) (Python 3.10+).
- **Auth:** none. Optional `IRAN_BROKERS_MCP_PROXY` (one HTTP proxy URL for every request, or a comma-separated list); system proxy settings are ignored.
- **Access:** read-only market data. It cannot log in, read a portfolio, cash or orders, place, edit or cancel orders, or pay.
- **Tools:** 22, all prefixed `br_`. Typical sequences: `br_search` -> `br_stock` / `br_history`; `br_market` -> `br_market_top` or `br_stocks`; `br_etfs` -> `br_stock` or `br_fund`; `br_screener`; `br_rates`; `br_announcements`; `br_learn` -> `br_learn(ref=...)`.
- **`source` argument:** each source answers in its own block (`{"mofid": ..., "agah": ...}`). With `all`, a source that fails shows `{"error": ...}` and the other still answers. Blocks are never blended into one row, because the fields differ.
- **Use when:** the user asks for a stock, fund, option, index, gold or dollar price on the Iranian market, a ranking or screen, a chart, a company's Codal filings, or how something in the bourse works.
- **Do not use when:** the user wants to trade, see their own portfolio, or needs real-time tick data (everything here is a polled snapshot).
- **Output:** compact JSON. Prices are Rial (divide by 10 for Toman). Text Persian.

## Quick start

```bash
claude mcp add iran-brokers -- uvx iran-brokers-mcp
```

Other clients: command `uvx`, args `["iran-brokers-mcp"]`.

## Tools

Merged tools (`source`: `mofid`, `agah` or `all`; the default is in brackets):

| Tool | What it does |
|---|---|
| `br_search` | Find the id of a stock, fund, option or index by symbol or name [all]. |
| `br_stock` | One stock or ETF in detail: Mofid price card (P/E, EPS, NAV, flows, scores, optional 5-level order book) and Agah card (ratios, returns, flows, order book) [all]. |
| `br_history` | Mofid OHLC candles (1 minute to weekly, any ISIN) or Agah daily rows with buyer/seller flows [mofid]. |
| `br_market` | Today's indices, value, queues (Mofid) and total index, money flow, movers, dollar/coin/gold/oil prices (Agah), optional Agah breadth [all]. |
| `br_industries` | Industry ranking: Mofid valuation ratios and returns, Agah change, money flow, one industry with its stocks [all]. |
| `br_index` | Index card (Mofid) and daily index history (both) [all]. |
| `br_etfs` | ETFs with price, NAV and gap/bubble: Mofid 375 ETFs, Agah 331 listed ETFs with NAV returns [mofid]. |
| `br_learn` | Search and read Mofid blog and Agah Pedia articles, list Mofid categories, search Agah Academy courses [all]. |

Mofid only: `br_quotes` (up to 30 live quotes), `br_market_top` (36 rankings), `br_bonds` (bonds, sukuk, T-bills with yield), `br_rates` (currency, crypto, coin, gold, commodity prices and history), `br_news` (notices, Codal letters, Telegram, research PDFs), `br_announcements` (Codal filings by company, type, date), `br_scores` (theoretical price, technical and fundamental scores), `br_mofid_funds` (Mofid-branded fund catalogue).

Agah only: `br_stocks` (market watch of about 750 stocks), `br_screener` (filter stocks, ETFs or all funds on any column), `br_fund` (fund detail, NAV/bubble history, comparison), `br_options` (chain, or one contract), `br_technical` (indicator tally for a stock or industry), `br_stock_events` (dividends, capital changes, notices, holders).

Old tool to new tool: `mf_search` and `ag_search` -> `br_search`; `mf_symbol`, `mf_depth`, `ag_stock` -> `br_stock`; `mf_history`, `ag_stock_history` -> `br_history`; `mf_market_summary`, `ag_market_pulse`, `ag_market_overview` -> `br_market`; `mf_industries`, `ag_industries` -> `br_industries`; `mf_index`, `ag_index_history` -> `br_index`; `mf_etfs` -> `br_etfs`; `mf_learn`, `mf_learn_categories`, `ag_articles`, `ag_article`, `ag_academy` -> `br_learn`; `mf_fx_gold`, `mf_rate_history` -> `br_rates`; `ag_fund`, `ag_fund_trend`, `ag_fund_compare` -> `br_fund`; `ag_options`, `ag_option` -> `br_options`; the other `mf_*` and `ag_*` tools keep their name with the `br_` prefix (`mf_quote` -> `br_quotes`).

## Limits

- **No portfolio, orders or cash.** Those need a broker login (EasyTrader, AsaTrader); nothing here logs in or can place an order.
- **Agah Pedia and Academy answer only from an Iranian network** (`pediaapi.agah.com`, `academyapi.agah.com` time out from abroad). The error says so; the Mofid side of `br_learn` still answers. Do not put a foreign proxy in front of them.
- **Mofid depends on the `accept-info` header** of the Bourse View gateway (derived from the server clock; the client resyncs on a 401). If the site changes that check, Mofid tools fail with an HTTP 401 error and the Agah side keeps working.
- **Data is polled snapshots**, cached 30 to 60 seconds, delayed or unaudited; the market trades Saturday to Wednesday about 09:00-12:30 Tehran time. Not investment advice.
- The two sources use different ids for funds (Mofid ISIN, Agah 11-digit national code) and different fields, so merged tools show them side by side.

## FAQ

**Is it allowed?** It calls the same public JSON the websites use, two requests at a time per broker and with caching, and each client sends the headers its site's web app sends (Chrome TLS fingerprint, Referer, Accept, Mofid's `accept-info`).

**Does it need the internet from Iran?** Mofid and Agah Research answer from inside and outside Iran. Set `IRAN_BROKERS_MCP_PROXY` to route through one.

**What about tsetmc-mcp and codal-mcp?** They read the exchange and Codal directly; this server reads the brokers' own views of the same market (rankings, screeners, NAV gaps, education).

Author: Sepehr Radmard (سپهر رادمرد), github.com/sepehr071, linkedin.com/in/sepehr-radmard. MIT licensed. Not affiliated with Mofid Securities or Agah.
