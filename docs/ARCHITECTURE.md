# Architecture

Compact map for agents. Read this before opening source files.

## Pipeline

```text
data/watchlist.csv
  -> screener.run.run_market(market, mode)
  -> fetch OHLCV and market-specific inputs
  -> indicators.compute()
  -> sectors.build_sector_snapshots()
  -> score.score()
  -> data/latest_signals.json
  -> streamlit_app.py dashboard
```

GitHub Actions runs the same entrypoint and commits generated outputs back to
the repo. Trading remains manual; this app only screens and displays signals.

## Module Ownership

| Area | Open first | Notes |
|---|---|---|
| Pipeline orchestration | `screener/run.py` | Market loop, mode behavior, output assembly, latest-signal writes. |
| External data fetch | `screener/fetch.py` | yfinance OHLCV, Finnhub analyst/news parsing. |
| Indicators | `screener/indicators.py` | Snapshot fields used by scoring, market regime, and charts. |
| Scoring | `screener/score.py` | Rule weights, skip behavior, score reasons. |
| File I/O and schemas | `screener/io.py` | Watchlist, sector map, generated JSON, target-event JSONL. |
| TW chip data | `screener/chip.py` | TWSE T86 fetch and institutional buy snapshots. |
| Sector strength | `screener/sectors.py` | Grouping, breadth, continuation-day scoring input. |
| Market regime | `screener/market_regime.py` | Index trend context shown above stock signals. |
| Dashboard | `streamlit_app.py` | Streamlit rendering, local Plotly chart fallback, target history display. |
| Sector-map refresh | `scripts/build_sector_map.py` | TWSE/TPEx/yfinance sector map builder. |

## Run Modes

`eod`:

- Fetches OHLCV.
- US: refreshes analyst/news data and merges new target events into
  `data/analyst_target_events.jsonl`.
- TW: fetches chip data and reads manual TW target events.
- Writes `data/latest_signals.json`.

`intraday`:

- Fetches OHLCV.
- Carries forward analyst and chip blobs from the previous snapshot.
- Updates price-driven indicators and scores without mutating analyst/chip
  state.

## Output Shape

`data/latest_signals.json` top level:

- `generated_at`: UTC timestamp for the snapshot write.
- `last_run`: per-market and per-mode UTC timestamps.
- `market_regime`: TW and US index trend context.
- `signals`: map of symbol to signal record.

Each `signals[*]` record usually contains:

- identity: `symbol`, `market`, `name`, `tradingview_symbol`, `status`, `mode`
- scoring: `score`, `max_score`, `reasons`
- data blobs: `indicators`, `analyst`, `chip`, `sector`

Failed fetches keep a signal record with `status: "fetch_failed"` and `error`.

## Change Hotspots

- Scoring rule behavior: edit `screener/score.py`, then tests in
  `tests/test_score.py`, and update README if visible scoring semantics change.
- Output contract changes: edit `screener/run.py` or `screener/io.py`, then
  tests in `tests/test_run_mode.py` or `tests/test_target_events_io.py`.
- Watchlist or target events: read `docs/DATA_CONTRACTS.md` before editing data.
- Dashboard-only rendering: usually `streamlit_app.py` plus
  `tests/test_streamlit_app.py`.
