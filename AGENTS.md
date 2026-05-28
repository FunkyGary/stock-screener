# Agent Instructions

This is the canonical agent guide for this repository. Keep `CLAUDE.md` as a
thin pointer to this file so Claude Code and Codex share the same instructions.

## Project Shape

This repo is a personal daily stock screener. GitHub Actions refreshes signal
data, `streamlit_app.py` renders the dashboard, and TradingView is used for
chart inspection. Trading decisions remain manual.

Core modules:

- `screener/run.py` orchestrates market runs for `tw` and `us`.
- `screener/fetch.py` owns external OHLCV, Finnhub, and related fetch logic.
- `screener/indicators.py` computes technical indicator snapshots.
- `screener/score.py` owns rule-based scoring and human-readable reasons.
- `screener/io.py` owns watchlist, signal JSON, sector map, and target-event
  JSONL I/O.
- `screener/chip.py` handles TW institutional chip data.
- `screener/sectors.py` and `screener/market_regime.py` handle sector strength
  and index-level trend context.
- `streamlit_app.py` is the Streamlit dashboard.
- `tests/` should cover scoring, data I/O, run-mode behavior, and dashboard
  helpers when behavior changes.

## Run Modes

- `eod` fetches OHLCV plus end-of-day-only data such as analyst and TW chip
  inputs.
- `intraday` fetches OHLCV only and carries analyst/chip blobs forward from the
  previous snapshot.

Preserve this split when changing pipeline behavior. Intraday updates should not
silently mutate analyst or chip state.

## Data Conventions

- `data/watchlist.csv` is hand edited. TW yfinance symbols use the `.TW` suffix;
  `market` is `tw` or `us`; `tradingview_symbol` uses TradingView notation such
  as `TWSE:2330` or `NASDAQ:NVDA`.
- `data/latest_signals.json` is a generated snapshot written by runs and GitHub
  Actions. Do not include generated snapshot churn in feature commits unless the
  user specifically asks for it.
- `data/analyst_target_events.jsonl` stores US target-price raise events.
- `data/tw_target_events.jsonl` stores manual TW target-price raise events using
  the same normalized event shape.
- `data/sector_map.csv` should reflect objective TWSE/TPEx/yfinance sector
  classifications. Prefer updating it through `scripts/build_sector_map.py`
  when possible.

When adding manual target-price events, append normalized JSONL entries and rely
on `screener.io` merge/normalization helpers rather than ad hoc rewrites.

## Development Commands

```bash
uv sync
uv run pytest
uv run python -m screener.run --market tw
uv run python -m screener.run --market us
uv run python -m screener.run --market tw --mode intraday
uv run python scripts/build_sector_map.py
uv run streamlit run streamlit_app.py
```

US analyst fetches require `FINNHUB_API_KEY`.

## Change Guidelines

- Keep scoring changes centralized in `screener/score.py`; update tests and the
  README scoring table when rule behavior changes.
- Missing market data should skip only the affected rule or section rather than
  failing an entire run when a graceful fallback already exists.
- Prefer structured CSV/JSON/JSONL handling over string manipulation.
- Keep dashboard changes compatible with both desktop and mobile layouts.
- Add or update focused tests for behavior changes, especially around scoring,
  event normalization, generated signal shape, and run-mode carry-forward logic.

## Repository Workflow

- Unless the user explicitly says otherwise, completed code, documentation, or
  configuration changes should be committed and pushed to `origin/main`.
- Do not include local editor settings, caches, or generated data snapshots in
  feature commits unless the user specifically asks for them.
- Respect any existing dirty worktree changes. Do not revert user changes unless
  the user explicitly requests it.
