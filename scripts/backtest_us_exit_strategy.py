from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.backtest_us_strategy import (  # noqa: E402
    ACTIVE_SLOT,
    BENCHMARK,
    DEFAULT_WEIGHTS,
    INITIAL_CAPITAL,
    SPECIAL_MIN_SCORE_RATIO,
    Holding,
    _build_signal_facts,
    _buy_benchmark,
    _date_slice,
    _download,
    _download_range,
    _indicators_for_frame,
    _load_us_symbols,
    _open_price,
    _raise_cash_from_benchmark,
    _signals_from_facts,
    _summarize,
)


@dataclass(frozen=True)
class ExitStrategy:
    label: str
    kind: str
    score_threshold: float | None = None
    volume_threshold: float | None = None
    low_window: int | None = None
    ma_window: int | None = None
    penalty_threshold: float | None = None
    require_ma5_break: bool = False
    require_market_ma10_break: bool = False


def _exit_indicators_for_frame(df: pd.DataFrame) -> pd.DataFrame:
    close = df["Close"].astype(float)
    open_ = df["Open"].astype(float)
    low = df["Low"].astype(float)
    high = df["High"].astype(float)
    volume = df["Volume"].fillna(0).astype(float)
    out = pd.DataFrame(index=df.index)
    out["open"] = open_
    out["close"] = close
    out["high"] = high
    out["low"] = low
    out["prev_close"] = close.shift(1)
    out["ma5"] = close.rolling(5).mean()
    out["ma10"] = close.rolling(10).mean()
    out["ma20"] = close.rolling(20).mean()
    out["vol_ratio"] = volume / volume.rolling(20).mean()
    out["down_day"] = close < close.shift(1)
    out["prev2_low"] = low.shift(2)
    out["prev_3d_low"] = low.shift(1).rolling(3).min()
    out["prev_5d_low"] = low.shift(1).rolling(5).min()
    long_red = (
        (close > open_)
        & ((close / open_ - 1.0) >= 0.03)
        & (out["vol_ratio"] >= 1.5)
    )
    out["big_bull_low"] = low.where(long_red).ffill()
    return out


def _build_exit_rows(
    data: dict[str, pd.DataFrame], benchmark: pd.DataFrame
) -> dict[pd.Timestamp, dict[str, dict]]:
    rows_by_date: dict[pd.Timestamp, dict[str, dict]] = {}
    bench_exit = _exit_indicators_for_frame(benchmark)
    for symbol, df in data.items():
        ind = _exit_indicators_for_frame(df)
        for date, row in ind.iterrows():
            if date not in bench_exit.index:
                continue
            bench_row = bench_exit.loc[date]
            rows_by_date.setdefault(date, {})[symbol] = {
                "below_ma5": _below(row, "ma5"),
                "below_ma10": _below(row, "ma10"),
                "below_ma20": _below(row, "ma20"),
                "down_day": bool(row.down_day),
                "vol_ratio": _float_or_none(row.vol_ratio),
                "below_prev2_low": _close_below(row, "prev2_low"),
                "below_prev_3d_low": _close_below(row, "prev_3d_low"),
                "below_prev_5d_low": _close_below(row, "prev_5d_low"),
                "below_big_bull_low": _close_below(row, "big_bull_low"),
                "market_below_ma10": _below(bench_row, "ma10"),
            }
    return rows_by_date


def _below(row: pd.Series, field: str) -> bool:
    value = row.get(field)
    return not pd.isna(value) and float(row.close) < float(value)


def _close_below(row: pd.Series, field: str) -> bool:
    value = row.get(field)
    return not pd.isna(value) and float(row.close) < float(value)


def _float_or_none(value: float) -> float | None:
    if pd.isna(value):
        return None
    return float(value)


def _close_or_last_price(
    data: dict[str, pd.DataFrame], symbol: str, date: pd.Timestamp
) -> float | None:
    frame = data.get(symbol)
    if frame is None:
        return None
    if date in frame.index:
        value = frame.loc[date, "Close"]
        return None if pd.isna(value) else float(value)
    history = frame.loc[:date]
    if history.empty:
        return None
    value = history.iloc[-1]["Close"]
    return None if pd.isna(value) else float(value)


def _portfolio_value_ffill(
    data: dict[str, pd.DataFrame],
    holdings: dict[str, Holding],
    benchmark_shares: float,
    cash: float,
    date: pd.Timestamp,
) -> float:
    value = cash
    bench_close = _close_or_last_price(data, BENCHMARK, date)
    if bench_close is not None:
        value += benchmark_shares * bench_close
    for symbol, holding in holdings.items():
        close = _close_or_last_price(data, symbol, date)
        if close is not None:
            value += holding.shares * close
    return value


def _penalty_ratio(row: dict) -> float:
    penalty = 0.0
    if row["below_ma10"]:
        penalty += 0.08
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


def _sell_signal(strategy: ExitStrategy, signal: dict, exit_row: dict) -> bool:
    ratio = float(signal.get("ratio") or 0.0)
    below_ma5 = exit_row["below_ma5"]
    market_break = exit_row["market_below_ma10"]
    if strategy.require_ma5_break and not below_ma5:
        return False
    if strategy.require_market_ma10_break and not market_break:
        return False
    if strategy.kind == "ma5":
        return below_ma5
    if strategy.kind == "score":
        return below_ma5 and ratio < (strategy.score_threshold or 0.0)
    if strategy.kind == "volume":
        return (
            below_ma5
            and exit_row["down_day"]
            and (exit_row["vol_ratio"] or 0.0) >= (strategy.volume_threshold or 0.0)
        )
    if strategy.kind == "low":
        key = f"below_prev_{strategy.low_window}d_low"
        if strategy.low_window == 2:
            key = "below_prev2_low"
        return bool(exit_row[key])
    if strategy.kind == "ma":
        return bool(exit_row[f"below_ma{strategy.ma_window}"])
    if strategy.kind == "big_bull_low":
        if not exit_row["below_big_bull_low"]:
            return False
        if strategy.volume_threshold is None:
            return True
        return (exit_row["vol_ratio"] or 0.0) >= strategy.volume_threshold
    if strategy.kind == "market_ma10_score":
        return market_break and ratio < (strategy.score_threshold or 0.0)
    if strategy.kind == "penalty_score":
        adjusted = max(0.0, ratio - _penalty_ratio(exit_row))
        return adjusted < (strategy.penalty_threshold or 0.0)
    raise ValueError(f"unknown exit strategy kind: {strategy.kind}")


def _exit_strategies() -> list[ExitStrategy]:
    return [
        ExitStrategy("baseline_ma5", "ma5"),
        ExitStrategy("ma5_score_lt_10pct", "score", score_threshold=0.10),
        ExitStrategy("ma5_score_lt_20pct", "score", score_threshold=0.20),
        ExitStrategy("ma5_down_vol_1_3x", "volume", volume_threshold=1.3),
        ExitStrategy("ma5_down_vol_1_5x", "volume", volume_threshold=1.5),
        ExitStrategy("break_prev2_low", "low", low_window=2),
        ExitStrategy("break_prev3_low", "low", low_window=3),
        ExitStrategy("break_prev5_low", "low", low_window=5),
        ExitStrategy("ma5_and_break_prev2_low", "low", low_window=2, require_ma5_break=True),
        ExitStrategy("ma5_and_break_prev3_low", "low", low_window=3, require_ma5_break=True),
        ExitStrategy("ma5_and_break_prev5_low", "low", low_window=5, require_ma5_break=True),
        ExitStrategy("break_big_bull_low", "big_bull_low"),
        ExitStrategy(
            "break_big_bull_low_and_vol_1_3x",
            "big_bull_low",
            volume_threshold=1.3,
        ),
        ExitStrategy("break_ma10", "ma", ma_window=10),
        ExitStrategy("break_ma20", "ma", ma_window=20),
        ExitStrategy(
            "ma5_and_market_break_ma10",
            "ma5",
            require_market_ma10_break=True,
        ),
        ExitStrategy(
            "market_break_ma10_score_lt_20pct",
            "market_ma10_score",
            score_threshold=0.20,
        ),
        ExitStrategy(
            "penalty_adjusted_score_lt_10pct",
            "penalty_score",
            penalty_threshold=0.10,
        ),
        ExitStrategy(
            "penalty_adjusted_score_lt_20pct",
            "penalty_score",
            penalty_threshold=0.20,
        ),
    ]


def _strategy_sell_fraction(
    strategy: ExitStrategy,
    holdings: dict[str, Holding],
    pending: dict[str, dict],
    pending_exits: dict[str, dict],
) -> dict[str, float]:
    if strategy.label == "market_break_ma10_sell_lowest_50pct":
        symbols = [
            symbol
            for symbol in holdings
            if pending_exits.get(symbol, {}).get("market_below_ma10")
        ]
        symbols.sort(key=lambda symbol: pending.get(symbol, {}).get("ratio", 0.0))
        sell_count = math.ceil(len(symbols) / 2)
        return {symbol: 1.0 for symbol in symbols[:sell_count]}
    return {
        symbol: 1.0
        for symbol in holdings
        if symbol in pending_exits
        and _sell_signal(strategy, pending.get(symbol, {}), pending_exits[symbol])
    }


def run_exit_backtest(
    data: dict[str, pd.DataFrame],
    signals: dict[pd.Timestamp, dict[str, dict]],
    exits: dict[pd.Timestamp, dict[str, dict]],
    names: dict[str, str],
    dates: list[pd.Timestamp],
    strategy: ExitStrategy,
):
    cash = INITIAL_CAPITAL
    benchmark_shares, cash = _buy_benchmark(data, cash, dates[0])
    holdings: dict[str, Holding] = {}
    trades: list[dict] = []
    equity_curve: list[tuple[pd.Timestamp, float]] = []
    pending = signals.get(dates[0], {})
    pending_exits = exits.get(dates[0], {})

    for date in dates[1:]:
        today = signals.get(date, {})
        today_exits = exits.get(date, {})

        for symbol, fraction in _strategy_sell_fraction(
            strategy, holdings, pending, pending_exits
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

        candidates = [
            (symbol, row)
            for symbol, row in pending.items()
            if row["special"] and symbol not in holdings
        ]
        candidates.sort(key=lambda item: (item[1]["ratio"], item[1]["score"]), reverse=True)
        for symbol, row in candidates:
            price = _open_price(data, symbol, date)
            if price is None or price <= 0:
                continue
            benchmark_shares, cash = _raise_cash_from_benchmark(
                data, benchmark_shares, cash, ACTIVE_SLOT, date
            )
            spend = min(ACTIVE_SLOT, cash)
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

    return equity_curve, trades, holdings


def _trade_stats(trades: list[dict]) -> dict:
    buys_by_symbol: dict[str, list[dict]] = {}
    returns: list[float] = []
    hold_days: list[int] = []
    for trade in trades:
        symbol = trade["symbol"]
        if trade["action"] == "buy":
            buys_by_symbol.setdefault(symbol, []).append(trade)
            continue
        buys = buys_by_symbol.get(symbol)
        if not buys:
            continue
        buy = buys.pop(0)
        if buy["amount"] <= 0:
            continue
        returns.append(trade["amount"] / buy["amount"] - 1.0)
        hold_days.append((trade["date"] - buy["date"]).days)
    if not returns:
        return {
            "closed_trades": 0,
            "trade_win_rate_pct": 0.0,
            "avg_trade_return_pct": 0.0,
            "median_trade_return_pct": 0.0,
            "avg_hold_days": 0.0,
        }
    series = pd.Series(returns, dtype=float)
    return {
        "closed_trades": len(returns),
        "trade_win_rate_pct": float((series > 0).mean() * 100.0),
        "avg_trade_return_pct": float(series.mean() * 100.0),
        "median_trade_return_pct": float(series.median() * 100.0),
        "avg_hold_days": float(pd.Series(hold_days, dtype=float).mean()),
    }


def _run_case(
    label: str,
    start: str | None,
    end: str | None,
    period: str,
    days: int,
    watchlist: Path,
) -> pd.DataFrame:
    names = _load_us_symbols(watchlist)
    symbols = sorted(set(names) | {BENCHMARK})
    data = _download_range(symbols, start, end) if start or end else _download(symbols, period)
    if BENCHMARK not in data:
        raise RuntimeError(f"missing benchmark data: {BENCHMARK}")
    names = {symbol: name for symbol, name in names.items() if symbol in data}
    benchmark_ind = _indicators_for_frame(data[BENCHMARK])
    all_dates = sorted(data[BENCHMARK].index)
    backtest_dates = _date_slice(all_dates, start, end, days)
    signal_data = {symbol: data[symbol] for symbol in names}
    signal_facts = _build_signal_facts(signal_data, benchmark_ind)
    signals = _signals_from_facts(signal_facts, DEFAULT_WEIGHTS)
    exits = _build_exit_rows(signal_data, data[BENCHMARK])
    strategies = [
        *_exit_strategies(),
        ExitStrategy("market_break_ma10_sell_lowest_50pct", "portfolio"),
    ]
    rows = []
    for strategy in strategies:
        active_curve, trades, holdings = run_exit_backtest(
            data, signals, exits, names, backtest_dates, strategy
        )
        summary = _summarize(
            strategy.label, backtest_dates, data, trades, holdings, active_curve
        )
        summary["case"] = label
        summary["entry"] = (
            f"new_above_all_ma_and_score>={SPECIAL_MIN_SCORE_RATIO:.0%}"
        )
        summary.update(_trade_stats(trades))
        rows.append(summary)
    return pd.DataFrame(rows)


def _preset_cases() -> list[tuple[str, str, str]]:
    return [
        ("us_bear_2022", "2022-01-03", "2022-12-30"),
        ("us_choppy_2021", "2021-01-04", "2021-12-31"),
        ("us_bull_2025_2026", "2025-01-02", "2026-05-28"),
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--period", default="3y")
    parser.add_argument("--days", type=int, default=252)
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--label", default="custom")
    parser.add_argument("--watchlist", default="data/watchlist.csv")
    parser.add_argument("--preset", choices=["all"], help="run the three US market cases")
    parser.add_argument("--output-csv")
    parser.add_argument("--top", type=int, default=10)
    args = parser.parse_args()

    watchlist = Path(args.watchlist)
    if args.preset == "all":
        frames = [
            _run_case(label, start, end, args.period, args.days, watchlist)
            for label, start, end in _preset_cases()
        ]
        frame = pd.concat(frames, ignore_index=True)
    else:
        frame = _run_case(
            args.label, args.start, args.end, args.period, args.days, watchlist
        )
    frame = frame.sort_values(["case", "excess_pct"], ascending=[True, False])
    columns = [
        "case",
        "label",
        "active_return_pct",
        "benchmark_return_pct",
        "excess_pct",
        "max_drawdown_pct",
        "buys",
        "sells",
        "open_positions",
        "trade_win_rate_pct",
        "avg_trade_return_pct",
        "median_trade_return_pct",
        "avg_hold_days",
    ]
    print(frame[columns].groupby("case", group_keys=False).head(args.top).to_string(index=False))
    if args.output_csv:
        Path(args.output_csv).parent.mkdir(parents=True, exist_ok=True)
        frame.to_csv(args.output_csv, index=False)


if __name__ == "__main__":
    main()
