from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
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
SCALE_UP_MIN_SCORE_RATIO = 0.80


@dataclass
class Holding:
    shares: float
    cost: float
    added: bool = False


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


def _build_signals(
    data: dict[str, pd.DataFrame], benchmark_ind: pd.DataFrame
) -> dict[pd.Timestamp, dict[str, dict]]:
    signals: dict[pd.Timestamp, dict[str, dict]] = {}
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
            ratio = result.score / result.max_score if result.max_score else 0.0
            newly_above = _above_all(snapshot) and not _was_above_all(snapshot)
            reasons = {reason.rule: reason.passed for reason in result.reasons}
            special = newly_above and ratio >= SPECIAL_MIN_SCORE_RATIO
            scale_up = (
                newly_above
                and ratio >= SCALE_UP_MIN_SCORE_RATIO
                and snapshot.high_5d is not None
                and snapshot.close >= snapshot.high_5d
                and any(
                    rule.startswith("相對強度") and passed
                    for rule, passed in reasons.items()
                )
                and any(
                    rule.startswith("OBV") and passed for rule, passed in reasons.items()
                )
            )
            sell = snapshot.ma5 is not None and snapshot.close < snapshot.ma5
            signals.setdefault(date, {})[symbol] = {
                "score": result.score,
                "max_score": result.max_score,
                "ratio": ratio,
                "special": special,
                "scale_up": scale_up,
                "sell": sell,
            }
    return signals


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
            amount = ACTIVE_SLOT * (2 if row["scale_up"] else 1)
            benchmark_shares, cash = _raise_cash_from_benchmark(
                data, benchmark_shares, cash, amount, date
            )
            spend = min(amount, cash)
            if spend <= 0:
                continue
            holdings[symbol] = Holding(
                shares=spend / price, cost=spend, added=row["scale_up"]
            )
            cash -= spend
            trades.append(
                {
                    "date": date,
                    "symbol": symbol,
                    "name": names.get(symbol, ""),
                    "action": "buy_scale" if row["scale_up"] else "buy",
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--period", default="3y")
    parser.add_argument("--days", type=int, default=252)
    parser.add_argument("--watchlist", default="data/watchlist.csv")
    args = parser.parse_args()

    names = _load_tw_symbols(Path(args.watchlist))
    symbols = sorted(set(names) | {BENCHMARK})
    data = _download(symbols, args.period)
    if BENCHMARK not in data:
        raise RuntimeError(f"missing benchmark data: {BENCHMARK}")
    missing = sorted(set(names) - set(data))
    names = {symbol: name for symbol, name in names.items() if symbol in data}
    benchmark_ind = _indicators_for_frame(data[BENCHMARK])
    all_dates = sorted(data[BENCHMARK].index)
    backtest_dates = all_dates[-args.days - 1 :]
    signals = _build_signals({symbol: data[symbol] for symbol in names}, benchmark_ind)
    active_curve, trades, holdings = run_backtest(data, signals, names, backtest_dates)

    start_date = backtest_dates[1]
    end_date = backtest_dates[-1]
    bench_start = _open_price(data, BENCHMARK, start_date)
    bench_end = _close_price(data, BENCHMARK, end_date)
    benchmark_final = INITIAL_CAPITAL / bench_start * bench_end
    active_final = active_curve[-1][1]
    buy_trades = [trade for trade in trades if trade["action"] in {"buy", "buy_scale"}]
    scale_buys = [trade for trade in trades if trade["action"] == "buy_scale"]
    sells = [trade for trade in trades if trade["action"] == "sell"]
    print(f"period={start_date.date()}..{end_date.date()} trading_days={len(backtest_dates) - 1}")
    print(f"symbols_loaded={len(names)} missing={len(missing)}")
    if missing:
        print("missing_symbols=" + ",".join(missing[:30]))
    print(
        f"benchmark={BENCHMARK} final={benchmark_final:.0f} "
        f"return={(benchmark_final / INITIAL_CAPITAL - 1) * 100:.2f}%"
    )
    print(
        f"active final={active_final:.0f} "
        f"return={(active_final / INITIAL_CAPITAL - 1) * 100:.2f}%"
    )
    print(f"excess={(active_final / benchmark_final - 1) * 100:.2f}%")
    print(
        f"buys={len(buy_trades)} scale_entry_buys={len(scale_buys)} "
        f"sells={len(sells)} open_positions={len(holdings)}"
    )
    print("first_buys:")
    for trade in buy_trades[:20]:
        print(
            f"{trade['date'].date()} {trade['symbol']} {trade.get('name', '')} "
            f"{trade['action']} amount={trade['amount']:.0f} "
            f"score={trade.get('score', 0):.1f}/{trade.get('max_score', 0):.1f} "
            f"ratio={trade.get('ratio', 0) * 100:.1f}%"
        )


if __name__ == "__main__":
    main()
