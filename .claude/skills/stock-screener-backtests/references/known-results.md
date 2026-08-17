# Known Results

This file is a human-readable companion to
`results/backtests/backtest_registry.jsonl`. The registry is the source of
truth for machine lookup.

## Taiwan

| Regime | Period | Benchmark | Strategy | Benchmark | Excess | Notes |
|---|---:|---|---:|---:|---:|---|
| bear_crash | 2020-01-02..2020-12-31 | 0050.TW | +36.75% | +31.00% | +4.39% | 2020 was an acute selloff followed by recovery. |
| bear_downtrend | 2022-01-03..2022-12-30 | 0050.TW | -15.13% | -21.49% | +8.10% | 2022 was open-high, close-low bear/downtrend. |
| range | 2021-01-04..2021-12-30 | 0050.TW | +25.11% | +21.97% | +2.57% | Current range-style adopted weights, not best sweep. |
| bull | 2025-01-02..2026-05-28 | 0050.TW | +233.42% | +112.74% | +56.73% | Strong bull result; drawdown caveat is large. |

## United States

| Regime | Period | Benchmark | Strategy | Benchmark | Excess | Notes |
|---|---:|---|---:|---:|---:|---|
| bear_2022_weight_sweep | 2022-01-03..2022-12-30 | SPY | -23.04% | -18.41% | -5.68% | Weight-only optimization still lost to SPY. |
| bear_downtrend_2022_adopted | 2022-01-03..2022-12-30 | SPY | -13.06% | -18.41% | +6.55% | Currently adopted 2022 downtrend rule: SPY>MA10, score>=55%, max 10 positions, break big bull low with vol>=1.3x (`us_bear_downtrend_2022_robust_defense_adopted`). |
| bear_crash_2020_adopted (split repair) | 2020-02-19..2020-06-30 | SPY | +3.75% | -7.77% | +12.49% | Currently adopted 2020 crash rule, supersedes the two rows below (`us_bear_crash_2020_split_repair_adopted`). |
| bear_crash_2020_defense (superseded) | 2020-02-19..2020-06-30 | SPY | +3.32% | -7.77% | +12.03% | Pre-split-repair best_sweep row, kept for history (`us_bear_crash_2020_defense_best_sweep`). |
| bear_crash_2020_robust (superseded) | 2020-02-19..2020-06-30 | SPY | -1.19% | -7.77% | +7.14% | Pre-split-repair robust-defense row for 2020 crash, kept for history (`us_bear_crash_2020_robust_defense_adopted`). |
| range | 2021-01-04..2021-12-31 | SPY | +33.99% | +28.24% | +4.49% | Rotation/range-like result. |
| bull | 2025-01-02..2026-05-28 | SPY | +134.11% | +29.85% | +80.29% | Bull weight sweep best row. |

## Open Caveats

- US `bear_downtrend` (2022) uses the robust defensive bear setup
  (`us_bear_downtrend_2022_robust_defense_adopted`). US `bear_crash` (2020) has
  since moved to the split-repair rule (`us_bear_crash_2020_split_repair_adopted`,
  +12.49% excess), which supersedes the robust-defense row for that regime.
- Some entries are from one-off analysis rather than committed CSVs; see
  registry `source_type`.
- Valuation (PE/PB) and US EPS-surprise are **display-only, not scored**
  (`valuation_eps_surprise_display_only_path_a`). A scoring backtest is blocked
  by lack of free point-in-time historical fundamentals (Finnhub free tier =
  ~4 quarters, no earnings calendar, no historical PE/PB). Forward dataset is
  accumulating in `data/valuation_snapshots.jsonl`; revisit scoring once enough
  history exists or a paid fundamentals source is added.
- US profitability-trend (quarterly GM/OM/NM direction + 本業 vs 業外 net-vs-
  operating divergence) is also **display-only, not scored**
  (`profitability_margin_trend_display_only_path_a`). Source is yfinance
  `quarterly_income_stmt` (verified available for all 86 US watchlist symbols,
  ~4-5 usable quarters). Same scoring blocker: margins are as-of-now, not
  historical point-in-time. Latest-quarter margins are logged into
  `data/valuation_snapshots.jsonl` for the same forward dataset. Intended as
  confirmation alongside the technical breakout (real 戴維斯雙擊 vs sell-the-news
  trap), not a standalone signal.
- US 領先財報佈局 (Jeff 內訓-3) is also **display-only, not scored**
  (`revenue_eps_inflection_display_only_path_a`). Zero extra fetches: revenue
  reuses the yfinance `quarterly_income_stmt`, EPS actuals reuse the Finnhub
  `company_earnings` payload. Dashboard shows 營收 YoY (when ≥5 quarters), a
  營收/EPS 落底回升 flag (single-quarter series bottoming and turning up — an early
  領先 entry), and a sell-the-news caution (EPS/營收 at a multi-quarter high while
  price is ≥20% above MA20). Same scoring blocker: these levels are as-of-now,
  not historical point-in-time. `revenues`/`eps_actuals` live only in the
  display blob (not logged to `valuation_snapshots.jsonl` this round). The
  genuinely backtestable path — TW monthly revenue YoY, which IS point-in-time
  historical — is deferred to a separate (b) effort.
- **(b) done — TW monthly revenue YoY tested and rejected for scoring**
  (`tw_monthly_revenue_yoy_breakout_event_study`). The data blocker is broken:
  FinMind gives free/anonymous full monthly-revenue history for all 231 TW
  watchlist codes, made point-in-time by lagging each row +10 days (public by
  the 10th of the next month). Event study, 2018–2024, 24,102 newly-站上全均線
  breakouts, excess vs 0050: YoY>0 ex60 +2.19% vs YoY≤0 +1.49% (~0.7pp, ex20
  identical, win-rate ~equal); "翻正" is *worse* (ex20 +0.32%, win 44%); the
  YoY-sign edge is regime-inconsistent by year. Only high-magnitude growth
  (YoY≥30–50%, ex60 +3.5–3.9%) separates, and it is tail-driven (flat win-rate)
  and largely collinear with the existing 站上全均線/相對強度 momentum rules.
  Verdict: keep fundamentals **display-only** — now evidence-backed, not just a
  data-availability assumption. Tooling: `scripts/fetch_tw_month_revenue.py`,
  `scripts/backtest_tw_revenue_yoy.py`; events CSV
  `results/backtests/tw_revenue_yoy_breakout_events.csv`.
- **Graded MA-break penalty exit vs the hard MA5 exit — "MA5 break is noise,
  MA10 break is the signal"** (`{tw,us}_{bull_2025_2026,range_2021}` +
  `tw_bear_2020` / `us_bear_2022` `_graded_ma5_penalty_exit_reference`). Tests
  the idea that in a bull a close below MA5 usually recovers (站回), so a hard
  MA5 exit whipsaws you out; instead treat MA5 as a light deduction and MA10 as
  a heavier one, exiting only when `score_ratio − Σpenalty < threshold`. Sweep
  compared, on IDENTICAL entry signals, the hard-MA5 exit against graded-penalty
  variants: no-MA5 (ma10=0.08), V2 (ma5=0.03/ma10=0.15), ma5-only
  (ma5=0.03/ma10=0.08), heavy (ma5=0.05/ma10=0.15), at thresholds 0.10 and 0.20.
  Excess-return results (best graded variant vs current hard-MA5):
  - TW bull 25-26: +54.3% → **+70.0%** (V2, thr10), DD −25.0% → −23.4%
  - US bull 25-26: +53.2% → **+71.8%** (ma5-only, thr10), DD −24.9% → −24.3%
  - TW range 21: −0.1% → **+11.6%** (V2, thr20)
  - US range 21: −2.9% → **−0.3%** (V2, thr20; still trails SPY)
  - TW bear 20: −4.3% → **+0.5%** (ma5=0.05, thr20)
  - US bear 22: −11.1% → **−10.5%** (V2, thr20; all lose — use big-bull-low here)
  Verdict / lessons: (1) The dominant lever is **switching hard-MA5 → graded
  penalty**, not the MA5 term itself — graded beats hard-MA5 in ALL six
  market×regime cells, by +11–18pp in bull/range. (2) **Adding a soft MA5 term
  (0.03) is neutral-to-positive everywhere, never harmful at the right
  threshold** (biggest help TW bull thr10 +1.7pp, US range +3.2pp). (3) **Heavy
  MA10=0.15 is TW-friendly but slightly HURTS US bull** (V2 −2.5pp vs ma5-only)
  — argues for a per-market penalty table if ported. (4) **Threshold interacts
  with regime: bull → thr10 (patient), range/bear → thr20.** (5) MA5-penalty is
  not a bear tool (bear regimes keep break_big_bull_low+vol). NOT yet ported to
  `screener/score.py` (production bull/range still uses hard `close_below_ma5`).
  CAVEAT: `scripts/backtest_{tw,us}_strategy.py` had drifted from the current
  `screener` API — `AnalystSnapshot(target_mean=…, prev_target_mean=…)` no
  longer valid after the target-event refactor; only the crashing kwargs were
  removed to make the harness run, so the target-raise entry signal never fires
  and absolute returns are NOT comparable to older registry rows. Variant-vs-
  variant is apples-to-apples (same entries, same download). Tooling:
  `scripts/backtest_ma5_penalty_sweep.py` (monkeypatches the module-level
  `_penalty_ratio` per config; no production code changed); CSV
  `results/backtests/ma5_penalty_sweep.csv`.
- **OOS validation of the graded MA-break exit (bull 2019 + 2023-2024)**
  (`{tw,us}_bull_{2019,2023_2024}_graded_ma5_penalty_oos_reference`,
  `results/backtests/ma5_penalty_oos_bull.csv`, `--oos-bull` flag on the same
  sweep script). Two independent bull windows, run because the original single
  2025-2026 sample overfit risk was high. Excess vs 0050/SPY, thr10 (thr20 was
  uniformly worse in bull):
  - TW 2019: hard-MA5 +1.05%, no_ma5 **+1.41%**, ma5only +0.70%, V2 +0.28%, heavy −1.66%
  - TW 2023-24: hard-MA5 +34.82%, ma5only **+35.07%**, V2 +34.18%, no_ma5 +30.72%
  - US 2019: hard-MA5 +0.93%, ma5only **+2.83%**, no_ma5 +1.71%, V2 +1.20%
  - US 2023-24: hard-MA5 +23.92%, ma5only **+29.77%**, no_ma5 +27.60%, V2 +28.52%
  OOS verdicts (these OVERTURN parts of the in-sample read):
  1. **V2 / heavy MA10=0.15 ("十日線扣比較多分") does NOT survive OOS** — never
     best in any of the 4 windows, worst-tier in TW 2019. It was overfit to TW
     2025-2026. Drop it.
  2. **The OOS-robust winner is `ma5only` (soft MA5=0.03, MA10 unchanged at
     0.08, thr10)** — best in 3 of 4 windows, tie-in-noise in the 4th.
  3. **graded-vs-hard is only robust for US** (beats hard-MA5 +1.9pp in 2019,
     +5.9pp in 2023-24, consistent with US 2025-2026). **For TW it is a WASH on
     return** (ma5only ties hard-MA5 within noise in both windows; the large TW
     2025-2026 graded edge did not replicate), and the MA5-term itself is
     inconsistent in TW (hurts 2019, helps 2023-24).
  Bottom line: if ported, ship `ma5only` (MA5=0.03, MA10=0.08, thr10 bull /
  thr20 range), NOT V2; expect a real edge in US and roughly parity in TW. Same
  harness-drift caveat as the in-sample sweep (target entry signal inactive;
  variant-vs-variant valid, absolute returns not comparable to older rows).

## Removed (2026-08-17): unreproducible write-ups

Several experiment write-ups previously in this file (continuation-bonus entry
weighting, T+1-open confirm-still-above execution rule, and the full
gap-signal series — index exposure throttle, stock-level gap event study,
fast-fill exit overlay, regime-gated entry/no-force-sell) were removed. None
of their referenced registry ids, scripts, or CSVs exist on this branch, so
they could not be verified or reproduced, violating the registry-first rule
above. If these backtests are rerun, re-add them here in the same commit as
their `results/backtests/backtest_registry.jsonl` rows and CSV/script
artifacts.
