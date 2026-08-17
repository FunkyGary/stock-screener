---
name: target-updates
description: Repo-specific workflow for /Users/ghuang01/Documents/work/stock-screener. Use only for this stock-screener repo when the user provides manual Taiwan or US analyst target-price updates and wants Codex to store them in JSONL event logs, add missing watchlist tickers, test, commit, and push.
---

# Target Updates

## Scope

Use this skill only in `/Users/ghuang01/Documents/work/stock-screener`.

It handles manual analyst target-price updates for:

- TW: `data/tw_target_events.jsonl`
- US: `data/analyst_target_events.jsonl`

## Workflow

1. Start from a clean temporary worktree based on `origin/main`.
2. Parse the user's broker-grouped update text. Treat a heading line as `firm`; parse following rows as ticker/code plus target price.
3. For pasted table-style TW updates with columns like stock name/code, broker, rating, target price, and bullish reason:
   - Include rows rated `買進` or `強力買進`.
   - Exclude rows rated only `優於大盤`, `中立`, `持有`, `賣出`, or other non-buy ratings.
   - Require an explicit numeric target price. Skip vague targets such as `波段上修`.
   - Do not pause to discuss the filtered list unless the user explicitly asks to review it first.
   - Store broker as `firm`, rating text as `rating`, and the core bullish reason as `headline` when provided.
4. Resolve each symbol against `data/watchlist.csv`.
5. Add missing symbols to `data/watchlist.csv`:
   - TW listed: `1234.TW`, `TWSE:1234`
   - TW OTC: `1234.TWO`, `TPEX:1234`
   - US: use the exchange TradingView symbol, for example `NASDAQ:AAPL` or `NYSE:V`.
   Verify unknown names, exchanges, and TW listing venue before adding.
6. Create JSONL events with `source: manual` and `action: raise` unless the user says lower/initiate/maintain.
7. Merge using repo helpers:
   - TW: `screener.io.merge_tw_target_events(events)`
   - US: `screener.io.merge_target_events(events)`
8. Run `ruff check .` and `pytest`.
9. Commit and push to `origin/main`, unless the user explicitly says not to.
10. If the user asks for immediate scoring refresh, try to trigger the relevant GitHub Actions workflow. If permissions fail, report that the next scheduled/manual workflow run will pick it up.

## Event Shape

TW example:

```json
{"symbol":"2330.TW","market":"tw","event_date":"2026-05-25","published_at":"2026-05-25T00:00:00+00:00","firm":"凱基投顧","action":"raise","target_price":1300.0,"source":"manual"}
```

US example:

```json
{"symbol":"AAPL","market":"us","event_date":"2026-05-25","published_at":"2026-05-25T00:00:00+00:00","firm":"JPMorgan","action":"raise","target_price":250.0,"source":"manual"}
```

`previous_target`, `rating`, `headline`, and `url` are optional. Include them only when the user provides them.

## Helper Script

Use `scripts/parse_targets.py` to convert broker-grouped pasted text into draft JSON events. It does not edit repo files. Review and resolve emitted `raw_symbol` values before merging into repo JSONL.
