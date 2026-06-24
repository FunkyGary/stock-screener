"""Watchlist + signals JSON I/O."""

from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from .sectors import SectorMapEntry


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


def target_events_path() -> Path:
    return repo_root() / "data" / "analyst_target_events.jsonl"


def tw_target_events_path() -> Path:
    return repo_root() / "data" / "tw_target_events.jsonl"


def valuation_snapshots_path() -> Path:
    return repo_root() / "data" / "valuation_snapshots.jsonl"


def sector_map_path() -> Path:
    return repo_root() / "data" / "sector_map.csv"


def load_sector_map() -> dict[str, SectorMapEntry]:
    path = sector_map_path()
    if not path.exists():
        return {}

    out: dict[str, SectorMapEntry] = {}
    with path.open() as f:
        reader = csv.DictReader(f)
        for row in reader:
            symbol = (row.get("symbol") or "").strip()
            market = (row.get("market") or "").strip().lower()
            industry_group = (row.get("industry_group") or "").strip()
            if not symbol or not market or not industry_group:
                continue
            out[symbol] = SectorMapEntry(
                symbol=symbol,
                market=market,
                sector_official=(row.get("sector_official") or "").strip(),
                industry_group=industry_group,
                industry=(row.get("industry") or "").strip(),
                source=(row.get("source") or "").strip(),
            )
    return out


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


def _load_jsonl_events(path: Path) -> list[dict]:
    if not path.exists():
        return []

    events: list[dict] = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            events.append(json.loads(line))
    return events


def append_valuation_snapshots(rows: list[dict]) -> int:
    """Append point-in-time PE/PB/EPS-surprise rows, deduped by (date, symbol).

    Each row should carry at least `date` and `symbol`. Rows whose (date, symbol)
    already exist in the log are skipped, so re-running an EOD job for the same
    day does not duplicate entries. Returns the number of rows written.
    """
    if not rows:
        return 0

    path = valuation_snapshots_path()
    existing = _load_jsonl_events(path)
    seen = {(r.get("date"), r.get("symbol")) for r in existing}

    to_write = []
    for row in rows:
        key = (row.get("date"), row.get("symbol"))
        if key in seen or key[0] is None or key[1] is None:
            continue
        seen.add(key)
        to_write.append(row)

    if not to_write:
        return 0

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        for row in to_write:
            json.dump(row, f, default=str, ensure_ascii=False, sort_keys=True)
            f.write("\n")
    return len(to_write)


def load_target_events() -> list[dict]:
    return _load_jsonl_events(target_events_path())


def load_tw_target_events() -> list[dict]:
    return _load_jsonl_events(tw_target_events_path())


def _target_event_identity(event: dict) -> str:
    parts = [
        event.get("symbol"),
        event.get("published_at") or event.get("event_date"),
        event.get("firm") or event.get("source"),
        event.get("previous_target"),
        event.get("target_price"),
        event.get("url") or event.get("headline"),
    ]
    raw = "|".join("" if part is None else str(part) for part in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def normalize_target_event(event: dict) -> dict:
    out = dict(event)
    if not out.get("event_date"):
        published_at = out.get("published_at")
        out["event_date"] = (
            published_at[:10] if isinstance(published_at, str) else out.get("date")
        )
    out.pop("date", None)
    out["event_id"] = out.get("event_id") or _target_event_identity(out)
    return out


def _merge_target_events_into(
    path: Path, existing_events: list[dict], new_events: list[dict], keep_days: int
) -> int:
    if not new_events:
        return 0

    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=keep_days)).date()
    merged: dict[str, dict] = {}
    for event in existing_events:
        normalized = normalize_target_event(event)
        event_date = normalized.get("event_date")
        if isinstance(event_date, str):
            try:
                if datetime.fromisoformat(event_date).date() < cutoff:
                    continue
            except ValueError:
                pass
        merged[normalized["event_id"]] = normalized

    before = set(merged)
    for event in new_events:
        normalized = normalize_target_event(event)
        merged[normalized["event_id"]] = {
            **merged.get(normalized["event_id"], {}),
            **normalized,
        }

    ordered = sorted(
        merged.values(),
        key=lambda e: (
            e.get("published_at") or e.get("event_date") or "",
            e.get("symbol") or "",
            e.get("firm") or "",
        ),
    )

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for event in ordered:
            json.dump(event, f, default=str, ensure_ascii=False, sort_keys=True)
            f.write("\n")

    return len(set(merged) - before)


def merge_target_events(new_events: list[dict], keep_days: int = 370) -> int:
    return _merge_target_events_into(
        target_events_path(), load_target_events(), new_events, keep_days
    )


def merge_tw_target_events(new_events: list[dict], keep_days: int = 370) -> int:
    return _merge_target_events_into(
        tw_target_events_path(), load_tw_target_events(), new_events, keep_days
    )
