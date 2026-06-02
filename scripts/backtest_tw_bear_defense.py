from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from screener.fetch import AnalystSnapshot  # noqa: E402
from screener.indicators import IndicatorSnapshot  # noqa: E402
from screener.score import score, strategy_rule_weights  # noqa: E402


INITIAL_CAPITAL = 1_000_000.0
ACTIVE_SLOT = 50_000.0
BENCHMARK = "0050.TW"
SPECIAL_MIN_SCORE_RATIO = 0.50
BEAR_DOWNTREND_MIN_SCORE_RATIO = 0.70
VOLUME_EXIT_RATIO = 1.3


@dataclass(frozen=True)
class Case:
    label: str
    strategy: str
    start: str
    end: str
    baseline_min_ratio: float
    baseline_exit: str


@dataclass(frozen=True)
class Holding:
    shares: float
    cost: float


@dataclass(frozen=True)
class ExitStrategy:
    label: str
    kind: str
    score_threshold: float | None = None
    volume_threshold: float | None = None
    low_window: int | None = None
    ma_window: int | None = None
    require_ma5_break: bool = False
    require_market_ma10_break: bool = False


CASES = (
    Case(
        "tw_bear_crash_2020",
        "bear_crash",
        "2020-01-02",
        "2020-12-31",
        SPECIAL_MIN_SCORE_RATIO,
        "break_prev5_low",
    ),
    Case(
        "tw_bear_downtrend_2022",
        "bear_downtrend",
        "2022-01-03",
        "2022-12-30",
        BEAR_DOWNTREND_MIN_SCORE_RATIO,
        "penalty_adjusted_score_lt_20pct",
    ),
)

ENTRY_GATES = (
    "always",
    "benchmark_above_ma5",
    "benchmark_above_ma10",
    "benchmark_above_ma20",
    "benchmark_up_day",
    "benchmark_above_ma5_and_ma5_up",
    "benchmark_above_ma10_or_up_day",
    "benchmark_ret20_positive",
)
MIN_RATIOS = (0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80)
MAX_POSITIONS = (None, 5, 10, 15, 20)
SLOTS = (25_000.0, 50_000.0)


def _exit_strategies() -> list[ExitStrategy]:
    return [
        ExitStrategy("baseline_ma5", "ma5"),
        ExitStrategy("break_prev5_low", "low", low_window=5),
        ExitStrategy("break_big_bull_low", "big_bull_low"),
        ExitStrategy(
            "break_big_bull_low_and_vol_1_3x",
            "big_bull_low",
            volume_threshold=VOLUME_EXIT_RATIO,
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
            "penalty_adjusted_score_lt_20pct",
            "penalty_score",
            score_threshold=0.20,
        ),
    ]


def _load_tw_symbols(path: Path) -> dict[str, str]:
    rows = pd.read_csv(path)
    symbols: dict[str, str] = {}
    for row in rows.itertuples(index=False):
        symbol = str(row.symbol)
        if row.market != "tw" or symbol.startswith("00"):
            continue
        symbols[symbol] = str(row.name)
    return symbols


def _download_range(
    symbols: list[str], start: str, end: str, warmup_days: int = 420
) -> dict[str, pd.DataFrame]:
    start_ts = pd.Timestamp(start) - pd.Timedelta(days=warmup_days)
    end_ts = pd.Timestamp(end) + pd.Timedelta(days=5)
    out: dict[str, pd.DataFrame] = {}
    for idx in range(0, len(symbols), 40):
        chunk = symbols[idx : idx + 40]
        data = yf.download(
            " ".join(chunk),
            start=start_ts.date().isoformat(),
            end=end_ts.date().isoformat(),
            interval="1d",
            auto_adjust=True,
            progress=False,
            threads=True,
            group_by="ticker",
        )
        for symbol in chunk:
            try:
                frame = data[symbol].copy() if len(chunk) > 1 else data.copy()
            except KeyError:
                continue
            frame = frame.dropna(subset=["Open", "High", "Low", "Close"])
            if not frame.empty:
                frame.index = pd.to_datetime(frame.index).tz_localize(None).normalize()
                out[symbol] = frame
    return out


def _indicators_for_frame(df: pd.DataFrame) -> pd.DataFrame:
    close = df["Close"].astype(float)
    open_ = df["Open"].astype(float)
    low = df["Low"].astype(float)
    volume = df["Volume"].fillna(0).astype(float)
    prev_close = close.shift(1)
    out = pd.DataFrame(index=df.index)
    out["open"] = open_
    out["close"] = close
    out["low"] = low
    out["prev_close"] = prev_close
    out["today_return"] = close / prev_close - 1.0
    out["return_20d"] = close / close.shift(20) - 1.0
    for window in (5, 10, 20, 240):
        out[f"ma{window}"] = close.rolling(window).mean()
        out[f"prev_ma{window}"] = out[f"ma{window}"].shift(1)
    out["volume"] = volume
    out["vol_ratio"] = volume / volume.rolling(20).mean()
    out["high_5d"] = close.rolling(5).max()
    out["high_20d"] = close.rolling(20).max()
    out["prev_high_20d"] = close.shift(1).rolling(20).max()
    out["prev2_low"] = low.shift(2)
    out["prev_3d_low"] = low.shift(1).rolling(3).min()
    out["prev_5d_low"] = low.shift(1).rolling(5).min()
    long_bull = (
        (close > open_)
        & ((close / open_ - 1.0) >= 0.03)
        & (out["vol_ratio"] >= 1.5)
    )
    out["big_bull_low"] = low.where(long_bull).ffill()
    out["pct_of_high_20d"] = close / out["high_20d"]
    direction = close.diff().fillna(0).apply(
        lambda value: 1 if value > 0 else -1 if value < 0 else 0
    )
    obv = (direction * volume).cumsum()
    out["obv"] = obv
    out["obv_ma5"] = obv.rolling(5).mean()
    out["obv_ma20"] = obv.rolling(20).mean()
    macd = close.ewm(span=12, adjust=False).mean() - close.ewm(
        span=26, adjust=False
    ).mean()
    signal = macd.ewm(span=9, adjust=False).mean()
    out["macd"] = macd
    out["macd_signal"] = signal
    out["macd_hist"] = macd - signal
    out["macd_prev"] = macd.shift(1)
    out["macd_signal_prev"] = signal.shift(1)
    return out


def _float_or_none(value: float) -> float | None:
    return None if pd.isna(value) else float(value)


def _snapshot(row: pd.Series) -> IndicatorSnapshot:
    return IndicatorSnapshot(
        close=float(row.close),
        prev_close=_float_or_none(row.prev_close),
        today_return=_float_or_none(row.today_return),
        return_20d=_float_or_none(row.return_20d),
        ma5=_float_or_none(row.ma5),
        ma10=_float_or_none(row.ma10),
        ma20=_float_or_none(row.ma20),
        ma240=_float_or_none(row.ma240),
        prev_ma5=_float_or_none(row.prev_ma5),
        prev_ma10=_float_or_none(row.prev_ma10),
        prev_ma20=_float_or_none(row.prev_ma20),
        prev_ma240=_float_or_none(row.prev_ma240),
        volume=float(row.volume),
        vol_ratio=_float_or_none(row.vol_ratio),
        high_5d=_float_or_none(row.high_5d),
        high_20d=_float_or_none(row.high_20d),
        prev_high_20d=_float_or_none(row.prev_high_20d),
        prev2_low=_float_or_none(row.prev2_low),
        prev_3d_low=_float_or_none(row.prev_3d_low),
        prev_5d_low=_float_or_none(row.prev_5d_low),
        big_bull_low=_float_or_none(row.big_bull_low),
        pct_of_high_20d=_float_or_none(row.pct_of_high_20d),
        obv=_float_or_none(row.obv),
        obv_ma5=_float_or_none(row.obv_ma5),
        obv_ma20=_float_or_none(row.obv_ma20),
        macd=_float_or_none(row.macd),
        macd_signal=_float_or_none(row.macd_signal),
        macd_hist=_float_or_none(row.macd_hist),
        macd_prev=_float_or_none(row.macd_prev),
        macd_signal_prev=_float_or_none(row.macd_signal_prev),
    )


def _above_all(snapshot: IndicatorSnapshot) -> bool:
    return all(
        value is not None and snapshot.close > value
        for value in (snapshot.ma5, snapshot.ma10, snapshot.ma20, snapshot.ma240)
    )


def _was_above_all(snapshot: IndicatorSnapshot) -> bool:
    return snapshot.prev_close is not None and all(
        value is not None and snapshot.prev_close > value
        for value in (
            snapshot.prev_ma5,
            snapshot.prev_ma10,
            snapshot.prev_ma20,
            snapshot.prev_ma240,
        )
    )


def _rule_key(rule: str) -> str | None:
    if rule.startswith("今日站上全均線"):
        return "above_all"
    if rule.startswith("20日收盤新高"):
        return "new_high"
    if rule.startswith("短線趨勢確認"):
        return "trend"
    if rule.startswith("放量上漲"):
        return "volume"
    if rule.startswith("OBV"):
        return "obv"
    if rule.startswith("相對強度"):
        return "relative_strength"
    if rule.startswith("MACD"):
        return "macd"
    if rule.startswith("法人目標價"):
        return "target"
    if rule.startswith("強勢板塊"):
        return "sector"
    if rule.startswith("投信"):
        return "trust"
    if rule.startswith("外資"):
        return "foreign"
    return None


def _score_with_weights(result, weights: dict[str, float]) -> tuple[float, float, float]:
    raw = 0.0
    max_score = 0.0
    penalty_ratio = 0.0
    for reason in result.reasons:
        if reason.rule.startswith("賣壓扣分"):
            if reason.passed:
                penalty_ratio += abs(reason.score or 0.0)
            continue
        key = _rule_key(reason.rule)
        if key is None or key not in weights:
            continue
        weight = weights[key]
        max_score += weight
        if reason.passed:
            if reason.score is None:
                raw += weight
            elif reason.weight:
                raw += weight * reason.score / reason.weight
    total = max(0.0, raw - max_score * penalty_ratio)
    return total, max_score, penalty_ratio


def _benchmark_state(benchmark_ind: pd.DataFrame) -> dict[pd.Timestamp, dict]:
    state: dict[pd.Timestamp, dict] = {}
    for date, row in benchmark_ind.iterrows():
        state[date] = {
            "above_ma5": bool(pd.notna(row.ma5) and row.close > row.ma5),
            "above_ma10": bool(pd.notna(row.ma10) and row.close > row.ma10),
            "above_ma20": bool(pd.notna(row.ma20) and row.close > row.ma20),
            "up_day": bool(pd.notna(row.today_return) and row.today_return > 0),
            "ret20": _float_or_none(row.return_20d),
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
    if gate == "benchmark_above_ma5":
        return bool(row.get("above_ma5"))
    if gate == "benchmark_above_ma10":
        return bool(row.get("above_ma10"))
    if gate == "benchmark_above_ma20":
        return bool(row.get("above_ma20"))
    if gate == "benchmark_up_day":
        return bool(row.get("up_day"))
    if gate == "benchmark_above_ma5_and_ma5_up":
        return bool(row.get("above_ma5") and row.get("ma5_up"))
    if gate == "benchmark_above_ma10_or_up_day":
        return bool(row.get("above_ma10") or row.get("up_day"))
    if gate == "benchmark_ret20_positive":
        return (row.get("ret20") or -1.0) > 0
    raise ValueError(f"unknown entry gate: {gate}")


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
    if strategy.kind == "low":
        return bool(exit_row[f"below_prev_{strategy.low_window}d_low"])
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
        return signal.get("penalty_ratio", 0.0) > 0 and ratio < (
            strategy.score_threshold or 0.0
        )
    raise ValueError(f"unknown exit strategy kind: {strategy.kind}")


def _build_exit_rows(
    data: dict[str, pd.DataFrame], benchmark_ind: pd.DataFrame
) -> dict[pd.Timestamp, dict[str, dict]]:
    rows_by_date: dict[pd.Timestamp, dict[str, dict]] = {}
    for symbol, df in data.items():
        ind = _indicators_for_frame(df)
        for date, row in ind.iterrows():
            if date not in benchmark_ind.index:
                continue
            bench_row = benchmark_ind.loc[date]
            rows_by_date.setdefault(date, {})[symbol] = {
                "below_ma5": pd.notna(row.ma5) and row.close < row.ma5,
                "below_ma10": pd.notna(row.ma10) and row.close < row.ma10,
                "below_ma20": pd.notna(row.ma20) and row.close < row.ma20,
                "vol_ratio": _float_or_none(row.vol_ratio),
                "below_prev_5d_low": (
                    pd.notna(row.prev_5d_low) and row.close < row.prev_5d_low
                ),
                "below_big_bull_low": (
                    pd.notna(row.big_bull_low) and row.close < row.big_bull_low
                ),
                "market_below_ma10": (
                    pd.notna(bench_row.ma10) and bench_row.close < bench_row.ma10
                ),
            }
    return rows_by_date


def _build_signals(
    *,
    data: dict[str, pd.DataFrame],
    names: dict[str, str],
    benchmark_ind: pd.DataFrame,
    strategy: str,
) -> dict[pd.Timestamp, dict[str, dict]]:
    weights = strategy_rule_weights(strategy, "tw")
    analyst = AnalystSnapshot(target_mean=None, rating=None, rating_score=None)
    signals: dict[pd.Timestamp, dict[str, dict]] = {}
    for symbol in names:
        ind = _indicators_for_frame(data[symbol])
        for date, row in ind.iterrows():
            if date not in benchmark_ind.index:
                continue
            snapshot = _snapshot(row)
            if snapshot.ma240 is None or snapshot.prev_ma240 is None:
                continue
            bench_row = benchmark_ind.loc[date]
            market_below_ma10 = (
                pd.notna(bench_row.ma10) and bench_row.close < bench_row.ma10
            )
            result = score(
                "tw",
                snapshot,
                analyst,
                prev_target_mean=None,
                chip=None,
                benchmark_return_20d=_float_or_none(bench_row.return_20d),
                strategy=strategy,
                market_below_ma10=market_below_ma10,
            )
            total, max_score, penalty_ratio = _score_with_weights(result, weights)
            ratio = total / max_score if max_score else 0.0
            signals.setdefault(date, {})[symbol] = {
                "score": total,
                "max_score": max_score,
                "ratio": ratio,
                "penalty_ratio": penalty_ratio,
                "special_base": _above_all(snapshot) and not _was_above_all(snapshot),
            }
    return signals


def _open_price(
    data: dict[str, pd.DataFrame], symbol: str, date: pd.Timestamp
) -> float | None:
    if symbol not in data or date not in data[symbol].index:
        return None
    value = data[symbol].loc[date, "Open"]
    return None if pd.isna(value) else float(value)


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


def _buy_benchmark(
    data: dict[str, pd.DataFrame], cash: float, date: pd.Timestamp
) -> tuple[float, float]:
    price = _open_price(data, BENCHMARK, date)
    if price is None or price <= 0 or cash <= 0:
        return 0.0, cash
    return cash / price, 0.0


def _raise_cash_from_benchmark(
    data: dict[str, pd.DataFrame],
    benchmark_shares: float,
    cash: float,
    needed: float,
    date: pd.Timestamp,
) -> tuple[float, float]:
    if cash >= needed:
        return benchmark_shares, cash
    price = _open_price(data, BENCHMARK, date)
    if price is None or price <= 0:
        return benchmark_shares, cash
    shares_to_sell = min(benchmark_shares, (needed - cash) / price)
    return benchmark_shares - shares_to_sell, cash + shares_to_sell * price


def _portfolio_value(
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


def _sell_fractions(
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


def _run_backtest(
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
) -> tuple[list[tuple[pd.Timestamp, float]], list[dict], dict[str, Holding]]:
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

        for symbol, fraction in _sell_fractions(
            exit_strategy, holdings, pending, pending_exits
        ).items():
            price = _open_price(data, symbol, date)
            if price is None:
                continue
            holding = holdings.pop(symbol)
            shares_to_sell = holding.shares * fraction
            proceeds = shares_to_sell * price
            cash += proceeds
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
                if row["special_base"]
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
            (date, _portfolio_value(data, holdings, benchmark_shares, cash, date))
        )
        pending = today
        pending_exits = today_exits
        pending_date = date

    return equity_curve, trades, holdings


def _summarize(
    label: str,
    dates: list[pd.Timestamp],
    data: dict[str, pd.DataFrame],
    trades: list[dict],
    holdings: dict[str, Holding],
    active_curve: list[tuple[pd.Timestamp, float]],
) -> dict:
    start_date = dates[1]
    end_date = dates[-1]
    bench_start = _open_price(data, BENCHMARK, start_date)
    bench_end = _close_or_last_price(data, BENCHMARK, end_date)
    if bench_start is None or bench_end is None:
        raise RuntimeError("missing benchmark start/end prices")
    benchmark_final = INITIAL_CAPITAL / bench_start * bench_end
    active_final = active_curve[-1][1]
    values = pd.Series(
        [value for _, value in active_curve],
        index=[date for date, _ in active_curve],
        dtype=float,
    )
    drawdown = values / values.cummax() - 1.0
    return {
        "label": label,
        "start": start_date.date().isoformat(),
        "end": end_date.date().isoformat(),
        "trading_days": len(dates) - 1,
        "benchmark_final": benchmark_final,
        "benchmark_return_pct": (benchmark_final / INITIAL_CAPITAL - 1) * 100,
        "active_final": active_final,
        "active_return_pct": (active_final / INITIAL_CAPITAL - 1) * 100,
        "excess_pct": (active_final / benchmark_final - 1) * 100,
        "max_drawdown_pct": drawdown.min() * 100,
        "buys": sum(1 for trade in trades if trade["action"] == "buy"),
        "sells": sum(1 for trade in trades if trade["action"] == "sell"),
        "open_positions": len(holdings),
    }


def _date_slice(
    benchmark_dates: list[pd.Timestamp], start: str, end: str
) -> list[pd.Timestamp]:
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)
    dates = [date for date in benchmark_dates if start_ts <= date <= end_ts]
    prev_dates = [date for date in benchmark_dates if date < dates[0]]
    if prev_dates:
        dates = [prev_dates[-1], *dates]
    return dates


def _run_case(case: Case, watchlist: Path) -> pd.DataFrame:
    names = _load_tw_symbols(watchlist)
    symbols = sorted(set(names) | {BENCHMARK})
    data = _download_range(symbols, case.start, case.end)
    if BENCHMARK not in data:
        raise RuntimeError(f"missing benchmark data: {BENCHMARK}")
    names = {symbol: name for symbol, name in names.items() if symbol in data}
    benchmark_ind = _indicators_for_frame(data[BENCHMARK])
    all_dates = sorted(data[BENCHMARK].index)
    dates = _date_slice(all_dates, case.start, case.end)
    signal_data = {symbol: data[symbol] for symbol in names}
    signals = _build_signals(
        data=signal_data,
        names=names,
        benchmark_ind=benchmark_ind,
        strategy=case.strategy,
    )
    exits = _build_exit_rows(signal_data, benchmark_ind)
    benchmark_state = _benchmark_state(benchmark_ind)
    exit_strategies = _exit_strategies()
    exit_strategies.append(
        ExitStrategy("market_break_ma10_sell_lowest_50pct", "portfolio")
    )

    rows: list[dict] = []
    for exit_strategy in exit_strategies:
        for entry_gate in ENTRY_GATES:
            for min_ratio in MIN_RATIOS:
                for max_positions in MAX_POSITIONS:
                    for active_slot in SLOTS:
                        active_curve, trades, holdings = _run_backtest(
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
                        summary.update(
                            {
                                "case": case.label,
                                "strategy": case.strategy,
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

    return pd.DataFrame(rows)


def _focused_configs(case: Case) -> list[dict]:
    return [
        {
            "label": "current_rule",
            "exit": case.baseline_exit,
            "entry_gate": "always",
            "min_ratio": case.baseline_min_ratio,
            "max_positions": None,
            "active_slot": ACTIVE_SLOT,
        },
        {
            "label": "market_gate_current_exit",
            "exit": case.baseline_exit,
            "entry_gate": "benchmark_above_ma10",
            "min_ratio": case.baseline_min_ratio,
            "max_positions": None,
            "active_slot": ACTIVE_SLOT,
        },
        {
            "label": "market_gate_limit10_current_exit",
            "exit": case.baseline_exit,
            "entry_gate": "benchmark_above_ma10",
            "min_ratio": case.baseline_min_ratio,
            "max_positions": 10,
            "active_slot": ACTIVE_SLOT,
        },
        {
            "label": "market_gate_limit10_slot25_current_exit",
            "exit": case.baseline_exit,
            "entry_gate": "benchmark_above_ma10",
            "min_ratio": case.baseline_min_ratio,
            "max_positions": 10,
            "active_slot": 25_000.0,
        },
        {
            "label": "us_style_defensive_55",
            "exit": "break_big_bull_low_and_vol_1_3x",
            "entry_gate": "benchmark_above_ma10",
            "min_ratio": 0.55,
            "max_positions": 10,
            "active_slot": ACTIVE_SLOT,
        },
        {
            "label": "us_style_defensive_case_min",
            "exit": "break_big_bull_low_and_vol_1_3x",
            "entry_gate": "benchmark_above_ma10",
            "min_ratio": case.baseline_min_ratio,
            "max_positions": 10,
            "active_slot": ACTIVE_SLOT,
        },
        {
            "label": "market_gate_limit10_penalty20_exit",
            "exit": "penalty_adjusted_score_lt_20pct",
            "entry_gate": "benchmark_above_ma10",
            "min_ratio": case.baseline_min_ratio,
            "max_positions": 10,
            "active_slot": ACTIVE_SLOT,
        },
    ]


def _run_focused_case(case: Case, watchlist: Path) -> pd.DataFrame:
    names = _load_tw_symbols(watchlist)
    symbols = sorted(set(names) | {BENCHMARK})
    data = _download_range(symbols, case.start, case.end)
    if BENCHMARK not in data:
        raise RuntimeError(f"missing benchmark data: {BENCHMARK}")
    names = {symbol: name for symbol, name in names.items() if symbol in data}
    benchmark_ind = _indicators_for_frame(data[BENCHMARK])
    all_dates = sorted(data[BENCHMARK].index)
    dates = _date_slice(all_dates, case.start, case.end)
    signal_data = {symbol: data[symbol] for symbol in names}
    signals = _build_signals(
        data=signal_data,
        names=names,
        benchmark_ind=benchmark_ind,
        strategy=case.strategy,
    )
    exits = _build_exit_rows(signal_data, benchmark_ind)
    benchmark_state = _benchmark_state(benchmark_ind)
    exit_by_label = {strategy.label: strategy for strategy in _exit_strategies()}

    rows: list[dict] = []
    for config in _focused_configs(case):
        exit_strategy = exit_by_label[config["exit"]]
        active_curve, trades, holdings = _run_backtest(
            data=data,
            signals=signals,
            exits=exits,
            names=names,
            dates=dates,
            benchmark_state=benchmark_state,
            exit_strategy=exit_strategy,
            entry_gate=config["entry_gate"],
            min_ratio=config["min_ratio"],
            max_positions=config["max_positions"],
            active_slot=config["active_slot"],
        )
        summary = _summarize(
            config["label"],
            dates,
            data,
            trades,
            holdings,
            active_curve,
        )
        summary.update(
            {
                "case": case.label,
                "strategy": case.strategy,
                "exit": exit_strategy.label,
                "entry_gate": config["entry_gate"],
                "min_ratio": config["min_ratio"],
                "max_positions": (
                    config["max_positions"]
                    if config["max_positions"] is not None
                    else "none"
                ),
                "active_slot": config["active_slot"],
            }
        )
        rows.append(summary)
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--watchlist", default="data/watchlist.csv")
    parser.add_argument("--output-csv")
    parser.add_argument("--top", type=int, default=15)
    parser.add_argument("--focused", action="store_true")
    args = parser.parse_args()

    watchlist = Path(args.watchlist)
    runner = _run_focused_case if args.focused else _run_case
    frame = pd.concat((runner(case, watchlist) for case in CASES), ignore_index=True)
    frame = frame.sort_values(["case", "excess_pct"], ascending=[True, False])
    columns = [
        "case",
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
    ]
    print(
        frame[columns]
        .groupby("case", group_keys=False)
        .head(args.top)
        .to_string(index=False)
    )
    if args.output_csv:
        Path(args.output_csv).parent.mkdir(parents=True, exist_ok=True)
        frame.to_csv(args.output_csv, index=False)


if __name__ == "__main__":
    main()
