#!/usr/bin/env python3
"""Parse broker-grouped manual target-price updates into draft JSON events."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


BROKER_LINE = re.compile(r"^[^\d\s][^\d]*$")
TARGET_LINE = re.compile(
    r"^(?P<symbol>[A-Za-z]{1,6}|\d{4}(?:\.(?:TW|TWO))?)\s+"
    r"(?P<target>[0-9]+(?:\.[0-9]+)?)$",
    re.IGNORECASE,
)
MAX_SIGNALS_AGE = timedelta(days=7)
DEFAULT_MIN_UPSIDE = 0.20


def parse(text: str, market: str, event_date: str) -> list[dict]:
    firm: str | None = None
    events: list[dict] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.lower() in {
            "股票代碼 目標價",
            "股票代碼\t目標價",
            "symbol target",
            "ticker target",
        }:
            continue

        match = TARGET_LINE.match(line)
        if match:
            if firm is None:
                raise ValueError(f"target row before firm heading: {line}")
            raw_symbol = match.group("symbol").upper()
            events.append(
                {
                    "raw_symbol": raw_symbol,
                    "market": market,
                    "event_date": event_date,
                    "published_at": f"{event_date}T00:00:00+00:00",
                    "firm": firm,
                    "action": "raise",
                    "target_price": float(match.group("target")),
                    "source": "manual",
                }
            )
            continue

        if BROKER_LINE.match(line):
            firm = line
            continue

        raise ValueError(f"unrecognized line: {line}")
    return events


def load_signals(path: Path, now: datetime | None = None) -> dict:
    with path.open(encoding="utf-8") as file:
        payload = json.load(file)

    generated_at = payload.get("generated_at")
    if not isinstance(generated_at, str):
        raise ValueError(f"{path} has no generated_at timestamp")
    try:
        generated = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{path} has an invalid generated_at timestamp") from exc
    if generated.tzinfo is None:
        raise ValueError(f"{path} generated_at must include a timezone")

    now = now or datetime.now(timezone.utc)
    if now - generated.astimezone(timezone.utc) > MAX_SIGNALS_AGE:
        raise ValueError(
            f"{path} is more than {MAX_SIGNALS_AGE.days} days old; refresh market data"
        )
    if generated.astimezone(timezone.utc) > now + timedelta(minutes=5):
        raise ValueError(f"{path} generated_at is in the future")

    signals = payload.get("signals")
    if not isinstance(signals, dict):
        raise ValueError(f"{path} has no signals object")
    return signals


def _signal_for(raw_symbol: str, market: str, signals: dict) -> tuple[str, dict]:
    if raw_symbol in signals:
        return raw_symbol, signals[raw_symbol]

    if market == "tw" and raw_symbol.isdigit():
        matches = [
            (symbol, signal)
            for symbol, signal in signals.items()
            if symbol.startswith(f"{raw_symbol}.")
            and isinstance(signal, dict)
            and signal.get("market") == "tw"
        ]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise ValueError(
                f"ambiguous TW symbol {raw_symbol}; use a watchlist symbol with .TW or .TWO"
            )

    raise ValueError(
        f"no current signal for {raw_symbol}; add it to the watchlist and refresh market data"
    )


def filter_by_upside(
    events: list[dict], signals: dict, min_upside: float = DEFAULT_MIN_UPSIDE
) -> tuple[list[dict], list[tuple[str, float, float]]]:
    if not math.isfinite(min_upside) or min_upside < 0:
        raise ValueError("minimum upside must be a finite, non-negative number")

    included: list[dict] = []
    excluded: list[tuple[str, float, float]] = []
    for event in events:
        symbol, signal = _signal_for(event["raw_symbol"], event["market"], signals)
        indicators = signal.get("indicators")
        price = indicators.get("close") if isinstance(indicators, dict) else None
        if signal.get("status") != "ok" or not isinstance(price, (int, float)):
            raise ValueError(f"no valid current price for {symbol}; refresh market data")
        if not math.isfinite(price) or price <= 0:
            raise ValueError(f"invalid current price for {symbol}: {price}")

        target = event["target_price"]
        upside = target / price - 1
        if target >= price * (1 + min_upside):
            included.append(event)
        else:
            excluded.append((symbol, price, upside))
    return included, excluded


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", choices=["tw", "us"], required=True)
    parser.add_argument("--date", default=date.today().isoformat())
    parser.add_argument(
        "--signals",
        type=Path,
        default=Path(__file__).resolve().parents[4] / "data" / "latest_signals.json",
        help="fresh latest_signals.json snapshot (defaults to this repository's data file)",
    )
    parser.add_argument(
        "--min-upside",
        type=float,
        default=DEFAULT_MIN_UPSIDE,
        help="minimum target upside (default: 0.20 for 20%%)",
    )
    args = parser.parse_args()
    events = parse(sys.stdin.read(), args.market, args.date)
    signals = load_signals(args.signals)
    events, excluded = filter_by_upside(events, signals, args.min_upside)
    for symbol, price, upside in excluded:
        print(
            f"Excluded {symbol}: target upside {upside:.1%} at current price {price:g} "
            f"is below {args.min_upside:.1%}",
            file=sys.stderr,
        )
    print(
        f"Included {len(events)} event(s); excluded {len(excluded)} below the "
        f"{args.min_upside:.1%} upside threshold.",
        file=sys.stderr,
    )
    json.dump(events, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
