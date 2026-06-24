"""Point-in-time event study: does TW monthly-revenue YoY add value at a
technical breakout?

Question: at each "newly 站上全均線" breakout (close > MA5/MA10/MA20/MA240 today,
not all-above yesterday — the screener's 今日站上全均線 entry), does conditioning on
the latest *publicly available* monthly-revenue YoY improve forward return vs the
benchmark (0050)? This is a filter-efficacy study, not a portfolio sim: it
isolates the fundamental's marginal value before deciding whether it earns a
place in score().

Point-in-time: a revenue row for month M is published by the 10th of M+1. FinMind
dates the row at the first day of M+1, so it is only usable from the 11th
onward. We lag every row by +10 days; no lookahead.

Universe: current TW watchlist (survivorship caveat — same as every other backtest
here). Revenue from results/backtests/_cache/tw_month_revenue.json (run
fetch_tw_month_revenue.py first). Prices from yfinance.

Usage:
    uv run python scripts/backtest_tw_revenue_yoy.py --start 2018-01-01 --end 2024-12-31
"""

from __future__ import annotations

import argparse
import bisect
import json
import sys
from pathlib import Path

import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
CACHE = ROOT / "results" / "backtests" / "_cache" / "tw_month_revenue.json"
BENCHMARK = "0050.TW"
PUBLISH_LAG_DAYS = 10  # revenue for month M public by the 10th of M+1
FWD_WINDOWS = (20, 60)  # forward trading-day horizons


def _load_tw_names(watchlist: Path) -> dict[str, str]:
    rows = pd.read_csv(watchlist)
    names: dict[str, str] = {}
    for row in rows.itertuples(index=False):
        if str(row.market) != "tw":
            continue
        symbol = str(row.symbol)
        if symbol[:2] == "00":
            continue
        names[symbol] = str(row.name)
    return names


def _build_yoy_index(rows: list[dict]) -> tuple[list[pd.Timestamp], list[float], list[bool]]:
    """Return (avail_dates, yoy, turned_positive) sorted by availability date."""
    by_month: dict[tuple[int, int], float] = {}
    for r in rows:
        rev = r.get("revenue")
        if rev is None:
            continue
        by_month[(int(r["revenue_year"]), int(r["revenue_month"]))] = float(rev)
    ordered = sorted(by_month)  # chronological (y, m)
    avail: list[pd.Timestamp] = []
    yoys: list[float] = []
    turned: list[bool] = []
    prev_yoy: float | None = None
    # map (y,m) -> row date for availability
    date_of = {}
    for r in rows:
        date_of[(int(r["revenue_year"]), int(r["revenue_month"]))] = r["date"]
    for (y, m) in ordered:
        prior = by_month.get((y - 1, m))
        if prior is None or prior <= 0:
            prev_yoy = None
            continue
        yoy = by_month[(y, m)] / prior - 1.0
        a = pd.Timestamp(date_of[(y, m)]) + pd.Timedelta(days=PUBLISH_LAG_DAYS)
        avail.append(a)
        yoys.append(yoy)
        turned.append(prev_yoy is not None and prev_yoy < 0 <= yoy)
        prev_yoy = yoy
    return avail, yoys, turned


def _pit_yoy(idx, asof: pd.Timestamp):
    avail, yoys, turned = idx
    if not avail:
        return None
    pos = bisect.bisect_right(avail, asof) - 1
    if pos < 0:
        return None
    return yoys[pos], turned[pos]


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
            df = df.dropna(subset=["Close"])
            if len(df) > 250:
                out[sym] = df
    return out


def _breakout_mask(close: pd.Series) -> pd.Series:
    ma5, ma10, ma20, ma240 = (close.rolling(n).mean() for n in (5, 10, 20, 240))
    above = (close > ma5) & (close > ma10) & (close > ma20) & (close > ma240)
    # newly above: today all-above, yesterday not (and yesterday data present)
    return above & ~above.shift(1, fill_value=False) & ma240.notna()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--watchlist", default="data/watchlist.csv")
    ap.add_argument("--start", default="2018-01-01")
    ap.add_argument("--end", default="2024-12-31")
    ap.add_argument("--warmup-start", default="2016-06-01")
    ap.add_argument("--output-csv", default="results/backtests/tw_revenue_yoy_breakout_events.csv")
    args = ap.parse_args()

    if not CACHE.exists():
        sys.exit("revenue cache missing — run scripts/fetch_tw_month_revenue.py first")
    cache = json.loads(CACHE.read_text())
    yoy_index = {code: _build_yoy_index(rows) for code, rows in cache.items() if rows}
    print(f"revenue codes with YoY: {len(yoy_index)}")

    names = _load_tw_names(ROOT / args.watchlist)
    symbols = sorted(set(names) | {BENCHMARK})
    prices = _download(symbols, args.warmup_start, args.end)
    print(f"price symbols loaded: {len(prices)} (of {len(symbols)})")
    if BENCHMARK not in prices:
        sys.exit("benchmark 0050.TW prices missing")
    bench_close = prices[BENCHMARK]["Close"]

    win_start = pd.Timestamp(args.start)
    win_end = pd.Timestamp(args.end)
    events: list[dict] = []
    for symbol, df in prices.items():
        if symbol == BENCHMARK:
            continue
        code = symbol[:-3] if symbol.endswith(".TW") else symbol.split(".")[0]
        idx = yoy_index.get(code)
        if idx is None:
            continue
        close = df["Close"]
        mask = _breakout_mask(close)
        dates = close.index
        for i in range(len(close)):
            if not bool(mask.iloc[i]):
                continue
            d = dates[i]
            if d < win_start or d > win_end:
                continue
            pit = _pit_yoy(idx, d)
            if pit is None:
                continue
            yoy, turned = pit
            rec = {
                "symbol": symbol, "date": d.date().isoformat(),
                "yoy": round(yoy, 4), "yoy_positive": yoy > 0, "turned_positive": turned,
            }
            ok = True
            for w in FWD_WINDOWS:
                if i + w >= len(close):
                    ok = False
                    break
                d_fwd = dates[i + w]
                fwd = close.iloc[i + w] / close.iloc[i] - 1.0
                b0 = bench_close.asof(d)
                b1 = bench_close.asof(d_fwd)
                bench_fwd = (b1 / b0 - 1.0) if b0 and b1 else float("nan")
                rec[f"fwd{w}"] = round(fwd, 4)
                rec[f"excess{w}"] = round(fwd - bench_fwd, 4)
            if ok:
                events.append(rec)

    if not events:
        sys.exit("no breakout events with point-in-time revenue YoY in window")
    ev = pd.DataFrame(events)
    out_path = ROOT / args.output_csv
    ev.to_csv(out_path, index=False)

    def summarize(label: str, sub: pd.DataFrame) -> None:
        if sub.empty:
            print(f"{label:<28} n=0")
            return
        parts = [f"n={len(sub):>4}"]
        for w in FWD_WINDOWS:
            ex = sub[f"excess{w}"]
            parts.append(
                f"ex{w} mean={ex.mean() * 100:+5.2f}% med={ex.median() * 100:+5.2f}% "
                f"win={(ex > 0).mean() * 100:4.1f}%"
            )
        print(f"{label:<28} " + "  ".join(parts))

    print(f"\n=== TW revenue-YoY breakout event study {args.start}..{args.end} ===")
    print(f"events={len(ev)} symbols={ev['symbol'].nunique()} (excess vs {BENCHMARK})\n")
    summarize("ALL breakouts", ev)
    summarize("  YoY > 0", ev[ev["yoy_positive"]])
    summarize("  YoY <= 0", ev[~ev["yoy_positive"]])
    summarize("  YoY turned positive", ev[ev["turned_positive"]])
    print(f"\nCSV -> {out_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
