from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.backtest_us_bear_defense import (  # noqa: E402
    _benchmark_state,
    _run_defensive_backtest,
)
from scripts.backtest_us_exit_strategy import (  # noqa: E402
    _build_exit_rows,
    _exit_strategies,
    _trade_stats,
)
from scripts.backtest_us_strategy import (  # noqa: E402
    BENCHMARK,
    DEFAULT_WEIGHTS,
    _build_signal_facts,
    _date_slice,
    _download_range,
    _indicators_for_frame,
    _load_us_symbols,
    _signals_from_facts,
    _summarize,
)


@dataclass(frozen=True)
class Case:
    label: str
    regime: str
    start: str
    end: str


@dataclass(frozen=True)
class Config:
    label: str
    exit_label: str
    entry_gate: str
    min_ratio: float
    max_positions: int | None
    active_slot: float = 50_000.0


CASES = (
    Case("us_bear_crash_2020_split", "bear_crash", "2020-02-19", "2020-06-30"),
    Case("us_bear_downtrend_2022_split", "bear_downtrend", "2022-01-03", "2022-12-30"),
)


def _configs(case: Case) -> tuple[Config, ...]:
    if case.regime == "bear_crash":
        return (
            Config(
                "current_robust_bear_rule",
                "break_big_bull_low_and_vol_1_3x",
                "spy_above_ma10",
                0.55,
                10,
            ),
            Config(
                "crash_repair_best_existing",
                "break_big_bull_low",
                "spy_above_ma5_and_ma5_up",
                0.60,
                None,
            ),
            Config(
                "crash_repair_capped10",
                "break_big_bull_low",
                "spy_above_ma5_and_ma5_up",
                0.60,
                10,
            ),
            Config(
                "crash_repair_ma20_capped10",
                "break_big_bull_low_and_vol_1_3x",
                "spy_above_ma20",
                0.60,
                10,
            ),
            Config(
                "crash_repair_ma10_60_capped10",
                "break_big_bull_low",
                "spy_above_ma10",
                0.60,
                10,
            ),
        )

    return tuple(
        Config(
            f"downtrend_ma10_min_{int(min_ratio * 100)}",
            "break_big_bull_low_and_vol_1_3x",
            "spy_above_ma10",
            min_ratio,
            10,
        )
        for min_ratio in (0.55, 0.60, 0.65, 0.70, 0.75, 0.80)
    ) + (
        Config(
            "downtrend_ma20_min_65",
            "break_big_bull_low_and_vol_1_3x",
            "spy_above_ma20",
            0.65,
            10,
        ),
        Config(
            "downtrend_ma20_min_70",
            "break_big_bull_low_and_vol_1_3x",
            "spy_above_ma20",
            0.70,
            10,
        ),
        Config(
            "downtrend_ma10_min_70_penalty_exit",
            "penalty_adjusted_score_lt_20pct",
            "spy_above_ma10",
            0.70,
            10,
        ),
    )


def _exit_by_label():
    return {strategy.label: strategy for strategy in _exit_strategies()}


def _run_case(case: Case, watchlist: Path) -> pd.DataFrame:
    names = _load_us_symbols(watchlist)
    symbols = sorted(set(names) | {BENCHMARK})
    data = _download_range(symbols, case.start, case.end)
    if BENCHMARK not in data:
        raise RuntimeError(f"missing benchmark data: {BENCHMARK}")

    names = {symbol: name for symbol, name in names.items() if symbol in data}
    benchmark_ind = _indicators_for_frame(data[BENCHMARK])
    all_dates = sorted(data[BENCHMARK].index)
    dates = _date_slice(all_dates, case.start, case.end, 252)
    signal_data = {symbol: data[symbol] for symbol in names}
    signal_facts = _build_signal_facts(signal_data, benchmark_ind)
    signals = _signals_from_facts(signal_facts, DEFAULT_WEIGHTS)
    exits = _build_exit_rows(signal_data, data[BENCHMARK])
    benchmark_state = _benchmark_state(benchmark_ind)
    exit_strategies = _exit_by_label()

    rows: list[dict] = []
    for config in _configs(case):
        exit_strategy = exit_strategies[config.exit_label]
        active_curve, trades, holdings = _run_defensive_backtest(
            data=data,
            signals=signals,
            exits=exits,
            names=names,
            dates=dates,
            benchmark_state=benchmark_state,
            exit_strategy=exit_strategy,
            entry_gate=config.entry_gate,
            min_ratio=config.min_ratio,
            max_positions=config.max_positions,
            active_slot=config.active_slot,
        )
        summary = _summarize(config.label, dates, data, trades, holdings, active_curve)
        summary.update(_trade_stats(trades))
        summary.update(
            {
                "case": case.label,
                "regime": case.regime,
                "exit": config.exit_label,
                "entry_gate": config.entry_gate,
                "min_ratio": config.min_ratio,
                "max_positions": (
                    config.max_positions
                    if config.max_positions is not None
                    else "none"
                ),
                "active_slot": config.active_slot,
            }
        )
        rows.append(summary)
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--watchlist", default="data/watchlist.csv")
    parser.add_argument("--output-csv", default="results/backtests/us_bear_split_focused.csv")
    parser.add_argument("--top", type=int, default=12)
    args = parser.parse_args()

    frame = pd.concat(
        (_run_case(case, Path(args.watchlist)) for case in CASES), ignore_index=True
    ).sort_values(["case", "excess_pct"], ascending=[True, False])
    columns = [
        "case",
        "label",
        "exit",
        "entry_gate",
        "min_ratio",
        "max_positions",
        "active_return_pct",
        "benchmark_return_pct",
        "excess_pct",
        "max_drawdown_pct",
        "buys",
        "sells",
        "open_positions",
        "trade_win_rate_pct",
        "avg_trade_return_pct",
        "avg_hold_days",
    ]
    print(
        frame[columns]
        .groupby("case", group_keys=False)
        .head(args.top)
        .to_string(index=False)
    )
    if args.output_csv:
        output_path = Path(args.output_csv)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        frame.to_csv(output_path, index=False)


if __name__ == "__main__":
    main()
