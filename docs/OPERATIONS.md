# Operations

Compact runbook for local runs, scheduled jobs, and triage.

## Local Setup

```bash
uv sync
uv run pytest
```

US analyst/news fetches require:

```bash
export FINNHUB_API_KEY="..."
```

## Local Runs

```bash
uv run python -m screener.run --market tw --mode intraday
uv run python -m screener.run --market tw --mode eod
uv run python -m screener.run --market us --mode intraday
uv run python -m screener.run --market us --mode eod
uv run streamlit run streamlit_app.py
```

Refreshing sector classifications:

```bash
uv run python scripts/build_sector_map.py
```

## GitHub Actions

`tw_run.yml`:

- Intraday: `7,37 1-5 * * 1-5` UTC, Taiwan market session coverage.
- EOD: `17 9 * * 1-5` UTC, after TW chip data settles.
- Commits `data/latest_signals.json`.

`us_run.yml`:

- Intraday: `7,37 13-20 * * 1-5` UTC, covers EDT and EST trading windows.
- EOD: `17 21 * * 1-5` UTC, after US close in both EDT and EST.
- Requires `FINNHUB_API_KEY`.
- Commits `data/latest_signals.json` and, when present, US target-event history.

Both workflows support manual dispatch with `mode: intraday` or `mode: eod`.

## Deployment

Streamlit Community Cloud runs `streamlit_app.py`. The dashboard reads local
`data/latest_signals.json` first; `SIGNALS_URL` is an optional fallback for a raw
remote JSON source.

## Triage Checklist

Dashboard stale:

- Check `data/latest_signals.json` `generated_at` and `last_run`.
- Check GitHub Actions run status for the relevant market.
- Confirm Actions pushed the generated data commit.

US analyst or target events missing:

- Confirm `FINNHUB_API_KEY` is set in repo secrets or local environment.
- Run US EOD mode, not intraday mode.
- Check `data/analyst_target_events.jsonl` for merged events.

TW chip signals missing:

- Run TW EOD mode; intraday mode carries forward the previous chip blob.
- Check whether TWSE T86 data was available when the run executed.

Sector strength missing:

- Confirm the symbol exists in `data/sector_map.csv`.
- Groups need at least 3 watchlist members.
- Refresh with `scripts/build_sector_map.py` only when classification data is
  intentionally being updated.

Unexpected generated diff:

- If only `data/latest_signals.json` changed after a run, treat it as snapshot
  churn unless the task was to refresh data.
- Do not commit local editor settings, caches, screenshots, or `.env`.
