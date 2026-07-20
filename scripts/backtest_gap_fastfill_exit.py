"""Exp 2b: does a fast-filled up-gap exit improve the portfolio?

Exp 2a (gap event study) found the only robust, orthogonal gap signal is a
fast-filled up-gap (an up-gap that trades back below its origin high within 2
bars = failed breakout), which underperforms short-term. This tests it as an
EXIT trigger inside the existing exit harness, per regime preset and per market.

For each preset case it compares, against the adopted exit baselines:
  - baseline_ma5                    (hard MA5 exit, adopted TW bull/range)
  - baseline_ma5+fastfill           (MA5 OR fast-fill)
  - penalty_score_lt_20pct          (graded penalty exit)
  - penalty_score_lt_20pct+fastfill (penalty OR fast-fill)
  - fastfill_only                   (isolate fast-fill's standalone exit value)

Nothing in committed code is modified: the exit modules' `_build_exit_rows` and
`_sell_signal` are monkeypatched at the module level (same pattern as
backtest_ma5_penalty_sweep.py), so existing scripts and results are unaffected.

Usage:
    uv run python scripts/backtest_gap_fastfill_exit.py \
        --output-csv results/backtests/gap_fastfill_exit.csv
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import scripts.backtest_tw_exit_strategy as tw_exit  # noqa: E402
import scripts.backtest_us_exit_strategy as us_exit  # noqa: E402
from scripts.backtest_tw_strategy import _load_tw_symbols  # noqa: E402
from scripts.backtest_us_strategy import _load_us_symbols  # noqa: E402

MIN_GAP = 0.01
FAST_FILL_BARS = 2


def _fast_fill_series(df: pd.DataFrame, min_gap: float, fast_fill_bars: int) -> pd.Series:
    """Boolean per day: an up-gap opened <=fast_fill_bars ago filled TODAY.

    Detected on the fill day (no lookahead); the harness acts on it next open.
    up-gap at g: low[g] > high[g-1] * (1 + min_gap); support = high[g-1];
    fill = first later bar low <= support, flagged only if within fast_fill_bars.
    """
    high = df["High"].astype(float).to_numpy()
    low = df["Low"].astype(float).to_numpy()
    n = len(df)
    out = np.zeros(n, dtype=bool)
    for g in range(1, n):
        ph = high[g - 1]
        if ph > 0 and (low[g] - ph) / ph >= min_gap:
            support = ph
            for k in range(g + 1, min(n, g + fast_fill_bars + 1)):
                if low[k] <= support:
                    out[k] = True
                    break
    return pd.Series(out, index=df.index)


def _patch_module(mod, min_gap: float, fast_fill_bars: int) -> None:
    orig_build = mod._build_exit_rows
    orig_sell = mod._sell_signal

    def build_with_fastfill(data, benchmark):
        rows_by_date = orig_build(data, benchmark)
        for symbol, df in data.items():
            if symbol == mod.BENCHMARK:
                continue
            ff = _fast_fill_series(df, min_gap, fast_fill_bars)
            for date, flag in ff.items():
                row = rows_by_date.get(date, {}).get(symbol)
                if row is not None:
                    row["fast_filled_up_gap"] = bool(flag)
        # ensure the key exists everywhere so _sell_signal never KeyErrors
        for by_symbol in rows_by_date.values():
            for row in by_symbol.values():
                row.setdefault("fast_filled_up_gap", False)
        return rows_by_date

    def sell_with_fastfill(strategy, signal, exit_row):
        ff = bool(exit_row.get("fast_filled_up_gap"))
        if strategy.kind == "fastfill":
            return ff
        base = orig_sell(strategy, signal, exit_row)
        if strategy.label.endswith("+fastfill"):
            return base or ff
        return base

    mod._build_exit_rows = build_with_fastfill
    mod._sell_signal = sell_with_fastfill


def _strategies(mod) -> list:
    ES = mod.ExitStrategy
    return [
        ES("baseline_ma5", "ma5"),
        ES("baseline_ma5+fastfill", "ma5"),
        ES("penalty_score_lt_20pct", "penalty_score", penalty_threshold=0.20),
        ES("penalty_score_lt_20pct+fastfill", "penalty_score", penalty_threshold=0.20),
        ES("fastfill_only", "fastfill"),
    ]


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


def _run_market(mod, loader, market: str, watchlist: Path, cases, days: int) -> list[dict]:
    rows: list[dict] = []
    for case, start, end in cases:
        data, signals, exits, names, dates = _build_case(
            mod, loader, watchlist, start, end, days
        )
        for strategy in _strategies(mod):
            curve, trades, holdings = mod.run_exit_backtest(
                data, signals, exits, names, dates, strategy
            )
            summary = mod._summarize(
                strategy.label, dates, data, trades, holdings, curve
            )
            summary["case"] = case
            summary["market"] = market
            summary.update(mod._trade_stats(trades))
            rows.append(summary)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=252)
    parser.add_argument("--watchlist", default="data/watchlist.csv")
    parser.add_argument("--output-csv", default="results/backtests/gap_fastfill_exit.csv")
    parser.add_argument("--market", choices=["tw", "us", "both"], default="both")
    parser.add_argument("--min-gap", type=float, default=MIN_GAP)
    parser.add_argument("--fast-fill-bars", type=int, default=FAST_FILL_BARS)
    args = parser.parse_args()

    watchlist = Path(args.watchlist)
    rows: list[dict] = []
    if args.market in ("tw", "both"):
        _patch_module(tw_exit, args.min_gap, args.fast_fill_bars)
        rows += _run_market(
            tw_exit, _load_tw_symbols, "tw", watchlist, tw_exit._preset_cases(), args.days
        )
    if args.market in ("us", "both"):
        _patch_module(us_exit, args.min_gap, args.fast_fill_bars)
        rows += _run_market(
            us_exit, _load_us_symbols, "us", watchlist, us_exit._preset_cases(), args.days
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
