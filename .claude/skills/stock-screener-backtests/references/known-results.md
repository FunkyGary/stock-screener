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

### TW KD(9,3,3) experiment (rejected, 2026-10-07)

Tested adding KD to the TW screener across 4 regimes x 9 base-weight sets x 180
variants (golden-cross / low-zone-cross / K>D score weights, entry K gate 80/90,
4 exit rules). Summary row: `tw_kd_sweep_cross_regime_summary_rejected`; per-set
best rows: `tw_*_kd_sweep_*_best_rejected`; CSVs: `results/backtests/tw_*_kd_sweep_*.csv`.

- **No variant has a positive mean excess delta across the 4 regimes, and none is positive in all 4.**
  Per-regime in-sample bests (up to +12pp) are best-of-180 picks that do not replicate.
- KD golden-cross / K>D score weights: negative in most regimes, worst in the 2025-26 bull (-12 to -29pp).
- Entry gate K<=90: roughly neutral (+0.1 / +1.3 / +1.1 / -3.6pp); K<=80: mixed.
- Only lead: exit "MA5 break OR dead cross from K>=80" helped both bear periods (~+2pp) but cost ~10pp in bull. Unreplicated; treat as a hypothesis for out-of-sample / US testing.
- Caveats: 2022 uses the generic MA5 exit / 50% threshold, not the adopted penalty exit / 70% threshold; adding KD weight also dilutes the fixed 50% entry ratio; watchlist survivorship; bull path drawdown ~-90% in every row.
- Code: `screener.indicators.kd`, `scripts/backtest_tw_strategy.py --kd-sweep`. KD is **not** used by production scoring.

### TW Bollinger squeeze / band and RSI entry gates (rejected, 2026-10-07)

12 entry gates (Bollinger(20,2) bandwidth squeeze before breakout, with/without
close above the upper band; above/below upper band; RSI14 <= 70/80) vs the no-gate
baseline, on the same 9 sets as the KD sweep plus a 6-year out-of-sample check
(2016-2019, 2023, 2024). Rows: `tw_gate_sweep_in_sample_summary_rejected`,
`tw_gate_sweep_oos_summary_rejected`, `tw_range_2021_below_upper_gate_nonreplicating_rejected`;
CSVs: `results/backtests/tw_*_gate_sweep_*.csv`, `tw_oos_*_gate_sweep_default.csv`.

- **Squeeze gates hurt overall:** they cut buys 20-50%, cost 27-54pp in the 2025-26 bull, and help only the 2022 bear (+4 to +10pp). OOS mean is about 0 with -10pp in 2023.
- **"Not above upper band" looked great in 2021 (+23pp on all three weight sets) but did not replicate** (OOS mean -1.6pp, positive in 2/6 years) - a one-year artifact.
- **RSI<=80 cap** is the only steady one but tiny (+0.3pp in-sample, +1.4pp OOS); not worth a rule.
- RSI14 vs KD K: correlation 0.76 on 2021 data; RSI tracks 20d return (0.75) more than K does (0.49), so it overlaps the existing trend / relative-strength rules.
- Code: `scripts/backtest_tw_strategy.py --gate-sweep`. Not used by production scoring.

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

## Live performance replay (2026-05-21 .. 2026-10-07, reference)

Replayed every EOD snapshot in git history through the dashboard's own 特別注意 /
下跌特別注意 helpers; buy 50k per signal at next open, exit per product rule, idle
capital in the benchmark, no fees. Rows: `live_replay_tw_2026_05_10_product_exit_reference`,
`live_replay_us_2026_05_10_product_exit_reference`.

| Market | Period | Strategy | Benchmark | Excess | Max DD | Notes |
|---|---|---:|---:|---:|---:|---|
| TW | 2026-05-28..10-07 | +20.5% | 0050 +12.3% | +7.35% | -11.2% | Not significant (t=0.6); top 10 trades = 314% of PnL; +1.1% after 0.38% round-trip cost, -2.3% after 0.585%. |
| US | 2026-05-26..10-07 | -8.1% | SPY +3.8% | -11.47% | -18.2% | 20-day event-study excess -4.2pp (t=-3.4). Negative before costs. |

- Fixed-horizon event study (buy next open): TW excess vs 0050 about 0 at 5/10/20d; the score threshold beats the "newly above all MAs but below threshold" control by ~1.3pp at 5d in TW, nothing in US.
- Higher score ratio did NOT mean better outcomes in TW (excess by quartile +1.2 / +0.9 / +0.1 / -0.6pp).
- Entries >11% above MA20 did worst live, but a backtest of that filter (`tw_extension_ma20_entry_gate_rejected`) was not robust (bull 2025-26 -43pp, 2023 -13pp).
- Period had no real bear market; treat as a small-sample reference, not a verdict.

## Costs, turnover and the watchlist effect (2026-10-07)

Rows: `tw_baseline_net_of_cost_reference`, `tw_turnover_controls_sweep_rejected`,
`watchlist_universe_vs_strategy_reference`, `us_oos_baseline_reference`. CSVs:
`tw_turnover_sweep.csv`, `watchlist_universe_vs_strategy.csv`,
`us_oos_baseline_default_vs_live_weights.csv`.

- **All earlier results are gross of costs.** TW baseline (default weights, ~1,090 buys per period) vs 0050 over 10 periods (2016-2024 yearly, 2025-26): mean / median excess at 0% / 0.1% / 0.2% / 0.4% round-trip cost = +9.9 / +5.2 / +0.5 / -8.8 and +2.0 / -2.4 / -6.6 / -15.1 (positive periods 5 / 5 / 4 / 2 of 10). The gross mean is carried by 2025-26.
- **Turnover controls** (64 variants, 0.4% cost): no variant meets the adoption bar. Minimum hold is the useful lever (hold 3 / 5 / 10 days: +1.6 / +2.9 / +6.9pp, buys 1088 -> 949 / 783 / 528); position caps help the mean but lose 20-40pp in the 2025-26 bull. Best variants are still ~-1pp vs 0050 net of 0.4% cost.
- **The score is not a better ranker than random** under a position cap (net excess cap 5: score -5.5, random -4.3, 20d momentum -4.3, closest-to-MA20 -5.9). It works only as a threshold.
- **The backtest-vs-live gap is mostly the watchlist, not the timing.** The current watchlist is hindsight-picked: holding it equal-weight beat SPY by +26.6pp/yr on average (US) and 0050 by +10.4pp (TW). The US strategy beat SPY by +12.4pp but trailed its own universe by -14.2pp (beats it in 1/10 periods); TW beats its universe in 4/10 (median -6.0pp).
- US baseline vs SPY on years not used for tuning (2016-2019, 2023, 2024): mean +6.7pp, 5/6 positive; live 2026-06..10: -8.0pp.
- The US `run_backtest` copy was not updated with the TW turnover/cost options.

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
