"""Watchlist + signals JSON I/O."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class WatchlistEntry:
    symbol: str
    market: str
    name: str
    tradingview_symbol: str


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def load_watchlist(market: Optional[str] = None) -> list[WatchlistEntry]:
    path = repo_root() / "data" / "watchlist.csv"
    entries: list[WatchlistEntry] = []
    with path.open() as f:
        reader = csv.DictReader(f)
        for row in reader:
            entry = WatchlistEntry(
                symbol=row["symbol"].strip(),
                market=row["market"].strip().lower(),
                name=row["name"].strip(),
                tradingview_symbol=row["tradingview_symbol"].strip(),
            )
            if market is None or entry.market == market.lower():
                entries.append(entry)
    return entries


def signals_path() -> Path:
    return repo_root() / "data" / "latest_signals.json"


def load_latest_signals() -> dict:
    path = signals_path()
    if not path.exists():
        return {"generated_at": None, "last_run": {}, "signals": {}}
    with path.open() as f:
        return json.load(f)


def write_latest_signals(data: dict) -> None:
    path = signals_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        json.dump(data, f, indent=2, default=str, sort_keys=True)
        f.write("\n")
