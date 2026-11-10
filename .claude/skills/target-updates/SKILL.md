---
name: target-updates
description: Repo-specific workflow for /Users/ghuang01/Documents/work/stock-screener. Use for logged-in Yuanta or SinoPac Taiwan research-report scans or manual Taiwan/US analyst target-price updates; only record targets with at least 20% upside from the latest current price, then update JSONL, test, commit, and push.
---

# Target Updates

## Scope

Use this skill only in `/Users/ghuang01/Documents/work/stock-screener`.

It handles manual analyst target-price updates for:

- TW: `data/tw_target_events.jsonl`
- US: `data/analyst_target_events.jsonl`
- Logged-in Yuanta Taiwan individual-stock research reports.
- Logged-in SinoPac (永豐投顧) Taiwan stock research reports at
  `https://scm.sinotrade.com.tw/`.

## Workflow

1. Start from a clean temporary worktree based on `origin/main`.
2. Choose the source:
   - For Yuanta reports, use the user's already-authenticated browser session. Open the research-report list, set the inclusive date range to today and the previous 13 calendar days, choose `台股個股`, and inspect every result page.
   - Keep reports rated `買進` or `強力買進`. Open each report and capture its publication date, ticker, rating, current target, previous target (if any), and report title. Only consider an actual target-price increase; exclude unchanged targets, target cuts, and reports without an explicit numeric target.
   - For SinoPac, use the user's authenticated browser session at `https://scm.sinotrade.com.tw/`. Choose `台股報告`, inspect the `個股脈動` category and any other stock-specific research categories, and page through all reports dated today and the previous 13 calendar days. Open each relevant report and capture its publication date, ticker, rating (when provided), current and previous target prices, title, and report URL. If the site requires member login to open a report, ask the user to finish logging in; never request or handle their credentials.
   - Apply the same buy-rating filter to SinoPac reports when a rating is provided. If its report format does not state a rating or prior target, keep those fields absent; only record a raise when an explicit current target is higher than an explicit prior target. A report without both values is not evidence of a target raise.
   - For pasted broker-grouped text, treat a heading line as `firm`; parse following rows as ticker/code plus target price.
3. Use the latest available market price, not the report's historical closing price, as the upside baseline. The helper uses `indicators.close` from `data/latest_signals.json`; its snapshot must be no more than 7 days old and the symbol must have a successful quote. If the snapshot or quote is missing/stale, refresh market data or stop and ask for a current price. Never silently skip the threshold check.
4. Keep only targets whose upside is at least 20%, calculated as `(target_price / current_price) - 1`. Exactly 20% qualifies; anything below 20% is excluded. Run the draft parser with the current signals file so the same gate applies to pasted updates:

   ```bash
   uv run python .claude/skills/target-updates/scripts/parse_targets.py \
     --market tw < updates.txt
   ```

   The script reads the repository's latest signals by default, prints excluded rows to stderr, and emits only qualifying draft events. Use `--signals PATH` to select another fresh signals snapshot.
5. For pasted table-style TW updates with columns like stock name/code, broker, rating, target price, and bullish reason:
   - Include rows rated `買進` or `強力買進`.
   - Exclude rows rated only `優於大盤`, `中立`, `持有`, `賣出`, or other non-buy ratings.
   - Require an explicit numeric target price. Skip vague targets such as `波段上修`.
   - Do not pause to discuss the filtered list unless the user explicitly asks to review it first.
   - Store broker as `firm`, rating text as `rating`, and the core bullish reason as `headline` when provided.
6. Resolve each qualifying symbol against `data/watchlist.csv`.
7. Add missing symbols to `data/watchlist.csv`:
   - TW listed: `1234.TW`, `TWSE:1234`
   - TW OTC: `1234.TWO`, `TPEX:1234`
   - US: use the exchange TradingView symbol, for example `NASDAQ:AAPL` or `NYSE:V`.
   Verify unknown names, exchanges, and TW listing venue before adding.
   - Obtain a fresh quote for newly added symbols before applying the 20% filter.
8. Create JSONL events with `source: manual` and `action: raise` unless the user says lower/initiate/maintain. Set `firm` to the broker that published the report (for example `元大` or `永豐`), and include the report URL and title as `url` and `headline` when available.
9. Merge using repo helpers:
   - TW: `screener.io.merge_tw_target_events(events)`
   - US: `screener.io.merge_target_events(events)`
10. Run `ruff check .` and `pytest`.
11. Commit and push to `origin/main`, unless the user explicitly says not to.
12. If the user asks for immediate scoring refresh, try to trigger the relevant GitHub Actions workflow. If permissions fail, report that the next scheduled/manual workflow run will pick it up.

## Event Shape

TW example:

```json
{"symbol":"2330.TW","market":"tw","event_date":"2026-05-25","published_at":"2026-05-25T00:00:00+00:00","firm":"凱基投顧","action":"raise","target_price":1300.0,"source":"manual"}
```

US example:

```json
{"symbol":"AAPL","market":"us","event_date":"2026-05-25","published_at":"2026-05-25T00:00:00+00:00","firm":"JPMorgan","action":"raise","target_price":250.0,"source":"manual"}
```

`previous_target`, `rating`, `headline`, and `url` are optional. Include them only when provided by the source. Store only events that pass the 20% upside gate.

## Helper Script

Use [scripts/parse_targets.py](./scripts/parse_targets.py) to convert broker-grouped pasted text into draft JSON events. It reads the latest signal prices, filters out targets below 20% upside, and does not edit repo files. Review and resolve emitted `raw_symbol` values before merging into repo JSONL. For website report scans, apply the same price and upside filter to each extracted report before creating events.
