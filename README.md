# stock-screener

Personal daily stock screener. GitHub Actions runs intraday and EOD data
pipelines, Streamlit Community Cloud serves the dashboard, TradingView shows the
charts. Trading is still manual.

## Layout

```
.github/workflows/   GitHub Actions cron jobs (tw_run.yml, us_run.yml)
docs/                AI-friendly architecture, data-contract, and operations notes
screener/            Python module: fetch / indicators / score / io / run
data/
  watchlist.csv      Hand-edited list of symbols to screen
  sector_map.csv     Objective TWSE/TPEx/yfinance sector classifications
  latest_signals.json  Output written by Actions, read by Streamlit
  analyst_target_events.jsonl  US analyst target raise history event log
  tw_target_events.jsonl  Manual TW analyst target history event log
streamlit_app.py     Dashboard
tests/               Unit tests for indicators + scoring
```

## Scoring

Rules use weighted points. Missing data skips the affected rule. TW and US
technical weights switch by market regime.

| Rule | Default | TW bear/crash | TW bear/downtrend | TW range | TW bull | US bear/crash | US range | US bull |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 今日站上全均線 | 3.0 | 1.5 | 1.5 | 4.5 | 4.5 | 1.5 | 3.0 | 4.5 |
| 20日收盤新高 | 1.5 | 2.25 | 2.25 | 0.75 | 0.75 | 2.25 | 1.5 | 0.75 |
| 短線趨勢確認 (close > MA5 and MA5 > MA20) | 1.5 | 1.5 | 1.5 | 2.25 | 0.75 | 0.75 | 0.75 | 1.5 |
| 放量上漲 (vol > 1.2x and up day) | 1.5 | 1.5 | 1.5 | 1.5 | 2.25 | 0.75 | 2.25 | 0.75 |
| OBV 5d > OBV 20d | 1.0 | 0.5 | 0.5 | 0.5 | 0.5 | 0.5 | 0.5 | 1.0 |
| 相對強度 20日 > 大盤 | 2.0 | 1.0 | 1.0 | 1.0 | 3.0 | 1.0 | 1.0 | 3.0 |
| MACD 今日上穿 / 位於 signal 上方 | 1.5 / 1.0 | 0.75 / 0.5 | 0.75 / 0.5 | 1.5 / 1.0 | 1.5 / 1.0 | 0.75 / 0.5 | 0.75 / 0.5 | 0.75 / 0.5 |
| analyst target raised within 7 days and target ≥ current price +10% | 2.0 | 1.0 | 1.0 | 2.0 | 2.0 | 2.0 | 2.0 | 2.0 |
| 強勢板塊延續 | 1.5 | 0.75 | 0.75 | 1.5 | 1.5 | 1.5 | 1.5 | 1.5 |
| 投信買進第一天 / 連續買超 ≥ 3 日 | 2.0 / 1.5 | 1.0 / 0.75 | 1.0 / 0.75 | 2.0 / 1.5 | 2.0 / 1.5 | — | — | — |
| 外資大買 (>5% volume or 3-day streak) | 1.0 | 0.5 | 0.5 | 1.0 | 1.0 | — | — | — |

TW sell-pressure penalties reduce the earned score ratio without increasing
`max_score`. They are intentionally limited to explicit sell signals so failed
positive rules are not double-counted as penalties:

| Sell-pressure rule | Penalty |
|---|---:|
| Close below MA10 | -8% |
| Close below MA20 | -12% |
| Close below the latest high-volume long bullish candle low | -12% |
| Close below the low from two sessions ago | -6% |
| Close below the prior 5-day low | -8% |
| Volume down day (vol ≥ 1.3x 20-day average and negative return) | -10% |
| TW benchmark below MA10 | -6% |

`下跌特別注意` uses the current TW strategy regime:

- Bear/crash: close below the prior 5-day low.
- Bear/downtrend: sell-pressure-adjusted score ratio below 20%; special
  attention requires a 70% score ratio.
- Bull: close below MA5 and score ratio below 20%.
- Range: sell-pressure-adjusted score ratio below 20%.

TW regime selection uses 0050. US regime selection uses SPY:

- bear/crash: 120-day drawdown ≤ -12%.
- bear/downtrend: close < MA240 and MA60 < MA240.
- bull: close > MA20 and MA60, MA60 > MA240, and 60-day return > 3%.
- range: fallback. This keeps high exposure unless there is a meaningful
  drawdown.

Sector, target-price, and chip weights are reduced only in bear/crash mode
because those inputs can lag price during sharp selloffs. They remain unchanged
in range and bull modes because the current historical backtests did not include
reliable daily history for those datasets.

Rules removed from scoring: close within 2% of 20-day high, and latest rating
is Buy or Strong Buy.

The `特別注意` section requires a newly-above-all-moving-averages signal and
at least a 50% score. Target-price raises remain visible in each stock's signal
list and score, but do not by themselves place a stock in `特別注意`.
A separate `下跌特別注意` section flags stocks that were above all moving
averages on the previous trading day but closed below MA5 today.

Sector strength uses objective classifications only. TW symbols come from TWSE
and TPEx OpenAPI industry codes; US symbols use yfinance sector/industry data.
Groups need at least 3 watchlist members. A sector is strong when its equal-
weighted 1-day and 5-day returns beat the benchmark, its 1-day return is
positive, and at least 50% of members close above MA5. Continuation scores:
day 1 = 0.5, day 2 = 1.0, days 3-5 = 1.5, day 6+ = 1.0.

## Local development

```bash
uv sync
# US run needs the Finnhub key
export FINNHUB_API_KEY="..."
uv run python -m screener.run --market us
uv run python -m screener.run --market tw
uv run python scripts/build_sector_map.py
uv run streamlit run streamlit_app.py
uv run pytest
```

## Editing the watchlist

`data/watchlist.csv` columns: `symbol,market,name,tradingview_symbol`.

- `symbol` is what `yfinance` accepts. TW stocks use the `.TW` suffix (e.g. `2330.TW`).
- `market` is `tw` or `us`. Only US runs hit Finnhub.
- `tradingview_symbol` is the embed format: `EXCHANGE:TICKER` (e.g. `NASDAQ:NVDA`, `TWSE:2330`).

## GitHub Actions

- `tw_run.yml` — TW intraday and EOD scheduled runs.
- `us_run.yml` — US intraday and EOD scheduled runs.

Both jobs commit `data/latest_signals.json` with `[skip ci]`. Manual triggers via
the Actions tab work as well.

Required repo secret: `FINNHUB_API_KEY`.

See `docs/OPERATIONS.md` for exact cron schedules and runbook details.

US analyst target detail is parsed from recent Finnhub company news headlines when
the headline clearly says an analyst/firm raised a price target. Parsed events
are shown in the dashboard's Analyst section with date, firm/source, target,
previous target when available, and upside versus the current screen price.
EOD US runs also merge these parsed events into `data/analyst_target_events.jsonl`
with stable event IDs, so the dashboard can show the selected stock's recent
target-price history without turning the project into a database-backed app.

TW target updates are stored manually in `data/tw_target_events.jsonl`, using the
same event-log shape. A typical entry is:

```json
{"symbol":"2330.TW","market":"tw","event_date":"2026-05-25","published_at":"2026-05-25T00:00:00+00:00","firm":"凱基投顧","action":"raise","previous_target":1200,"target_price":1300,"source":"manual"}
```

## Market Regime

Each data run also refreshes market-level trend indicators:

- TW: `^TWII` 加權指數 and `^TWOII` 櫃買指數.
- US: `^GSPC` S&P 500, `^IXIC` NASDAQ, and `^SOX` 費半.

The dashboard shows whether each index is above all tracked moving averages
(MA5/10/20/240), using `V` when the condition is true and `X` when it is not.

## Deploy the dashboard

1. Push this repo to GitHub (public).
2. On [streamlit.io/cloud](https://streamlit.io/cloud), connect the repo and pick
   `streamlit_app.py` as the entrypoint.
3. The dashboard reads `data/latest_signals.json` directly from the workspace,
   so as long as the repo is connected it stays in sync without extra config.
