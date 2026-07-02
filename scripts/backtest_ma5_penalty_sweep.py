"""Sweep: does adding a soft below-MA5 term to the graded penalty exit help?

Compares, per regime preset (bear / choppy-range / bull) and per market
(TW vs US), the current hard MA5 exit and several graded-penalty variants that
add a small MA5 penalty while (optionally) making the MA10 break heavier.

Motivation: in a bull regime a close below MA5 often recovers ("站回"), so a
hard MA5 exit whipsaws out of trend. A graded penalty that treats MA5 as a
light deduction and MA10 as a heavy one should hold through noise and only exit
when the deeper trend deteriorates.

Nothing in committed code is modified -- the module-level _penalty_ratio is
monkeypatched per config, which works because _sell_signal resolves it as a
module global at call time.

Usage:
    uv run python scripts/backtest_ma5_penalty_sweep.py --output-csv results/backtests/ma5_penalty_sweep.csv
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import scripts.backtest_tw_exit_strategy as tw_exit
import scripts.backtest_us_exit_strategy as us_exit
from scripts.backtest_tw_strategy import _load_tw_symbols
from scripts.backtest_us_strategy import _load_us_symbols


# (label, ma5_weight, ma10_weight) for the graded penalty. Other sell-pressure
# terms (ma20=0.12, big_bull_low=0.12, prev2_low=0.06, prev5_low=0.08,
# vol_down=0.10, market_ma10=0.06) are held at their production values.
PENALTY_CONFIGS = [
    ("penalty_no_ma5", 0.00, 0.08),          # B: current production penalty
    ("penalty_v2_ma5_03_ma10_15", 0.03, 0.15),  # C: user's V2
    ("penalty_ma5only_03", 0.03, 0.08),      # D: isolates the MA5 term alone
    ("penalty_ma5_05_ma10_15", 0.05, 0.15),  # E
]
PENALTY_THRESHOLDS = (0.10, 0.20)


def _make_penalty(ma5_w: float, ma10_w: float):
    def _penalty_ratio(row: dict) -> float:
        penalty = 0.0
        if row.get("below_ma5"):
            penalty += ma5_w
        if row["below_ma10"]:
            penalty += ma10_w
        if row["below_ma20"]:
            penalty += 0.12
        if row["below_big_bull_low"]:
            penalty += 0.12
        if row["below_prev2_low"]:
            penalty += 0.06
        if row["below_prev_5d_low"]:
            penalty += 0.08
        if row["down_day"] and (row["vol_ratio"] or 0.0) >= 1.3:
            penalty += 0.10
        if row["market_below_ma10"]:
            penalty += 0.06
        return penalty

    return _penalty_ratio


def _build_case(mod, loader, watchlist: Path, start: str, end: str, days: int):
    names = loader(watchlist)
    symbols = sorted(set(names) | {mod.BENCHMARK})
    data = mod._download_range(symbols, start, end)
    if mod.BENCHMARK not in data:
        raise RuntimeError(f"missing benchmark data: {mod.BENCHMARK}")
    names = {s: n for s, n in names.items() if s in data}
    benchmark_ind = mod._indicators_for_frame(data[mod.BENCHMARK])
    all_dates = sorted(data[mod.BENCHMARK].index)
    backtest_dates = mod._date_slice(all_dates, start, end, days)
    signal_data = {s: data[s] for s in names}
    signal_facts = mod._build_signal_facts(signal_data, benchmark_ind)
    signals = mod._signals_from_facts(signal_facts, mod.DEFAULT_WEIGHTS)
    exits = mod._build_exit_rows(signal_data, data[mod.BENCHMARK])
    return data, signals, exits, names, backtest_dates


def _run_strategy(mod, data, signals, exits, names, dates, strategy, label, case, market):
    curve, trades, holdings = mod.run_exit_backtest(
        data, signals, exits, names, dates, strategy
    )
    summary = mod._summarize(label, dates, data, trades, holdings, curve)
    summary["case"] = case
    summary["market"] = market
    summary.update(mod._trade_stats(trades))
    return summary


def _run_market(mod, loader, market: str, watchlist: Path, cases, days: int) -> list[dict]:
    rows: list[dict] = []
    for case, start, end in cases:
        data, signals, exits, names, dates = _build_case(
            mod, loader, watchlist, start, end, days
        )

        # A: current hard MA5 exit (adopted bull/range rule) -- reference.
        rows.append(
            _run_strategy(
                mod, data, signals, exits, names, dates,
                mod.ExitStrategy("baseline_ma5", "ma5"),
                "A_baseline_hard_ma5", case, market,
            )
        )

        for cfg_label, ma5_w, ma10_w in PENALTY_CONFIGS:
            mod._penalty_ratio = _make_penalty(ma5_w, ma10_w)
            for thr in PENALTY_THRESHOLDS:
                strat = mod.ExitStrategy(
                    f"{cfg_label}_thr{int(thr * 100)}",
                    "penalty_score",
                    penalty_threshold=thr,
                )
                rows.append(
                    _run_strategy(
                        mod, data, signals, exits, names, dates, strat,
                        f"{cfg_label}_thr{int(thr * 100)}", case, market,
                    )
                )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=252)
    parser.add_argument("--watchlist", default="data/watchlist.csv")
    parser.add_argument("--output-csv", default="results/backtests/ma5_penalty_sweep.csv")
    parser.add_argument("--market", choices=["tw", "us", "both"], default="both")
    parser.add_argument(
        "--oos-bull",
        action="store_true",
        help="run two out-of-sample bull windows (2019, 2023-2024) instead of the regime presets",
    )
    args = parser.parse_args()

    # Out-of-sample bull windows -- fully independent of the 2025-2026 bull used
    # in the original sweep. 0050 and SPY were both strongly up in each.
    oos_bull_cases = [
        ("bull_2019", "2019-01-02", "2019-12-31"),
        ("bull_2023_2024", "2023-01-03", "2024-12-31"),
    ]

    watchlist = Path(args.watchlist)
    tw_cases = oos_bull_cases if args.oos_bull else tw_exit._preset_cases()
    us_cases = oos_bull_cases if args.oos_bull else us_exit._preset_cases()
    rows: list[dict] = []
    if args.market in ("tw", "both"):
        rows += _run_market(
            tw_exit, _load_tw_symbols, "tw", watchlist, tw_cases, args.days,
        )
    if args.market in ("us", "both"):
        rows += _run_market(
            us_exit, _load_us_symbols, "us", watchlist, us_cases, args.days,
        )

    frame = pd.DataFrame(rows)
    frame = frame.sort_values(["market", "case", "excess_pct"], ascending=[True, True, False])
    columns = [
        "market", "case", "label",
        "active_return_pct", "benchmark_return_pct", "excess_pct",
        "max_drawdown_pct", "buys", "sells", "open_positions",
        "trade_win_rate_pct", "avg_trade_return_pct", "avg_hold_days",
    ]
    present = [c for c in columns if c in frame.columns]
    print(frame[present].to_string(index=False))
    if args.output_csv:
        Path(args.output_csv).parent.mkdir(parents=True, exist_ok=True)
        frame.to_csv(args.output_csv, index=False)
        print(f"\nwrote {args.output_csv}")


if __name__ == "__main__":
    main()
