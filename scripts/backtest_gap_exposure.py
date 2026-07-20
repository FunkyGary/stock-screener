"""Exp 1: index-level gap signal as a regime exposure throttle.

Tests whether daily-chart gap signals on the market benchmark can act as a
*leading* correction layer on top of the existing regime classification, to
modulate overall exposure between 100% and 70%.

Three strategies are compared on the same period:
  - buy_hold      : always 100% in the benchmark (total-return proxy).
  - regime        : 100% when regime in {bull, range}, 70% otherwise.
  - regime_gap    : regime base exposure, but gap signals may switch earlier:
                    early de-risk (100 -> 70) on an unfilled down-gap or a
                    fast-filled up-gap; early re-risk (70 -> 100) on a fresh
                    unfilled, volume-confirmed up-gap.

Gaps are detected on the benchmark ETF with auto_adjust=True, so ex-dividend /
split pseudo-gaps are removed by the adjustment (satisfies the "precise
ex-dividend exclusion" requirement for the index-level test for free).

No lookahead: exposure for day t is decided from data up to and including the
close of day t-1.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, asdict
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

BENCHMARKS = {"tw": "0050.TW", "us": "SPY"}

# Regime thresholds mirror screener/market_regime.py.
BEAR_DRAWDOWN_120D = -0.12
BULL_RETURN_60D = 0.03

DEFAULT_START = "2018-01-01"
DEFAULT_END = "2026-06-30"

FULL_EXPOSURE = 1.0
REDUCED_EXPOSURE = 0.70
SWITCH_FEE = 0.001  # round-trip cost charged on the traded exposure fraction


@dataclass
class StrategyResult:
    name: str
    total_return_pct: float
    cagr_pct: float
    max_drawdown_pct: float
    switches: int
    bear_return_pct: float
    bear_max_drawdown_pct: float


def _download(symbol: str, start: str, end: str, warmup_days: int = 520) -> pd.DataFrame:
    start_ts = pd.Timestamp(start) - pd.Timedelta(days=warmup_days)
    end_ts = pd.Timestamp(end) + pd.Timedelta(days=5)
    df = yf.download(
        symbol,
        start=start_ts.date().isoformat(),
        end=end_ts.date().isoformat(),
        interval="1d",
        auto_adjust=True,
        progress=False,
        threads=False,
    )
    if df is None or df.empty:
        raise SystemExit(f"no OHLCV data for {symbol}")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.dropna(subset=["Open", "High", "Low", "Close", "Volume"])
    df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
    return df


def _classify_regime(df: pd.DataFrame) -> pd.Series:
    """Per-day regime label using the same rules as market_regime.py."""
    close = df["Close"].astype(float)
    ma20 = close.rolling(20).mean()
    ma60 = close.rolling(60).mean()
    ma240 = close.rolling(240).mean()
    high_120d = close.rolling(120).max()
    close_60d_ago = close.shift(60)
    return_60d = close / close_60d_ago - 1.0
    drawdown_120d = close / high_120d - 1.0

    labels = pd.Series("range", index=df.index, dtype=object)
    is_bear_crash = drawdown_120d <= BEAR_DRAWDOWN_120D
    is_bear_downtrend = (close < ma240) & (ma60 < ma240)
    is_bull = (
        (close > ma20)
        & (close > ma60)
        & (ma60 > ma240)
        & (return_60d > BULL_RETURN_60D)
    )
    # Priority mirrors market_regime.py: bear_crash > bear_downtrend > bull > range.
    labels[is_bull] = "bull"
    labels[is_bear_downtrend] = "bear_downtrend"
    labels[is_bear_crash] = "bear_crash"
    return labels


def _detect_gap_signals(
    df: pd.DataFrame,
    min_gap: float,
    window: int,
    fast_fill_bars: int,
    vol_mult: float,
) -> pd.DataFrame:
    """Return per-day boolean signal columns using data up to that day only."""
    high = df["High"].astype(float).to_numpy()
    low = df["Low"].astype(float).to_numpy()
    vol = df["Volume"].astype(float).to_numpy()
    prev_high = np.concatenate([[np.nan], high[:-1]])
    prev_low = np.concatenate([[np.nan], low[:-1]])
    vol_ma20 = df["Volume"].astype(float).rolling(20).mean().to_numpy()

    n = len(df)
    up_gap = np.zeros(n, dtype=bool)
    down_gap = np.zeros(n, dtype=bool)
    up_gap_low = np.full(n, np.nan)  # support = prev high
    down_gap_high = np.full(n, np.nan)  # resistance = prev low
    up_gap_vol_ok = np.zeros(n, dtype=bool)

    for t in range(1, n):
        if prev_high[t] > 0 and (low[t] - prev_high[t]) / prev_high[t] >= min_gap:
            up_gap[t] = True
            up_gap_low[t] = prev_high[t]
            up_gap_vol_ok[t] = (
                not np.isnan(vol_ma20[t])
                and vol_ma20[t] > 0
                and vol[t] >= vol_ma20[t] * vol_mult
            )
        if prev_low[t] > 0 and (prev_low[t] - high[t]) / prev_low[t] >= min_gap:
            down_gap[t] = True
            down_gap_high[t] = prev_low[t]

    # For each day d, determine active signals from gaps in [d-window+1, d].
    active_unfilled_up = np.zeros(n, dtype=bool)
    active_unfilled_up_volok = np.zeros(n, dtype=bool)
    active_unfilled_down = np.zeros(n, dtype=bool)
    recent_fast_filled_up = np.zeros(n, dtype=bool)

    for d in range(n):
        lo = max(0, d - window + 1)
        for t in range(lo, d + 1):
            if up_gap[t]:
                support = up_gap_low[t]
                # filled if any bar in (t, d] traded back into the gap zone.
                min_low_after = low[t + 1 : d + 1].min() if d > t else np.inf
                filled = min_low_after <= support
                if not filled:
                    active_unfilled_up[d] = True
                    if up_gap_vol_ok[t]:
                        active_unfilled_up_volok[d] = True
                else:
                    # fast fill: filled within fast_fill_bars bars of opening.
                    fill_idx = None
                    for k in range(t + 1, min(d, t + fast_fill_bars) + 1):
                        if low[k] <= support:
                            fill_idx = k
                            break
                    if fill_idx is not None:
                        recent_fast_filled_up[d] = True
            if down_gap[t]:
                resistance = down_gap_high[t]
                max_high_after = high[t + 1 : d + 1].max() if d > t else -np.inf
                reclaimed = max_high_after >= resistance
                if not reclaimed:
                    active_unfilled_down[d] = True

    return pd.DataFrame(
        {
            "up_gap": up_gap,
            "down_gap": down_gap,
            "active_unfilled_up": active_unfilled_up,
            "active_unfilled_up_volok": active_unfilled_up_volok,
            "active_unfilled_down": active_unfilled_down,
            "recent_fast_filled_up": recent_fast_filled_up,
        },
        index=df.index,
    )


def _max_drawdown(equity: pd.Series) -> float:
    if equity.empty:
        return 0.0
    running_max = equity.cummax()
    drawdown = equity / running_max - 1.0
    return float(drawdown.min())


def _simulate(
    ret: pd.Series, exposure: pd.Series, fee: float
) -> tuple[pd.Series, int]:
    """Equity curve from daily returns and prior-day exposure decisions."""
    exp_shift = exposure.shift(1).fillna(exposure.iloc[0])
    turnover = exp_shift.diff().abs().fillna(0.0)
    daily = exp_shift * ret - turnover * fee
    equity = (1.0 + daily).cumprod()
    switches = int((exp_shift.diff().fillna(0.0) != 0).sum())
    return equity, switches


def _summarize(name: str, equity: pd.Series, switches: int, regime: pd.Series,
               ret: pd.Series, exposure: pd.Series, fee: float) -> StrategyResult:
    n_years = len(equity) / 252.0
    total = float(equity.iloc[-1] - 1.0)
    cagr = float(equity.iloc[-1] ** (1.0 / n_years) - 1.0) if n_years > 0 else 0.0
    mdd = _max_drawdown(equity)

    # Bear-only sub-performance (bear_crash or bear_downtrend days).
    bear_mask = regime.isin(["bear_crash", "bear_downtrend"])
    exp_shift = exposure.shift(1).fillna(exposure.iloc[0])
    turnover = exp_shift.diff().abs().fillna(0.0)
    daily = exp_shift * ret - turnover * fee
    bear_daily = daily.where(bear_mask, 0.0)
    bear_equity = (1.0 + bear_daily).cumprod()
    bear_return = float(bear_equity.iloc[-1] - 1.0)
    bear_mdd = _max_drawdown(bear_equity)

    return StrategyResult(
        name=name,
        total_return_pct=round(total * 100, 2),
        cagr_pct=round(cagr * 100, 2),
        max_drawdown_pct=round(mdd * 100, 2),
        switches=switches,
        bear_return_pct=round(bear_return * 100, 2),
        bear_max_drawdown_pct=round(bear_mdd * 100, 2),
    )


def run_market(
    market: str,
    start: str,
    end: str,
    min_gap: float,
    window: int,
    fast_fill_bars: int,
    vol_mult: float,
    fee: float,
) -> tuple[list[StrategyResult], dict]:
    symbol = BENCHMARKS[market]
    raw = _download(symbol, start, end)
    regime_full = _classify_regime(raw)
    signals_full = _detect_gap_signals(raw, min_gap, window, fast_fill_bars, vol_mult)

    # Trim warmup: keep the requested window.
    mask = (raw.index >= pd.Timestamp(start)) & (raw.index <= pd.Timestamp(end))
    df = raw[mask]
    regime = regime_full[mask]
    signals = signals_full[mask]
    ret = df["Close"].astype(float).pct_change().fillna(0.0)

    # Exposure series.
    base = pd.Series(
        np.where(regime.isin(["bull", "range"]), FULL_EXPOSURE, REDUCED_EXPOSURE),
        index=df.index,
    )
    bh = pd.Series(FULL_EXPOSURE, index=df.index)

    gap_exp = base.copy()
    early_derisk = (base == FULL_EXPOSURE) & (
        signals["active_unfilled_down"] | signals["recent_fast_filled_up"]
    )
    early_rerisk = (base == REDUCED_EXPOSURE) & signals["active_unfilled_up_volok"]
    gap_exp[early_derisk] = REDUCED_EXPOSURE
    gap_exp[early_rerisk] = FULL_EXPOSURE

    results = []
    for name, exp in (("buy_hold", bh), ("regime", base), ("regime_gap", gap_exp)):
        equity, switches = _simulate(ret, exp, fee)
        results.append(_summarize(name, equity, switches, regime, ret, exp, fee))

    diag = {
        "symbol": symbol,
        "trading_days": int(len(df)),
        "up_gaps": int(signals["up_gap"].sum()),
        "down_gaps": int(signals["down_gap"].sum()),
        "days_active_unfilled_up": int(signals["active_unfilled_up"].sum()),
        "days_active_unfilled_down": int(signals["active_unfilled_down"].sum()),
        "days_fast_filled_up": int(signals["recent_fast_filled_up"].sum()),
        "days_early_derisk": int(early_derisk.sum()),
        "days_early_rerisk": int(early_rerisk.sum()),
        "regime_days": {k: int(v) for k, v in regime.value_counts().items()},
    }
    return results, diag


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--markets", nargs="+", choices=["tw", "us"], default=["tw", "us"])
    parser.add_argument("--start", default=DEFAULT_START)
    parser.add_argument("--end", default=DEFAULT_END)
    parser.add_argument("--min-gap", type=float, default=0.0015)
    parser.add_argument("--window", type=int, default=10)
    parser.add_argument("--fast-fill-bars", type=int, default=2)
    parser.add_argument("--vol-mult", type=float, default=1.3)
    parser.add_argument("--fee", type=float, default=SWITCH_FEE)
    parser.add_argument("--csv", default=None)
    args = parser.parse_args()

    rows = []
    for market in args.markets:
        results, diag = run_market(
            market,
            args.start,
            args.end,
            args.min_gap,
            args.window,
            args.fast_fill_bars,
            args.vol_mult,
            args.fee,
        )
        print(f"\n=== {market.upper()} ({diag['symbol']}) {args.start}..{args.end} ===")
        print(
            f"trading_days={diag['trading_days']} "
            f"up_gaps={diag['up_gaps']} down_gaps={diag['down_gaps']} "
            f"active_up_days={diag['days_active_unfilled_up']} "
            f"active_down_days={diag['days_active_unfilled_down']} "
            f"fast_filled_up_days={diag['days_fast_filled_up']}"
        )
        print(
            f"gap overlay actions: early_derisk_days={diag['days_early_derisk']} "
            f"early_rerisk_days={diag['days_early_rerisk']}"
        )
        print(f"regime_days={diag['regime_days']}")
        header = (
            f"{'strategy':<12} {'total%':>9} {'cagr%':>8} {'maxDD%':>9} "
            f"{'switch':>7} {'bearRet%':>9} {'bearDD%':>9}"
        )
        print(header)
        for r in results:
            print(
                f"{r.name:<12} {r.total_return_pct:>9.2f} {r.cagr_pct:>8.2f} "
                f"{r.max_drawdown_pct:>9.2f} {r.switches:>7d} "
                f"{r.bear_return_pct:>9.2f} {r.bear_max_drawdown_pct:>9.2f}"
            )
            row = {"market": market, **asdict(r), **{
                "min_gap": args.min_gap, "window": args.window,
                "fast_fill_bars": args.fast_fill_bars, "vol_mult": args.vol_mult,
                "fee": args.fee, "start": args.start, "end": args.end,
            }, "up_gaps": diag["up_gaps"], "down_gaps": diag["down_gaps"]}
            rows.append(row)

    if args.csv:
        out = Path(args.csv)
        out.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(rows).to_csv(out, index=False)
        print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
