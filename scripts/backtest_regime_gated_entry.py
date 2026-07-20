"""Exp 3 (v2): regime-gated entry, NO force-selling (pure stocks + cash).

Tests the refined idea: "in a weak regime, stop BUYING new positions; held
stocks are NOT force-sold — they exit only on their own sell signal (close<MA5,
i.e. take profit / stop), and the freed cash just sits idle." The book is
purely {individual stocks + cash}; benchmark parking is disabled in every mode
(matching the user's mental model — leftover money is cash, not parked in the
index).

Run continuously over 2018-2026 (so regime transitions happen) per market.
Modes, compared against benchmark buy-and-hold:

  baseline            : new entries allowed in all regimes.
  no_add_bear         : stop new entries only in bear (bull + range still buy).
  no_add_range_bear   : stop new entries in range AND bear (bull-only buying).

Exits (close<MA5) are always active in every mode, so the ONLY variable is
whether new entries are paused in the weak regime. `range` is the catch-all
default regime (~44% of days). Prices are adjusted (auto_adjust).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import scripts.backtest_tw_strategy as tw  # noqa: E402
import scripts.backtest_us_strategy as us  # noqa: E402
from scripts.backtest_gap_exposure import _classify_regime  # noqa: E402

DEFAULT_START = "2018-01-01"
DEFAULT_END = "2026-06-30"

# Which regimes allow NEW entries per gate mode (held positions always exit on
# their own sell signal regardless — never force-sold).
GATE_INVEST_REGIMES = {
    "baseline": {"bull", "range", "bear_crash", "bear_downtrend"},
    "no_add_bear": {"bull", "range"},          # stop new entries only in bear
    "no_add_range_bear": {"bull"},             # stop new entries in range + bear
}


def _max_drawdown(equity: pd.Series) -> float:
    if equity.empty:
        return 0.0
    return float((equity / equity.cummax() - 1.0).min())


def _run(mod, data, signals, names, dates, regime, mode):
    """Pure {individual stocks + cash} portfolio with a regime entry-gate.

    Models the user's intent: in a gated regime we only STOP NEW ENTRIES — held
    positions are never force-sold, they exit only on their normal sell signal
    (close < MA5), and the freed proceeds simply sit in cash (no benchmark
    parking, no re-buying). Benchmark parking is disabled in every mode so the
    book is purely stocks + cash, matching the user's mental model.

    Decisions for `date` use the regime known at the prior close (no lookahead);
    trades execute at `date`'s open.
    """
    entry_regimes = GATE_INVEST_REGIMES[mode]
    cash = mod.INITIAL_CAPITAL
    holdings: dict[str, mod.Holding] = {}
    equity_curve: list[tuple[pd.Timestamp, float]] = []
    cash_weight: list[float] = []
    buys = sells = 0
    pending = signals.get(dates[0], {})
    prev_date = dates[0]

    for date in dates[1:]:
        allow_entry = regime.get(prev_date, "range") in entry_regimes

        # Exits (always active, every regime): close < MA5 -> sell at open.
        for symbol in list(holdings):
            if pending.get(symbol, {}).get("sell"):
                price = mod._open_price(data, symbol, date)
                if price is None:
                    continue
                holding = holdings.pop(symbol)
                cash += holding.shares * price
                sells += 1

        # New entries only when the regime permits; freed cash otherwise idles.
        if allow_entry:
            candidates = [
                (symbol, row)
                for symbol, row in pending.items()
                if row["special"] and symbol not in holdings
            ]
            candidates.sort(key=lambda item: (item[1]["ratio"], item[1]["score"]), reverse=True)
            for symbol, row in candidates:
                price = mod._open_price(data, symbol, date)
                if price is None or price <= 0:
                    continue
                spend = min(mod.ACTIVE_SLOT, cash)
                if spend <= 0:
                    break
                holdings[symbol] = mod.Holding(shares=spend / price, cost=spend)
                cash -= spend
                buys += 1

        value = mod._portfolio_value(data, holdings, 0.0, cash, date)
        equity_curve.append((date, value))
        cash_weight.append(cash / value if value > 0 else 0.0)
        pending = signals.get(date, {})
        prev_date = date

    trades = {"buys": buys, "sells": sells, "open_positions": len(holdings)}
    return equity_curve, cash_weight, trades


def _benchmark_curve(mod, data, dates):
    cash = mod.INITIAL_CAPITAL
    shares, cash = mod._buy_benchmark(data, cash, dates[0])
    return [
        (date, mod._portfolio_value(data, {}, shares, cash, date))
        for date in dates[1:]
    ]


def _summ(label, market, curve, initial, cash_weight=None, trades=None):
    eq = pd.Series({d: v for d, v in curve})
    ret = eq.iloc[-1] / initial - 1.0
    mdd = _max_drawdown(eq)
    row = {
        "market": market, "mode": label,
        "total_return_pct": round(ret * 100, 2),
        "max_drawdown_pct": round(mdd * 100, 2),
    }
    if cash_weight is not None:
        row["avg_cash_pct"] = round(float(pd.Series(cash_weight).mean()) * 100, 1)
    if trades is not None:
        row.update(trades)
    return row


def run_market(mod, loader, market, start, end):
    initial = mod.INITIAL_CAPITAL
    names = loader(Path("data/watchlist.csv"))
    symbols = sorted(set(names) | {mod.BENCHMARK})
    data = mod._download_range(symbols, start, end)
    if mod.BENCHMARK not in data:
        raise RuntimeError(f"missing benchmark {mod.BENCHMARK}")
    names = {s: n for s, n in names.items() if s in data}
    benchmark_ind = mod._indicators_for_frame(data[mod.BENCHMARK])
    regime = _classify_regime(data[mod.BENCHMARK]).to_dict()
    all_dates = sorted(data[mod.BENCHMARK].index)
    dates = [d for d in all_dates if pd.Timestamp(start) <= d <= pd.Timestamp(end)]
    signal_data = {s: data[s] for s in names}
    signals = mod._signals_from_facts(
        mod._build_signal_facts(signal_data, benchmark_ind), mod.DEFAULT_WEIGHTS
    )

    rows = []
    bh = _benchmark_curve(mod, data, dates)
    rows.append(_summ("benchmark_buy_hold", market, bh, initial,
                      trades={"buys": 1, "sells": 0, "open_positions": 1}))
    for mode in ("baseline", "no_add_bear", "no_add_range_bear"):
        curve, cw, trades = _run(mod, data, signals, names, dates, regime, mode)
        rows.append(_summ(mode, market, curve, initial, cw, trades))
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--markets", nargs="+", choices=["tw", "us"], default=["tw", "us"])
    ap.add_argument("--start", default=DEFAULT_START)
    ap.add_argument("--end", default=DEFAULT_END)
    ap.add_argument("--output-csv", default="results/backtests/regime_gated_entry.csv")
    args = ap.parse_args()

    rows = []
    for market in args.markets:
        mod, loader = (tw, tw._load_tw_symbols) if market == "tw" else (us, us._load_us_symbols)
        rows += run_market(mod, loader, market, args.start, args.end)

    frame = pd.DataFrame(rows)
    print(frame.to_string(index=False))
    if args.output_csv:
        Path(args.output_csv).parent.mkdir(parents=True, exist_ok=True)
        frame.to_csv(args.output_csv, index=False)
        print(f"\nwrote {args.output_csv}")


if __name__ == "__main__":
    main()
