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
streamlit_app.py     Dashboard
tests/               Unit tests for indicators + scoring
```

## Scoring (v1)

Each rule that triggers adds one point.

| Rule | TW | US |
|---|:-:|:-:|
| close > MA5 | yes | yes |
| MA5 > MA20 | yes | yes |
| volume > 1.5x MA20 volume | yes | yes |
| close within 2% of 20-day high | yes | yes |
| consensus target raised vs last snapshot | — | yes |
| latest rating is Buy or Strong Buy | — | yes |

TW max = 4. US max = 6.

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

## Deploy the dashboard

1. Push this repo to GitHub (public).
2. On [streamlit.io/cloud](https://streamlit.io/cloud), connect the repo and pick
   `streamlit_app.py` as the entrypoint.
3. The dashboard reads `data/latest_signals.json` directly from the workspace,
   so as long as the repo is connected it stays in sync without extra config.
