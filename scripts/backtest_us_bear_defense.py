from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.backtest_us_exit_strategy import (  # noqa: E402
    ExitStrategy,
    _build_exit_rows,
    _exit_strategies,
    _portfolio_value_ffill,
    _strategy_sell_fraction,
    _trade_stats,
)
from scripts.backtest_us_strategy import (  # noqa: E402
    BENCHMARK,
    DEFAULT_WEIGHTS,
    INITIAL_CAPITAL,
    Holding,
    _build_signal_facts,
    _buy_benchmark,
    _date_slice,
    _download_range,
    _indicators_for_frame,
    _load_us_symbols,
    _open_price,
    _raise_cash_from_benchmark,
    _signals_from_facts,
    _summarize,
)


DEFAULT_START = "2022-01-03"
DEFAULT_END = "2022-12-30"
ENTRY_GATES = (
    "always",
    "spy_above_ma5",
    "spy_above_ma10",
    "spy_above_ma20",
    "spy_up_day",
    "spy_above_ma5_and_ma5_up",
    "spy_above_ma10_or_up_day",
    "spy_above_ma20_or_up_day",
    "spy_ret20_positive",
)
MIN_RATIOS = (0.50, 0.55, 0.60, 0.65, 0.70)
MAX_POSITIONS = (None, 5, 10, 15, 20)
SLOTS = (25_000.0, 50_000.0)
EXIT_LABELS = {
    "baseline_ma5",
    "break_big_bull_low",
    "break_big_bull_low_and_vol_1_3x",
    "market_break_ma10_score_lt_20pct",
    "ma5_and_market_break_ma10",
    "break_prev3_low",
    "break_prev5_low",
    "ma5_down_vol_1_3x",
    "ma5_down_vol_1_5x",
    "penalty_adjusted_score_lt_20pct",
}


def _benchmark_state(benchmark_ind: pd.DataFrame) -> dict[pd.Timestamp, dict]:
    state: dict[pd.Timestamp, dict] = {}
    for date, row in benchmark_ind.iterrows():
        state[date] = {
            "above_ma5": bool(pd.notna(row.ma5) and row.close > row.ma5),
            "above_ma10": bool(pd.notna(row.ma10) and row.close > row.ma10),
            "above_ma20": bool(pd.notna(row.ma20) and row.close > row.ma20),
            "up_day": bool(pd.notna(row.today_return) and row.today_return > 0),
            "ret20": None if pd.isna(row.return_20d) else float(row.return_20d),
            "ma5_up": bool(
                pd.notna(row.ma5) and pd.notna(row.prev_ma5) and row.ma5 > row.prev_ma5
            ),
        }
    return state


def _entry_gate_ok(
    gate: str, date: pd.Timestamp, benchmark_state: dict[pd.Timestamp, dict]
) -> bool:
    row = benchmark_state.get(date, {})
    if gate == "always":
        return True
    if gate == "spy_above_ma5":
        return bool(row.get("above_ma5"))
    if gate == "spy_above_ma10":
        return bool(row.get("above_ma10"))
    if gate == "spy_above_ma20":
        return bool(row.get("above_ma20"))
    if gate == "spy_up_day":
        return bool(row.get("up_day"))
    if gate == "spy_above_ma5_and_ma5_up":
        return bool(row.get("above_ma5") and row.get("ma5_up"))
    if gate == "spy_above_ma10_or_up_day":
        return bool(row.get("above_ma10") or row.get("up_day"))
    if gate == "spy_above_ma20_or_up_day":
        return bool(row.get("above_ma20") or row.get("up_day"))
    if gate == "spy_ret20_positive":
        return (row.get("ret20") or -1.0) > 0
    raise ValueError(f"unknown entry gate: {gate}")


def _run_defensive_backtest(
    *,
    data: dict[str, pd.DataFrame],
    signals: dict[pd.Timestamp, dict[str, dict]],
    exits: dict[pd.Timestamp, dict[str, dict]],
    names: dict[str, str],
    dates: list[pd.Timestamp],
    benchmark_state: dict[pd.Timestamp, dict],
    exit_strategy: ExitStrategy,
    entry_gate: str,
    min_ratio: float,
    max_positions: int | None,
    active_slot: float,
):
    cash = INITIAL_CAPITAL
    benchmark_shares, cash = _buy_benchmark(data, cash, dates[0])
    holdings: dict[str, Holding] = {}
    trades: list[dict] = []
    equity_curve: list[tuple[pd.Timestamp, float]] = []
    pending = signals.get(dates[0], {})
    pending_exits = exits.get(dates[0], {})
    pending_date = dates[0]

    for date in dates[1:]:
        today = signals.get(date, {})
        today_exits = exits.get(date, {})

        for symbol, fraction in _strategy_sell_fraction(
            exit_strategy, holdings, pending, pending_exits
        ).items():
            price = _open_price(data, symbol, date)
            if price is None:
                continue
            holding = holdings.pop(symbol)
            shares_to_sell = holding.shares * fraction
            proceeds = shares_to_sell * price
            cash += proceeds
            if fraction < 1.0:
                holdings[symbol] = Holding(
                    shares=holding.shares - shares_to_sell,
                    cost=holding.cost * (1.0 - fraction),
                )
            trades.append(
                {
                    "date": date,
                    "symbol": symbol,
                    "name": names.get(symbol, ""),
                    "action": "sell",
                    "amount": proceeds,
                    "ratio": pending.get(symbol, {}).get("ratio"),
                }
            )

        if _entry_gate_ok(entry_gate, pending_date, benchmark_state):
            candidates = [
                (symbol, row)
                for symbol, row in pending.items()
                if row["special"]
                and symbol not in holdings
                and row.get("ratio", 0.0) >= min_ratio
            ]
            candidates.sort(
                key=lambda item: (item[1]["ratio"], item[1]["score"]), reverse=True
            )
            for symbol, row in candidates:
                if max_positions is not None and len(holdings) >= max_positions:
                    break
                price = _open_price(data, symbol, date)
                if price is None or price <= 0:
                    continue
                benchmark_shares, cash = _raise_cash_from_benchmark(
                    data, benchmark_shares, cash, active_slot, date
                )
                spend = min(active_slot, cash)
                if spend <= 0:
                    continue
                holdings[symbol] = Holding(shares=spend / price, cost=spend)
                cash -= spend
                trades.append(
                    {
                        "date": date,
                        "symbol": symbol,
                        "name": names.get(symbol, ""),
                        "action": "buy",
                        "amount": spend,
                        "score": row["score"],
                        "max_score": row["max_score"],
                        "ratio": row["ratio"],
                    }
                )

        benchmark_add, cash = _buy_benchmark(data, cash, date)
        benchmark_shares += benchmark_add
        equity_curve.append(
            (date, _portfolio_value_ffill(data, holdings, benchmark_shares, cash, date))
        )
        pending = today
        pending_exits = today_exits
        pending_date = date

    return equity_curve, trades, holdings


def run_sweep(
    *,
    start: str,
    end: str,
    watchlist: Path,
) -> pd.DataFrame:
    names = _load_us_symbols(watchlist)
    symbols = sorted(set(names) | {BENCHMARK})
    data = _download_range(symbols, start, end)
    if BENCHMARK not in data:
        raise RuntimeError(f"missing benchmark data: {BENCHMARK}")
    names = {symbol: name for symbol, name in names.items() if symbol in data}
    benchmark_ind = _indicators_for_frame(data[BENCHMARK])
    all_dates = sorted(data[BENCHMARK].index)
    dates = _date_slice(all_dates, start, end, 252)
    signal_data = {symbol: data[symbol] for symbol in names}
    signal_facts = _build_signal_facts(signal_data, benchmark_ind)
    signals = _signals_from_facts(signal_facts, DEFAULT_WEIGHTS)
    exits = _build_exit_rows(signal_data, data[BENCHMARK])
    benchmark_state = _benchmark_state(benchmark_ind)
    exit_strategies = [
        strategy for strategy in _exit_strategies() if strategy.label in EXIT_LABELS
    ]
    exit_strategies.append(
        ExitStrategy("market_break_ma10_sell_lowest_50pct", "portfolio")
    )

    rows: list[dict] = []
    for exit_strategy in exit_strategies:
        for entry_gate in ENTRY_GATES:
            for min_ratio in MIN_RATIOS:
                for max_positions in MAX_POSITIONS:
                    for active_slot in SLOTS:
                        active_curve, trades, holdings = _run_defensive_backtest(
                            data=data,
                            signals=signals,
                            exits=exits,
                            names=names,
                            dates=dates,
                            benchmark_state=benchmark_state,
                            exit_strategy=exit_strategy,
                            entry_gate=entry_gate,
                            min_ratio=min_ratio,
                            max_positions=max_positions,
                            active_slot=active_slot,
                        )
                        summary = _summarize(
                            exit_strategy.label,
                            dates,
                            data,
                            trades,
                            holdings,
                            active_curve,
                        )
                        summary.update(_trade_stats(trades))
                        summary.update(
                            {
                                "case": "us_bear_2022_defense",
                                "exit": exit_strategy.label,
                                "entry_gate": entry_gate,
                                "min_ratio": min_ratio,
                                "max_positions": (
                                    max_positions if max_positions is not None else "none"
                                ),
                                "active_slot": active_slot,
                            }
                        )
                        rows.append(summary)

    return pd.DataFrame(rows).sort_values(
        ["excess_pct", "active_return_pct"], ascending=False
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default=DEFAULT_START)
    parser.add_argument("--end", default=DEFAULT_END)
    parser.add_argument("--watchlist", default="data/watchlist.csv")
    parser.add_argument("--output-csv")
    parser.add_argument("--top", type=int, default=20)
    args = parser.parse_args()

    frame = run_sweep(
        start=args.start,
        end=args.end,
        watchlist=Path(args.watchlist),
    )
    columns = [
        "exit",
        "entry_gate",
        "min_ratio",
        "max_positions",
        "active_slot",
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
    print(frame[columns].head(args.top).to_string(index=False))
    if args.output_csv:
        Path(args.output_csv).parent.mkdir(parents=True, exist_ok=True)
        frame.to_csv(args.output_csv, index=False)


if __name__ == "__main__":
    main()
