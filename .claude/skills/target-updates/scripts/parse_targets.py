#!/usr/bin/env python3
"""Parse broker-grouped manual target-price updates into draft JSON events."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date


BROKER_LINE = re.compile(r"^[^\d\s][^\d]*$")
TARGET_LINE = re.compile(
    r"^(?P<symbol>[A-Za-z]{1,6}|\d{4}(?:\.(?:TW|TWO))?)\s+"
    r"(?P<target>[0-9]+(?:\.[0-9]+)?)$",
    re.IGNORECASE,
)


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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", choices=["tw", "us"], required=True)
    parser.add_argument("--date", default=date.today().isoformat())
    args = parser.parse_args()
    events = parse(sys.stdin.read(), args.market, args.date)
    json.dump(events, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
