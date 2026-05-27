"""Sector classification and relative-strength snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd


MIN_GROUP_MEMBERS = 3
SECTOR_BREADTH_THRESHOLD = 0.50
SECTOR_MAX_SCORE = 1.5


@dataclass(frozen=True)
class SectorMapEntry:
    symbol: str
    market: str
    sector_official: str
    industry_group: str
    industry: str
    source: str


@dataclass(frozen=True)
class SectorSnapshot:
    sector_official: str
    industry_group: str
    industry: str
    source: str
    group_name: str
    member_count: int
    strong_days: int
    return_1d: float
    return_5d: float
    benchmark_return_1d: float
    benchmark_return_5d: float
    breadth_above_ma5: float
    breadth_up_day: float


def sector_score(strong_days: int) -> float:
    if strong_days <= 0:
        return 0.0
    if strong_days == 1:
        return 0.5
    if strong_days == 2:
        return 1.0
    if strong_days <= 5:
        return 1.5
    return 1.0


def _metrics(df: pd.DataFrame) -> pd.DataFrame:
    close = df["Close"].astype(float)
    out = pd.DataFrame(index=pd.to_datetime(df.index).tz_localize(None).normalize())
    out["close"] = close.to_numpy()
    out["return_1d"] = (close / close.shift(1) - 1.0).to_numpy()
    out["return_5d"] = (close / close.shift(5) - 1.0).to_numpy()
    out["ma5"] = close.rolling(5).mean().to_numpy()
    out["above_ma5"] = out["close"] > out["ma5"]
    out["up_day"] = out["return_1d"] > 0
    return out


def _safe_float(value) -> Optional[float]:
    if pd.isna(value):
        return None
    return float(value)


def _available_metric(
    metrics_by_symbol: dict[str, pd.DataFrame], symbol: str, date: pd.Timestamp
) -> dict | None:
    frame = metrics_by_symbol.get(symbol)
    if frame is None or date not in frame.index:
        return None
    row = frame.loc[date]
    ret_1d = _safe_float(row["return_1d"])
    ret_5d = _safe_float(row["return_5d"])
    if ret_1d is None or ret_5d is None or pd.isna(row["ma5"]):
        return None
    return {
        "return_1d": ret_1d,
        "return_5d": ret_5d,
        "above_ma5": bool(row["above_ma5"]),
        "up_day": bool(row["up_day"]),
    }


def _group_snapshot_for_date(
    symbols: list[str],
    metrics_by_symbol: dict[str, pd.DataFrame],
    benchmark: pd.DataFrame,
    date: pd.Timestamp,
) -> dict | None:
    bench_row = benchmark.loc[date] if date in benchmark.index else None
    if bench_row is None:
        return None
    bench_1d = _safe_float(bench_row["return_1d"])
    bench_5d = _safe_float(bench_row["return_5d"])
    if bench_1d is None or bench_5d is None:
        return None

    rows = [
        row
        for symbol in symbols
        if (row := _available_metric(metrics_by_symbol, symbol, date)) is not None
    ]
    if len(rows) < MIN_GROUP_MEMBERS:
        return None

    ret_1d = sum(row["return_1d"] for row in rows) / len(rows)
    ret_5d = sum(row["return_5d"] for row in rows) / len(rows)
    breadth_above_ma5 = sum(1 for row in rows if row["above_ma5"]) / len(rows)
    breadth_up_day = sum(1 for row in rows if row["up_day"]) / len(rows)
    strong = (
        ret_1d > 0
        and ret_1d > bench_1d
        and ret_5d > bench_5d
        and breadth_above_ma5 >= SECTOR_BREADTH_THRESHOLD
    )
    return {
        "member_count": len(rows),
        "return_1d": ret_1d,
        "return_5d": ret_5d,
        "benchmark_return_1d": bench_1d,
        "benchmark_return_5d": bench_5d,
        "breadth_above_ma5": breadth_above_ma5,
        "breadth_up_day": breadth_up_day,
        "strong": strong,
    }


def build_sector_snapshots(
    market: str,
    sector_map: dict[str, SectorMapEntry],
    ohlcv_by_symbol: dict[str, pd.DataFrame],
    benchmark_df: pd.DataFrame | None,
    lookback_days: int = 10,
) -> dict[str, SectorSnapshot]:
    if benchmark_df is None or benchmark_df.empty:
        return {}

    market = market.lower()
    entries = {
        symbol: entry
        for symbol, entry in sector_map.items()
        if entry.market == market and symbol in ohlcv_by_symbol and entry.industry_group
    }
    groups: dict[str, list[str]] = {}
    for symbol, entry in entries.items():
        groups.setdefault(entry.industry_group, []).append(symbol)
    groups = {
        group: symbols
        for group, symbols in groups.items()
        if len(symbols) >= MIN_GROUP_MEMBERS
    }
    if not groups:
        return {}

    metrics_by_symbol = {
        symbol: _metrics(df)
        for symbol, df in ohlcv_by_symbol.items()
        if symbol in entries and not df.empty
    }
    benchmark = _metrics(benchmark_df)
    dates = list(benchmark.index[-lookback_days:])
    snapshots_by_group: dict[str, dict] = {}
    for group, symbols in groups.items():
        strong_days = 0
        latest_snapshot: dict | None = None
        for date in reversed(dates):
            current = _group_snapshot_for_date(symbols, metrics_by_symbol, benchmark, date)
            if latest_snapshot is None and current is not None:
                latest_snapshot = current
            if current is None or not current["strong"]:
                if latest_snapshot is not None:
                    break
                continue
            strong_days += 1
        if latest_snapshot is not None:
            snapshots_by_group[group] = {
                **latest_snapshot,
                "strong_days": strong_days,
            }

    out: dict[str, SectorSnapshot] = {}
    for symbol, entry in entries.items():
        current = snapshots_by_group.get(entry.industry_group)
        if current is None:
            continue
        out[symbol] = SectorSnapshot(
            sector_official=entry.sector_official,
            industry_group=entry.industry_group,
            industry=entry.industry,
            source=entry.source,
            group_name=entry.industry_group,
            member_count=int(current["member_count"]),
            strong_days=int(current["strong_days"]),
            return_1d=float(current["return_1d"]),
            return_5d=float(current["return_5d"]),
            benchmark_return_1d=float(current["benchmark_return_1d"]),
            benchmark_return_5d=float(current["benchmark_return_5d"]),
            breadth_above_ma5=float(current["breadth_above_ma5"]),
            breadth_up_day=float(current["breadth_up_day"]),
        )
    return out
