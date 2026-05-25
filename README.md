# stock-screener

Personal daily stock screener. GitHub Actions runs the data pipeline twice a day,
Streamlit Community Cloud serves the dashboard, TradingView shows the charts.
Trading is still manual.

## Layout

```
.github/workflows/   GitHub Actions cron jobs (tw_run.yml, us_run.yml)
screener/            Python module: fetch / indicators / score / io / run
data/
  watchlist.csv      Hand-edited list of symbols to screen
  latest_signals.json  Output written by Actions, read by Streamlit
  analyst_target_events.jsonl  US analyst target raise history event log
streamlit_app.py     Dashboard
tests/               Unit tests for indicators + scoring
```

## Scoring

Rules use weighted points. Missing data skips the affected rule.

| Rule | Weight | TW | US |
|---|---:|:-:|:-:|
| 今日站上全均線 | 3.0 | yes | yes |
| 相對強度 20日 > 大盤 | 2.0 | yes | yes |
| 放量上漲 (vol > 1.5x and up day) | 1.5 | yes | yes |
| 短線趨勢確認 (close > MA5 and MA5 > MA20) | 1.5 | yes | yes |
| OBV 5d > OBV 20d | 1.0 | yes | yes |
| MACD golden cross | 1.0 | yes | yes |
| analyst target raised within 7 days and target ≥ current price +10% | 2.0 | — | yes |
| 投信連續買超 ≥ 3 日 | 2.0 | yes | — |
| 外資大買 (>5% volume or 3-day streak) | 1.5 | yes | — |

Rules removed from scoring: close within 2% of 20-day high, and latest rating
is Buy or Strong Buy.

## Local development

```bash
uv sync
# US run needs the Finnhub key
export FINNHUB_API_KEY="..."
uv run python -m screener.run --market us
uv run python -m screener.run --market tw
uv run streamlit run streamlit_app.py
uv run pytest
```

## Editing the watchlist

`data/watchlist.csv` columns: `symbol,market,name,tradingview_symbol`.

- `symbol` is what `yfinance` accepts. TW stocks use the `.TW` suffix (e.g. `2330.TW`).
- `market` is `tw` or `us`. Only US runs hit Finnhub.
- `tradingview_symbol` is the embed format: `EXCHANGE:TICKER` (e.g. `NASDAQ:NVDA`, `TWSE:2330`).

## GitHub Actions

- `tw_run.yml` — daily at 06:30 UTC (14:30 Asia/Taipei).
- `us_run.yml` — daily at 22:00 UTC (covers both EST and EDT post-close).

Both jobs commit `data/latest_signals.json` with `[skip ci]`. Manual triggers via
the Actions tab work as well.

Required repo secret: `FINNHUB_API_KEY`.

US analyst target detail is parsed from recent Finnhub company news headlines when
the headline clearly says an analyst/firm raised a price target. Parsed events
are shown in the dashboard's Analyst section with date, firm/source, target,
previous target when available, and upside versus the current screen price.
EOD US runs also merge these parsed events into `data/analyst_target_events.jsonl`
with stable event IDs, so the dashboard can show the selected stock's recent
target-price history without turning the project into a database-backed app.

## Deploy the dashboard

1. Push this repo to GitHub (public).
2. On [streamlit.io/cloud](https://streamlit.io/cloud), connect the repo and pick
   `streamlit_app.py` as the entrypoint.
3. The dashboard reads `data/latest_signals.json` directly from the workspace,
   so as long as the repo is connected it stays in sync without extra config.
