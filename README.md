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
  tw_target_events.jsonl  Manual TW analyst target history event log
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
| analyst target raised within 7 days and target ≥ current price +10% | 2.0 | yes | yes |
| 投信連續買超 ≥ 3 日 | 2.0 | yes | — |
| 外資大買 (>5% volume or 3-day streak) | 1.5 | yes | — |

Rules removed from scoring: close within 2% of 20-day high, and latest rating
is Buy or Strong Buy.

The dashboard also separates an `上漲加碼` section before `特別注意`.
It contains stocks that are newly above all moving averages, score at least
80%, close at a 5-day high, beat the benchmark on 20-day relative strength,
and have OBV 5d above OBV 20d.
The regular `特別注意` section requires a newly-above-all-moving-averages signal
and at least a 50% score. Target-price raises remain visible in each stock's
signal list and score, but do not by themselves place a stock in `特別注意`.
A separate `下跌特別注意` section flags stocks that were above all moving
averages on the previous trading day but closed below MA5 today.

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

### YouTube digest

`youtube_digest.yml` runs daily at 23:30 UTC and checks Rhino Finance's
YouTube RSS feed for uploads from the last 7 days. For each new video inside the
lookback window, it reads the public caption track, summarizes stock-specific
ideas into Markdown, and commits the report under `data/youtube_digest/`. When
no public caption track is available, the workflow downloads the video's audio
and uses a local Whisper model to transcribe it before summarizing. Per-video
Markdown reports older than 7 days are pruned on each run.

The dashboard shows `data/youtube_digest/latest.md` in the `影片精華` tab.
Per-video summaries are stored as `data/youtube_digest/YYYY-MM-DD_<video-id>.md`.
`data/youtube_digest/latest.json` is metadata for the latest batch, and
`data/youtube_digest/state.json` stores de-duplication state. The summary prompt
is constrained to individual stocks/ETFs and only fills buy, sell, take-profit,
or stop-loss prices when the video explicitly states them.

The workflow uses GitHub Models through the built-in `GITHUB_TOKEN`, so no
OpenAI or Claude API key is required. Optional repo variable: `GITHUB_MODEL`
(defaults to `openai/gpt-4.1`). Optional repo variable:
`YOUTUBE_WHISPER_MODEL` (defaults to `base`).

For a manual run, use `workflow_dispatch`. The defaults scan the last 7 days and
process up to 3 videos. Videos can be reprocessed on later runs so the latest
report stays populated even when the channel has no brand-new upload.

To run transcription locally with your browser's YouTube login, first make sure
that browser is signed in to YouTube, then run:

```bash
uv pip install yt-dlp openai-whisper
export GITHUB_TOKEN="$(gh auth token)"
uv run python -m screener.youtube_digest \
  --since-hours 168 \
  --max-new 3 \
  --audio-fallback \
  --cookies-from-browser chrome
git add data/youtube_digest
git commit -m "data: YouTube digest run [skip ci]"
git push origin main
```

Use `--cookies-from-browser safari`, `firefox`, or `brave` if that is where
YouTube is signed in. Keep browser cookies private; they act like login
credentials.

## Deploy the dashboard

1. Push this repo to GitHub (public).
2. On [streamlit.io/cloud](https://streamlit.io/cloud), connect the repo and pick
   `streamlit_app.py` as the entrypoint.
3. The dashboard reads `data/latest_signals.json` directly from the workspace,
   so as long as the repo is connected it stays in sync without extra config.
