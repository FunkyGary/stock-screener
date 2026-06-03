from __future__ import annotations

import argparse
import itertools
import sys
from dataclasses import dataclass
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from screener.fetch import AnalystSnapshot  # noqa: E402
from screener.indicators import IndicatorSnapshot  # noqa: E402
from screener.score import score  # noqa: E402


INITIAL_CAPITAL = 1_000_000.0
ACTIVE_SLOT = 50_000.0
BENCHMARK = "0050.TW"
SPECIAL_MIN_SCORE_RATIO = 0.50
DEFAULT_WEIGHTS = {
    "above_all": 3.0,
    "new_high": 1.5,
    "trend": 1.5,
    "volume": 1.5,
    "obv": 1.0,
    "relative_strength": 2.0,
    "macd": 1.5,
    "target": 2.0,
    "trust": 2.0,
    "foreign": 1.0,
}
WEIGHT_KEYS = tuple(DEFAULT_WEIGHTS)
SWEEP_MULTIPLIERS = (0.5, 1.0, 1.5)

_WORKER_DATA: dict[str, pd.DataFrame] = {}
_WORKER_BASE_SIGNALS: dict[pd.Timestamp, dict[str, dict]] = {}
_WORKER_NAMES: dict[str, str] = {}
_WORKER_DATES: list[pd.Timestamp] = []
_WORKER_ENTRY_RULE = "newly_above_all"


@dataclass
class Holding:
    shares: float
    cost: float


def _load_tw_symbols(path: Path) -> dict[str, str]:
    rows = pd.read_csv(path)
    symbols: dict[str, str] = {}
    for row in rows.itertuples(index=False):
        symbol = str(row.symbol)
        if row.market != "tw":
            continue
        if symbol[:2] == "00":
            continue
        symbols[symbol] = str(row.name)
    return symbols


def _download(symbols: list[str], period: str) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    for idx in range(0, len(symbols), 40):
        chunk = symbols[idx : idx + 40]
        data = yf.download(
            " ".join(chunk),
            period=period,
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
            frame = frame.dropna(subset=["Open", "Close"])
            if not frame.empty:
                frame.index = pd.to_datetime(frame.index).tz_localize(None).normalize()
                out[symbol] = frame
    return out


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
            frame = frame.dropna(subset=["Open", "Close"])
            if not frame.empty:
                frame.index = pd.to_datetime(frame.index).tz_localize(None).normalize()
                out[symbol] = frame
    return out


def _indicators_for_frame(df: pd.DataFrame) -> pd.DataFrame:
    close = df["Close"].astype(float)
    volume = df["Volume"].fillna(0).astype(float)
    prev_close = close.shift(1)
    out = pd.DataFrame(index=df.index)
    out["open"] = df["Open"].astype(float)
    out["close"] = close
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
    if pd.isna(value):
        return None
    return float(value)


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
                prev2_low=None,
                prev_3d_low=None,
                prev_5d_low=None,
                big_bull_low=None,
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


def _score_day(snapshot: IndicatorSnapshot, benchmark_return_20d: float | None):
    return score(
        market="tw",
        ind=snapshot,
        analyst=AnalystSnapshot(target_mean=None, rating=None, rating_score=None),
        prev_target_mean=None,
        chip=None,
        benchmark_return_20d=benchmark_return_20d,
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
    if rule.startswith("投信"):
        return "trust"
    if rule.startswith("外資"):
        return "foreign"
    return None


def _weighted_score(result, weights: dict[str, float]) -> tuple[float, float]:
    total = 0.0
    max_score = 0.0
    for reason in result.reasons:
        key = _rule_key(reason.rule)
        weight = weights.get(key, reason.weight) if key else reason.weight
        max_score += weight
        if reason.passed:
            if reason.score is None:
                total += weight
            elif reason.weight:
                total += weight * reason.score / reason.weight
    return total, max_score


def _build_signal_facts(
    data: dict[str, pd.DataFrame], benchmark_ind: pd.DataFrame
) -> dict[pd.Timestamp, dict[str, dict]]:
    facts_by_date: dict[pd.Timestamp, dict[str, dict]] = {}
    for symbol, df in data.items():
        ind = _indicators_for_frame(df)
        for date, row in ind.iterrows():
            if date not in benchmark_ind.index:
                continue
            snapshot = _snapshot(row)
            if snapshot.ma240 is None or snapshot.prev_ma240 is None:
                continue
            bench_return = _float_or_none(benchmark_ind.loc[date, "return_20d"])
            result = _score_day(snapshot, bench_return)
            facts = []
            for reason in result.reasons:
                key = _rule_key(reason.rule)
                if key is None:
                    continue
                score_fraction = 0.0
                if reason.passed:
                    if reason.score is None:
                        score_fraction = 1.0
                    elif reason.weight:
                        score_fraction = reason.score / reason.weight
                facts.append((key, score_fraction))
            newly_above = _above_all(snapshot) and not _was_above_all(snapshot)
            above_ma5 = snapshot.ma5 is not None and snapshot.close > snapshot.ma5
            sell = snapshot.ma5 is not None and snapshot.close < snapshot.ma5
            facts_by_date.setdefault(date, {})[symbol] = {
                "facts": facts,
                "newly_above": newly_above,
                "above_ma5": above_ma5,
                "sell": sell,
            }
    return facts_by_date


def _signals_from_facts(
    facts_by_date: dict[pd.Timestamp, dict[str, dict]],
    weights: dict[str, float] | None = None,
    entry_rule: str = "newly_above_all",
) -> dict[pd.Timestamp, dict[str, dict]]:
    active_weights = weights or DEFAULT_WEIGHTS
    max_score = sum(active_weights.values())
    signals: dict[pd.Timestamp, dict[str, dict]] = {}
    for date, rows in facts_by_date.items():
        for symbol, row in rows.items():
            weighted_score = sum(
                active_weights[key] * score_fraction
                for key, score_fraction in row["facts"]
            )
            ratio = weighted_score / max_score if max_score else 0.0
            if entry_rule == "score_only":
                special = ratio >= SPECIAL_MIN_SCORE_RATIO
            elif entry_rule == "score_above_ma5":
                special = row["above_ma5"] and ratio >= SPECIAL_MIN_SCORE_RATIO
            else:
                special = row["newly_above"] and ratio >= SPECIAL_MIN_SCORE_RATIO
            signals.setdefault(date, {})[symbol] = {
                "score": weighted_score,
                "max_score": max_score,
                "ratio": ratio,
                "special": special,
                "sell": row["sell"],
            }
    return signals


def _build_signals(
    data: dict[str, pd.DataFrame],
    benchmark_ind: pd.DataFrame,
    weights: dict[str, float] | None = None,
    entry_rule: str = "newly_above_all",
) -> dict[pd.Timestamp, dict[str, dict]]:
    return _signals_from_facts(
        _build_signal_facts(data, benchmark_ind), weights, entry_rule
    )


def _open_price(
    data: dict[str, pd.DataFrame], symbol: str, date: pd.Timestamp
) -> float | None:
    frame = data.get(symbol)
    if frame is None or date not in frame.index:
        return None
    value = frame.loc[date, "Open"]
    return None if pd.isna(value) else float(value)


def _close_price(
    data: dict[str, pd.DataFrame], symbol: str, date: pd.Timestamp
) -> float | None:
    frame = data.get(symbol)
    if frame is None or date not in frame.index:
        return None
    value = frame.loc[date, "Close"]
    return None if pd.isna(value) else float(value)


def _portfolio_value(
    data: dict[str, pd.DataFrame],
    holdings: dict[str, Holding],
    benchmark_shares: float,
    cash: float,
    date: pd.Timestamp,
) -> float:
    value = cash
    bench_close = _close_price(data, BENCHMARK, date)
    if bench_close is not None:
        value += benchmark_shares * bench_close
    for symbol, holding in holdings.items():
        close = _close_price(data, symbol, date)
        if close is not None:
            value += holding.shares * close
    return value


def _buy_benchmark(
    data: dict[str, pd.DataFrame], cash: float, date: pd.Timestamp
) -> tuple[float, float]:
    price = _open_price(data, BENCHMARK, date)
    if price is None or price <= 0 or cash <= 0:
        return 0.0, cash
    shares = cash / price
    return shares, 0.0


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
    shortfall = needed - cash
    shares_to_sell = min(benchmark_shares, shortfall / price)
    benchmark_shares -= shares_to_sell
    cash += shares_to_sell * price
    return benchmark_shares, cash


def run_backtest(
    data: dict[str, pd.DataFrame],
    signals: dict[pd.Timestamp, dict[str, dict]],
    names: dict[str, str],
    dates: list[pd.Timestamp],
):
    cash = INITIAL_CAPITAL
    benchmark_shares, cash = _buy_benchmark(data, cash, dates[0])
    holdings: dict[str, Holding] = {}
    trades: list[dict] = []
    equity_curve: list[tuple[pd.Timestamp, float]] = []
    pending = signals.get(dates[0], {})

    for date in dates[1:]:
        today = signals.get(date, {})

        for symbol in list(holdings):
            if pending.get(symbol, {}).get("sell"):
                price = _open_price(data, symbol, date)
                if price is None:
                    continue
                holding = holdings.pop(symbol)
                proceeds = holding.shares * price
                cash += proceeds
                trades.append(
                    {
                        "date": date,
                        "symbol": symbol,
                        "name": names.get(symbol, ""),
                        "action": "sell",
                        "amount": proceeds,
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
            amount = ACTIVE_SLOT
            benchmark_shares, cash = _raise_cash_from_benchmark(
                data, benchmark_shares, cash, amount, date
            )
            spend = min(amount, cash)
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

    return equity_curve, trades, holdings


def _date_slice(
    benchmark_dates: list[pd.Timestamp], start: str | None, end: str | None, days: int
) -> list[pd.Timestamp]:
    if start or end:
        start_ts = pd.Timestamp(start) if start else benchmark_dates[0]
        end_ts = pd.Timestamp(end) if end else benchmark_dates[-1]
        dates = [date for date in benchmark_dates if start_ts <= date <= end_ts]
        prev_dates = [date for date in benchmark_dates if date < dates[0]]
        if prev_dates:
            dates = [prev_dates[-1], *dates]
        return dates
    return benchmark_dates[-days - 1 :]


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
    bench_end = _close_price(data, BENCHMARK, end_date)
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
    buy_trades = [trade for trade in trades if trade["action"] == "buy"]
    sells = [trade for trade in trades if trade["action"] == "sell"]
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
        "buys": len(buy_trades),
        "sells": len(sells),
        "open_positions": len(holdings),
    }


def _run_variant(label: str, weights: dict[str, float]) -> dict:
    signals = _signals_from_facts(_WORKER_BASE_SIGNALS, weights, _WORKER_ENTRY_RULE)
    active_curve, trades, holdings = run_backtest(
        _WORKER_DATA, signals, _WORKER_NAMES, _WORKER_DATES
    )
    summary = _summarize(
        label, _WORKER_DATES, _WORKER_DATA, trades, holdings, active_curve
    )
    summary["weights"] = ",".join(f"{key}={weights[key]:g}" for key in WEIGHT_KEYS)
    return summary


def _init_worker(data, signal_facts, names, dates, entry_rule):
    global _WORKER_DATA, _WORKER_BASE_SIGNALS, _WORKER_NAMES, _WORKER_DATES
    global _WORKER_ENTRY_RULE
    _WORKER_DATA = data
    _WORKER_BASE_SIGNALS = signal_facts
    _WORKER_NAMES = names
    _WORKER_DATES = dates
    _WORKER_ENTRY_RULE = entry_rule


def _weight_variants() -> list[tuple[str, dict[str, float]]]:
    variants = [("baseline", DEFAULT_WEIGHTS.copy())]
    sweep_keys = (
        "above_all",
        "new_high",
        "trend",
        "volume",
        "obv",
        "relative_strength",
        "macd",
    )
    for multipliers in itertools.product(SWEEP_MULTIPLIERS, repeat=len(sweep_keys)):
        weights = DEFAULT_WEIGHTS.copy()
        for key, multiplier in zip(sweep_keys, multipliers):
            weights[key] = DEFAULT_WEIGHTS[key] * multiplier
        label = "grid_" + "_".join(f"{key}{multiplier:g}" for key, multiplier in zip(sweep_keys, multipliers))
        if weights == DEFAULT_WEIGHTS:
            continue
        variants.append((label, weights))
    return variants


def _print_summary(summary: dict) -> None:
    print(
        f"{summary['label']} period={summary['start']}..{summary['end']} "
        f"trading_days={summary['trading_days']}"
    )
    print(
        f"benchmark={BENCHMARK} final={summary['benchmark_final']:.0f} "
        f"return={summary['benchmark_return_pct']:.2f}%"
    )
    print(
        f"active final={summary['active_final']:.0f} "
        f"return={summary['active_return_pct']:.2f}% "
        f"excess={summary['excess_pct']:.2f}% "
        f"max_dd={summary['max_drawdown_pct']:.2f}%"
    )
    print(
        f"buys={summary['buys']} sells={summary['sells']} "
        f"open_positions={summary['open_positions']}"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--period", default="3y")
    parser.add_argument("--days", type=int, default=252)
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--watchlist", default="data/watchlist.csv")
    parser.add_argument("--sweep", action="store_true")
    parser.add_argument("--jobs", type=int, default=1)
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument("--output-csv")
    parser.add_argument(
        "--entry-rule",
        choices=("newly_above_all", "score_only", "score_above_ma5"),
        default="newly_above_all",
        help="special-attention entry rule to backtest",
    )
    args = parser.parse_args()

    names = _load_tw_symbols(Path(args.watchlist))
    symbols = sorted(set(names) | {BENCHMARK})
    data = (
        _download_range(symbols, args.start, args.end)
        if args.start or args.end
        else _download(symbols, args.period)
    )
    if BENCHMARK not in data:
        raise RuntimeError(f"missing benchmark data: {BENCHMARK}")
    missing = sorted(set(names) - set(data))
    names = {symbol: name for symbol, name in names.items() if symbol in data}
    benchmark_ind = _indicators_for_frame(data[BENCHMARK])
    all_dates = sorted(data[BENCHMARK].index)
    backtest_dates = _date_slice(all_dates, args.start, args.end, args.days)
    signal_data = {symbol: data[symbol] for symbol in names}
    signal_facts = _build_signal_facts(signal_data, benchmark_ind)
    signals = _signals_from_facts(signal_facts, entry_rule=args.entry_rule)
    active_curve, trades, holdings = run_backtest(data, signals, names, backtest_dates)

    buy_trades = [trade for trade in trades if trade["action"] == "buy"]
    summary = _summarize(
        f"baseline_{args.entry_rule}", backtest_dates, data, trades, holdings, active_curve
    )
    _print_summary(summary)
    print(f"symbols_loaded={len(names)} missing={len(missing)}")
    if missing:
        print("missing_symbols=" + ",".join(missing[:30]))
    print("first_buys:")
    for trade in buy_trades[:20]:
        print(
            f"{trade['date'].date()} {trade['symbol']} {trade.get('name', '')} "
            f"{trade['action']} amount={trade['amount']:.0f} "
            f"score={trade.get('score', 0):.1f}/{trade.get('max_score', 0):.1f} "
            f"ratio={trade.get('ratio', 0) * 100:.1f}%"
        )
    if args.sweep:
        variants = _weight_variants()
        if args.jobs > 1:
            with ProcessPoolExecutor(
                max_workers=args.jobs,
                initializer=_init_worker,
                initargs=(data, signal_facts, names, backtest_dates, args.entry_rule),
            ) as executor:
                rows = list(
                    executor.map(
                        _run_variant, [v[0] for v in variants], [v[1] for v in variants]
                    )
                )
        else:
            _init_worker(data, signal_facts, names, backtest_dates, args.entry_rule)
            rows = [_run_variant(label, weights) for label, weights in variants]
        frame = pd.DataFrame(rows).sort_values(
            ["excess_pct", "active_return_pct"], ascending=False
        )
        print("sweep_top:")
        columns = [
            "label",
            "active_return_pct",
            "benchmark_return_pct",
            "excess_pct",
            "max_drawdown_pct",
            "buys",
            "sells",
            "open_positions",
            "weights",
        ]
        print(frame[columns].head(args.top).to_string(index=False))
        if args.output_csv:
            frame.to_csv(args.output_csv, index=False)


if __name__ == "__main__":
    main()
