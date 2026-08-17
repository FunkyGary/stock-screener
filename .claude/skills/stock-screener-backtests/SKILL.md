---
name: stock-screener-backtests
description: Use for /Users/ghuang01/Documents/work/stock-screener when discussing, checking, running, or recording TW/US strategy backtests, regime weight sweeps, entry/exit rule experiments, and comparisons against 0050/SPY. Always check the registry before rerunning and record new results afterward.
metadata:
  short-description: Stock screener backtest workflow and result registry
---

# Stock Screener Backtests

Use this skill only for `/Users/ghuang01/Documents/work/stock-screener`.

## First Steps

1. **Check the registry before rerunning anything.** (`AGENTS.md` is the canonical guide and is assumed already loaded; don't open other large files just for a registry lookup.)

   Fast lookup — filter by the relevant fields instead of reading the whole file:
   ```bash
   jq -c 'select(.market=="tw" and .regime=="bear_crash")' results/backtests/backtest_registry.jsonl
   jq -c 'select(.status=="adopted")' results/backtests/backtest_registry.jsonl
   ```

2. **Exact match** = same `market + regime + period_start + period_end + benchmark + entry_rule + exit_rule + initial_capital + active_slot + weights`.
   → Summarize the existing result. Do not rerun.

3. **Near match** = only `entry_rule`, `exit_rule`, or 1–2 weight values differ.
   → Describe the differences. Only rerun if the user explicitly confirms.

4. **No match** → proceed with backtest workflow below.

## Current Adopted Configurations

Update this section whenever `status: adopted` rows change in the registry.

### Taiwan

| Regime | Entry | Exit | Key weights (vs default) | Excess | Period |
|--------|-------|------|--------------------------|--------|--------|
| `bear_downtrend` | score ≥ 70%, newly above all MAs | penalty score < 20% + sell pressure, next open | target=1, trust=1, foreign=0.5, sector=0.75 (reduced) | +8.10% | 2022 |
| `range` | score ≥ 50%, newly above all MAs | close below MA5, next open | above_all=4.5, macd=1.5, new_high=0.75 | +2.57% | 2021 |

TW `bear_crash` and `bull`: no dedicated adopted row — use best_sweep references.

Condition for TW bear/downtrend: `0050 close < MA240` and `MA60 < MA240`.

### US

| Regime | Entry gate | Exit | Excess | Period |
|--------|-----------|------|--------|--------|
| `bear_crash` (split repair) | SPY > MA5, MA5 trending up, score ≥ 60%, max 10 pos | break big bull low | +12.49% | 2020 crash |
| `bear_crash` (robust fallback) | SPY > MA10, score ≥ 55%, max 10 pos | break big bull low + vol ≥ 1.3× | +7.14% | 2020 crash |
| `bear_downtrend` | SPY > MA10, score ≥ 55%, max 10 pos | break big bull low + vol ≥ 1.3× | +6.55% | 2022 |

US weights: not applicable (gate + position size + exit rule drove improvement, not weight tuning).

## Registry Rules

Every completed backtest or parameter sweep that informs strategy decisions must be recorded in `results/backtests/backtest_registry.jsonl`.

Record:
- Full adopted strategy results, not only best-performing sweeps.
- Different parameter configurations that were compared.
- Negative results and failed ideas.
- Missing-symbol/data caveats.
- CSV output paths when generated.
- The command or script used, if available.
- The current commit hash when the result was produced or recorded.

Use JSON Lines. Field schema: `references/backtest-log-schema.md`.

After appending to the registry, also update `references/known-results.md` in the same commit.

## Backtest Workflow

1. Define scope: market, regime, period start/end, benchmark, entry rule, exit rule, weights, initial capital, active slot.
2. Check registry (see First Steps).
3. Search existing CSVs in `results/backtests/` before creating new runs.
4. If running is needed, prefer existing scripts:
   - `scripts/backtest_tw_strategy.py`
   - `scripts/backtest_tw_exit_strategy.py`
   - `scripts/backtest_us_strategy.py`
   - `scripts/backtest_us_exit_strategy.py`
   - `scripts/backtest_us_bear_defense.py`
5. Save sweep CSVs under `results/backtests/`.
6. Append registry rows **and** update `references/known-results.md` in the same commit.
7. Summarize: active return, benchmark return, excess return, max drawdown, buys/sells/open positions, key caveats.

## Interpretation Rules

- Do not claim a strategy predicts the future. Say it classifies current market structure and changes signal confidence, position sizing, and exit strictness.
- Separate "best sweep result" from "currently adopted production rule."
- For 2020 TW, treat as bear/crash then recovery. For 2022 TW, treat as bear/downtrend when `0050 close < MA240` and `MA60 < MA240`.
- If a result came from manual one-off analysis, mark `source_type` as `manual_analysis` with enough detail to reproduce.
- If data has missing symbols, keep the result but record the caveat.
