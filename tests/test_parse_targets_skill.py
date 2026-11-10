import importlib.util
from pathlib import Path

import pytest


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / ".claude"
    / "skills"
    / "target-updates"
    / "scripts"
    / "parse_targets.py"
)
SPEC = importlib.util.spec_from_file_location("parse_targets_skill", SCRIPT)
assert SPEC is not None
assert SPEC.loader is not None
parse_targets = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(parse_targets)


def test_filter_by_upside_keeps_exact_twenty_percent_and_excludes_below():
    events = [
        {"raw_symbol": "2330", "market": "tw", "target_price": 120.0},
        {"raw_symbol": "2330", "market": "tw", "target_price": 119.99},
    ]
    signals = {
        "2330.TW": {
            "market": "tw",
            "status": "ok",
            "indicators": {"close": 100.0},
        }
    }

    included, excluded = parse_targets.filter_by_upside(events, signals)

    assert included == [events[0]]
    assert excluded == [("2330.TW", 100.0, pytest.approx(0.1999))]


def test_filter_by_upside_fails_when_price_is_unavailable():
    events = [{"raw_symbol": "AAPL", "market": "us", "target_price": 250.0}]
    signals = {"AAPL": {"market": "us", "status": "fetch_failed", "indicators": {}}}

    with pytest.raises(ValueError, match="no valid current price for AAPL"):
        parse_targets.filter_by_upside(events, signals)


def test_load_signals_rejects_stale_snapshot(tmp_path):
    path = tmp_path / "latest_signals.json"
    path.write_text(
        '{"generated_at":"2026-01-01T00:00:00+00:00","signals":{}}',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="more than 7 days old"):
        parse_targets.load_signals(
            path, now=parse_targets.datetime(2026, 1, 9, tzinfo=parse_targets.timezone.utc)
        )
