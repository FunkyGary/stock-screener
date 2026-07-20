"""Exp 2a: gap event study across the TW + US watchlist.

Zero production risk (does not touch score.py / indicators.py). Detects daily
true gaps on each watchlist symbol using ADJUSTED prices (auto_adjust=True), so
ex-dividend / split pseudo-gaps are removed by the adjustment. Records forward
excess returns vs the market benchmark to answer, before any scoring wiring:

  1. Do up-gaps (esp. volume-confirmed breakaway gaps) beat the benchmark?
  2. Is the "10-day unfilled = strong structure" rule real? Measured with
     POST-observation returns (from t+10), so the 10-day fill outcome does not
     leak into the return window it is supposed to predict.
  3. Do fast-filled up-gaps (failed breakout) and down-gaps underperform?

True gap definitions (daily OHLC):
  up   gap at t: low[t]  > high[t-1] * (1 + min_gap)   support  = high[t-1]
  down gap at t: high[t] < low[t-1]  * (1 - min_gap)   resistance = low[t-1]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

BENCHMARKS = {"tw": "0050.TW", "us": "SPY"}
FWD_WINDOWS = (10, 20, 60)
OBSERVE_DAYS = 10  # the article's 10-day gap-fill observation window
POST_WINDOWS = (20, 60)  # forward windows measured FROM t+OBSERVE_DAYS


def _load_watchlist(path: Path) -> dict[str, str]:
    df = pd.read_csv(path)
    return {str(r.symbol): str(r.market) for r in df.itertuples(index=False)}


def _download(symbols: list[str], start: str, end: str) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    for i in range(0, len(symbols), 40):
        chunk = symbols[i : i + 40]
        data = yf.download(
            " ".join(chunk), start=start, end=end, interval="1d",
            group_by="ticker", auto_adjust=True, progress=False, threads=True,
        )
        for sym in chunk:
            try:
                df = data[sym] if len(chunk) > 1 else data
            except KeyError:
                continue
            df = df.dropna(subset=["Open", "High", "Low", "Close", "Volume"])
            if len(df) > 250:
                out[sym] = df
    return out


def _events_for_symbol(
    symbol: str, market: str, df: pd.DataFrame, bench_close: pd.Series,
    min_gap: float, vol_conf: float, win_start: pd.Timestamp, win_end: pd.Timestamp,
) -> list[dict]:
    high = df["High"].astype(float).to_numpy()
    low = df["Low"].astype(float).to_numpy()
    close = df["Close"].astype(float).to_numpy()
    vol = df["Volume"].astype(float).to_numpy()
    dates = df.index
    vol_ma20 = df["Volume"].astype(float).rolling(20).mean().to_numpy()
    prior_high_20d = df["Close"].astype(float).shift(1).rolling(20).max().to_numpy()

    n = len(df)
    events: list[dict] = []
    for i in range(1, n):
        d = dates[i]
        if d < win_start or d > win_end:
            continue
        prev_high, prev_low = high[i - 1], low[i - 1]
        up = prev_high > 0 and (low[i] - prev_high) / prev_high >= min_gap
        down = prev_low > 0 and (prev_low - high[i]) / prev_low >= min_gap
        if not (up or down):
            continue

        gap_type = "up" if up else "down"
        support = prev_high if up else prev_low
        gap_size = (
            (low[i] - prev_high) / prev_high if up else (prev_low - high[i]) / prev_low
        )
        vr = (
            vol[i] / vol_ma20[i]
            if not np.isnan(vol_ma20[i]) and vol_ma20[i] > 0
            else float("nan")
        )
        rec = {
            "symbol": symbol, "market": market, "date": d.date().isoformat(),
            "gap_type": gap_type, "gap_size": round(float(gap_size), 4),
            "vol_ratio": round(float(vr), 2) if not np.isnan(vr) else None,
            "vol_confirmed": bool(not np.isnan(vr) and vr >= vol_conf),
            "makes_new_high": bool(
                not np.isnan(prior_high_20d[i]) and close[i] > prior_high_20d[i]
            ) if up else False,
        }

        # Fill tracking (up gaps only): fast-fill within 2 bars, unfilled over 10.
        if up:
            fast = any(
                low[k] <= support for k in range(i + 1, min(n, i + 3))
            )
            obs_end = i + OBSERVE_DAYS
            unfilled_10d = (
                obs_end < n and low[i + 1 : obs_end + 1].min() > support
            )
            rec["fast_filled"] = bool(fast)
            rec["unfilled_10d"] = bool(unfilled_10d)
        else:
            rec["fast_filled"] = False
            rec["unfilled_10d"] = False

        # Forward excess returns from the gap day t.
        ok = True
        for w in FWD_WINDOWS:
            if i + w >= n:
                ok = False
                break
            fwd = close[i + w] / close[i] - 1.0
            b0 = bench_close.asof(d)
            b1 = bench_close.asof(dates[i + w])
            bench_fwd = (b1 / b0 - 1.0) if b0 and b1 else float("nan")
            rec[f"excess{w}"] = round(float(fwd - bench_fwd), 4)
        if not ok:
            continue

        # Post-observation returns from t+OBSERVE_DAYS (for the 10-day test).
        base_i = i + OBSERVE_DAYS
        if base_i < n:
            b_obs0 = bench_close.asof(dates[base_i])
            for w in POST_WINDOWS:
                j = base_i + w
                if j < n and b_obs0:
                    fwd = close[j] / close[base_i] - 1.0
                    b1 = bench_close.asof(dates[j])
                    bench_fwd = (b1 / b_obs0 - 1.0) if b1 else float("nan")
                    rec[f"post{w}"] = round(float(fwd - bench_fwd), 4)
        events.append(rec)
    return events


def _baseline_excess(
    prices: dict[str, pd.DataFrame], wl: dict[str, str], bench: dict[str, pd.Series],
    win_start: pd.Timestamp, win_end: pd.Timestamp,
) -> dict[str, dict[int, float]]:
    """Beta-neutral control: mean forward excess on ALL days per market.

    Answers 'do watchlist stocks beat the benchmark anyway?' so gap-conditioned
    excess can be judged against the unconditional baseline, not zero.
    """
    acc: dict[str, dict[int, list[float]]] = {
        m: {w: [] for w in FWD_WINDOWS} for m in BENCHMARKS
    }
    for symbol, df in prices.items():
        mkt = wl.get(symbol)
        if mkt is None or symbol in BENCHMARKS.values():
            continue
        close = df["Close"].astype(float)
        dates = df.index
        bc = bench[mkt]
        in_win = (dates >= win_start) & (dates <= win_end)
        bench_on_date = pd.Series(bc.asof(pd.DatetimeIndex(dates)), index=dates)
        for w in FWD_WINDOWS:
            fwd = close.shift(-w) / close - 1.0
            bench_fwd = bench_on_date.shift(-w) / bench_on_date - 1.0
            ex = (fwd - bench_fwd)[in_win].dropna()
            acc[mkt][w].extend(ex.to_numpy().tolist())
    return {
        m: {w: (float(np.mean(v)) if v else float("nan")) for w, v in d.items()}
        for m, d in acc.items()
    }


def _summarize(label: str, sub: pd.DataFrame, cols: tuple[str, ...]) -> None:
    if sub.empty:
        print(f"{label:<34} n=0")
        return
    parts = [f"n={len(sub):>5}"]
    for c in cols:
        if c not in sub or sub[c].dropna().empty:
            parts.append(f"{c}=n/a")
            continue
        ex = sub[c].dropna()
        parts.append(
            f"{c} mean={ex.mean() * 100:+5.2f}% win={(ex > 0).mean() * 100:4.1f}%"
        )
    print(f"{label:<34} " + "  ".join(parts))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--watchlist", default="data/watchlist.csv")
    ap.add_argument("--start", default="2018-01-01")
    ap.add_argument("--end", default="2026-06-30")
    ap.add_argument("--warmup-start", default="2016-06-01")
    ap.add_argument("--min-gap", type=float, default=0.01)
    ap.add_argument("--vol-conf", type=float, default=1.5)
    ap.add_argument("--output-csv", default="results/backtests/gap_event_study.csv")
    args = ap.parse_args()

    wl = _load_watchlist(ROOT / args.watchlist)
    symbols = sorted(set(wl) | set(BENCHMARKS.values()))
    prices = _download(symbols, args.warmup_start, args.end)
    print(f"price symbols loaded: {len(prices)} (of {len(symbols)})")
    bench = {}
    for mkt, sym in BENCHMARKS.items():
        if sym not in prices:
            sys.exit(f"benchmark {sym} prices missing")
        bench[mkt] = prices[sym]["Close"]

    win_start, win_end = pd.Timestamp(args.start), pd.Timestamp(args.end)
    events: list[dict] = []
    for symbol, df in prices.items():
        mkt = wl.get(symbol)
        if mkt is None or symbol in BENCHMARKS.values():
            continue
        events.extend(
            _events_for_symbol(
                symbol, mkt, df, bench[mkt], args.min_gap, args.vol_conf,
                win_start, win_end,
            )
        )
    if not events:
        sys.exit("no gap events in window")
    ev = pd.DataFrame(events)
    out_path = ROOT / args.output_csv
    ev.to_csv(out_path, index=False)

    baseline = _baseline_excess(prices, wl, bench, win_start, win_end)

    fwd_cols = tuple(f"excess{w}" for w in FWD_WINDOWS)
    post_cols = tuple(f"post{w}" for w in POST_WINDOWS)

    for mkt in ("tw", "us"):
        m = ev[ev["market"] == mkt]
        up = m[m["gap_type"] == "up"]
        down = m[m["gap_type"] == "down"]
        base = baseline[mkt]
        print(
            f"\n=== {mkt.upper()} gap event study {args.start}..{args.end} "
            f"(min_gap={args.min_gap:.1%}, excess vs {BENCHMARKS[mkt]}) ==="
        )
        print(f"events={len(m)} symbols={m['symbol'].nunique()}")
        print(
            "BASELINE all-day mean excess (beta-neutral control): "
            + "  ".join(f"ex{w}={base[w] * 100:+5.2f}%" for w in FWD_WINDOWS)
            + "\n"
        )
        print("-- forward excess from gap day t (compare vs BASELINE, not zero) --")
        _summarize("UP gap (all)", up, fwd_cols)
        _summarize("  UP vol-confirmed", up[up["vol_confirmed"]], fwd_cols)
        _summarize("  UP breakaway (new 20d high)", up[up["makes_new_high"]], fwd_cols)
        _summarize("  UP vol+breakaway", up[up["vol_confirmed"] & up["makes_new_high"]], fwd_cols)
        _summarize("  UP fast-filled (<=2d)", up[up["fast_filled"]], fwd_cols)
        _summarize("DOWN gap (all)", down, fwd_cols)
        _summarize("  DOWN vol-confirmed", down[down["vol_confirmed"]], fwd_cols)
        print("-- 10-day observation: post-obs excess from t+10 --")
        _summarize("UP unfilled_10d", up[up["unfilled_10d"]], post_cols)
        _summarize("UP filled within 10d", up[~up["unfilled_10d"]], post_cols)
        _summarize("UP vol+unfilled_10d", up[up["vol_confirmed"] & up["unfilled_10d"]], post_cols)

    print(f"\nCSV -> {out_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
