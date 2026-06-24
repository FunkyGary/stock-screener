"""Cache TW monthly revenue history from FinMind (free, anonymous).

Point-in-time aware: FinMind dates each row at the first day of the *reporting*
month (revenue for (revenue_year, revenue_month) appears with ``date`` = first
day of the following month). Taiwan law requires monthly revenue to be published
by the 10th of that following month, so a row is only safely usable from the
11th of its ``date`` month onward. The backtest applies that lag; this script
only caches the raw rows.

Usage:
    uv run python scripts/fetch_tw_month_revenue.py --start 2015-01-01
Writes results/backtests/_cache/tw_month_revenue.json
    { stock_id: [ {date, revenue, revenue_year, revenue_month}, ... ] }
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "results" / "backtests" / "_cache" / "tw_month_revenue.json"
FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"


def _load_tw_codes(watchlist: Path) -> list[str]:
    rows = pd.read_csv(watchlist)
    codes: list[str] = []
    for row in rows.itertuples(index=False):
        if str(row.market) != "tw":
            continue
        symbol = str(row.symbol)
        if symbol[:2] == "00":  # skip ETFs
            continue
        bare = symbol[:-3] if symbol.endswith(".TW") else symbol.split(".")[0]
        codes.append(bare)
    return sorted(set(codes))


def _fetch_one(code: str, start: str, end: str) -> list[dict] | None:
    params = {
        "dataset": "TaiwanStockMonthRevenue",
        "data_id": code,
        "start_date": start,
        "end_date": end,
    }
    try:
        r = requests.get(FINMIND_URL, params=params, timeout=30)
    except requests.RequestException as exc:
        print(f"  {code}: request error {exc}", file=sys.stderr)
        return None
    if r.status_code != 200:
        print(f"  {code}: HTTP {r.status_code} {r.text[:120]}", file=sys.stderr)
        return None
    payload = r.json()
    if payload.get("status") != 200:
        print(f"  {code}: api status {payload.get('status')} {payload.get('msg')}",
              file=sys.stderr)
        return None
    return [
        {
            "date": row["date"],
            "revenue": row["revenue"],
            "revenue_year": row["revenue_year"],
            "revenue_month": row["revenue_month"],
        }
        for row in payload.get("data", [])
    ]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--watchlist", default="data/watchlist.csv")
    ap.add_argument("--start", default="2015-01-01")
    ap.add_argument("--end", default="2025-12-31")
    args = ap.parse_args()

    codes = _load_tw_codes(ROOT / args.watchlist)
    print(f"TW codes to fetch: {len(codes)}")

    CACHE.parent.mkdir(parents=True, exist_ok=True)
    cache: dict[str, list[dict]] = {}
    if CACHE.exists():
        cache = json.loads(CACHE.read_text())
        print(f"resuming with {len(cache)} cached codes")

    ok = 0
    rate_limited = False
    for i, code in enumerate(codes, 1):
        if code in cache and cache[code]:
            ok += 1
            continue
        rows = _fetch_one(code, args.start, args.end)
        if rows is None:
            # Distinguish rate limit (stop early) from a single bad symbol.
            rate_limited = True
            break
        cache[code] = rows
        ok += 1
        if i % 25 == 0:
            CACHE.write_text(json.dumps(cache, ensure_ascii=False))
            print(f"  {i}/{len(codes)} fetched (ok={ok})")

    CACHE.write_text(json.dumps(cache, ensure_ascii=False))
    nonempty = sum(1 for v in cache.values() if v)
    print(f"done: cached={len(cache)} nonempty={nonempty} "
          f"rate_limited_stop={rate_limited} -> {CACHE.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
