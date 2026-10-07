"""Turnover-control sweep for the TW strategy, net of trading costs.

Signals are fixed (default weights, newly-above-all entry, MA5 exit) and shared
by every variant; only portfolio rules change: concurrent-position cap, how
same-day candidates are ranked, and a minimum holding period. Ranking only
matters under a cap, so cap=None runs a single ranking.
"""

from __future__ import annotations

import argparse
import itertools
import sys
import zlib
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import scripts.backtest_tw_strategy as bt  # noqa: E402

PERIODS = (
    ("2016", "2016-01-04", "2016-12-30"),
    ("2017", "2017-01-03", "2017-12-29"),
    ("2018", "2018-01-02", "2018-12-28"),
    ("2019", "2019-01-02", "2019-12-31"),
    ("2020", "2020-01-02", "2020-12-31"),
    ("2021", "2021-01-04", "2021-12-30"),
    ("2022", "2022-01-03", "2022-12-30"),
    ("2023", "2023-01-03", "2023-12-29"),
    ("2024", "2024-01-02", "2024-12-31"),
    ("2025-26", "2025-01-02", "2026-05-28"),
)
CAPS = (None, 20, 10, 5)
MIN_HOLDS = (0, 3, 5, 10)
RANKS = ("score", "score_low", "mom20", "ext_low", "rand0", "rand1", "rand2")
DEFAULT_COST = 0.004  # TW round trip: 0.3% tax + ~0.1% fees

_STATE: dict = {}


def _rank_key(name: str):
    if name == "score":
        return bt.default_rank_key
    if name == "score_low":
        return lambda _s, row: (-row["ratio"], -row["score"])
    if name == "mom20":
        return lambda _s, row: -9.0 if row["ret20"] is None else row["ret20"]
    if name == "ext_low":
        return lambda _s, row: -9.0 if row["ext"] is None else -row["ext"]
    seed = name.removeprefix("rand")
    return lambda symbol, row: zlib.crc32(
        f"{seed}|{symbol}|{row['score']:.6f}|{row['ret20']}".encode()
    )


def _variants(cost: float) -> list[tuple[int | None, str, int, float]]:
    """(cap, rank, min_hold, cost) jobs, plus the zero-cost uncapped baseline."""
    out: list[tuple[int | None, str, int, float]] = []
    for cap, hold in itertools.product(CAPS, MIN_HOLDS):
        ranks = ("score",) if cap is None else RANKS
        out.extend((cap, rank, hold, cost) for rank in ranks)
    out.append((None, "score", 0, 0.0))
    return out


def _init(data, signals, names, dates):
    _STATE.update(data=data, signals=signals, names=names, dates=dates)


def _run(variant: tuple[int | None, str, int, float]) -> dict:
    cap, rank, hold, cost = variant
    curve, trades, holdings = bt.run_backtest(
        _STATE["data"],
        _STATE["signals"],
        _STATE["names"],
        _STATE["dates"],
        max_positions=cap,
        min_hold_days=hold,
        round_trip_cost=cost,
        rank_key=_rank_key(rank),
    )
    summary = bt._summarize("t", _STATE["dates"], _STATE["data"], trades, holdings, curve)
    # cap=0 means uncapped (keeps the CSV column numeric).
    summary.update(cap=cap if cap is not None else 0, rank=rank, min_hold=hold, cost=cost)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache-dir", default=str(ROOT / "results" / "backtests" / "_cache"))
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--cost", type=float, default=DEFAULT_COST)
    parser.add_argument("--watchlist", default="data/watchlist.csv")
    parser.add_argument("--output-csv", default="results/backtests/tw_turnover_sweep.csv")
    args = parser.parse_args()

    names_all = bt._load_tw_symbols(Path(args.watchlist))
    symbols = sorted(set(names_all) | {bt.BENCHMARK})
    cache_dir = Path(args.cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    for label, start, end in PERIODS:
        cache = cache_dir / f"tw_turnover_{label}.pkl"
        if cache.exists():
            data = pd.read_pickle(cache)
        else:
            data = bt._download_range(symbols, start, end)
            pd.to_pickle(data, cache)
        names = {s: n for s, n in names_all.items() if s in data}
        bench_ind = bt._indicators_for_frame(data[bt.BENCHMARK])
        dates = bt._date_slice(sorted(data[bt.BENCHMARK].index), start, end, 252)
        facts = bt._build_signal_facts({s: data[s] for s in names}, bench_ind)
        signals = bt._signals_from_facts(facts, bt.DEFAULT_WEIGHTS, "newly_above_all")
        jobs = _variants(args.cost)
        with ProcessPoolExecutor(
            args.jobs, initializer=_init, initargs=(data, signals, names, dates)
        ) as pool:
            for summary in pool.map(_run, jobs):
                summary["period"] = label
                rows.append(summary)
        print(f"{label}: {len(jobs)} variants done", flush=True)
    frame = pd.DataFrame(rows).drop(columns=["label"])
    Path(args.output_csv).parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(args.output_csv, index=False)
    print(f"wrote {args.output_csv} ({len(frame)} rows)")


if __name__ == "__main__":
    main()
