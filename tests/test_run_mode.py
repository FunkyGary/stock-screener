"""Tests for run.py EOD/intraday mode behavior."""

import pytest

from screener.fetch import AnalystSnapshot
from screener.run import (
    _build_analyst_blob_eod,
    _enrich_target_price_events,
    _target_events_for_history,
)


def test_eod_shift_moves_current_target_into_prev_eod_slot():
    prev = {
        "target_mean": 200.0,
        "rating": "Buy",
        "rating_score": 2.0,
        "target_mean_prev_eod": 195.0,
    }
    new = AnalystSnapshot(
        target_mean=210.0,
        rating="Buy",
        rating_score=2.0,
        target_price_events=[
            {"firm": "JPMorgan", "target_price": 240.0, "previous_target": 210.0}
        ],
    )
    blob = _build_analyst_blob_eod(new, prev, close=200.0)
    assert blob["target_mean"] == 210.0
    assert blob["target_mean_prev_eod"] == 200.0  # yesterday's EOD
    assert blob["target_price_events"][0]["upside_pct"] == pytest.approx(0.2)


def test_eod_shift_falls_back_to_legacy_target_mean_on_first_upgrade():
    # Pre-upgrade record had no target_mean_prev_eod field.
    prev = {"target_mean": 200.0, "rating": "Buy", "rating_score": 2.0}
    new = AnalystSnapshot(target_mean=205.0, rating="Buy", rating_score=2.0)
    blob = _build_analyst_blob_eod(new, prev)
    assert blob["target_mean_prev_eod"] == 200.0


def test_eod_shift_with_no_prev_record_yields_none_baseline():
    new = AnalystSnapshot(target_mean=205.0, rating="Buy", rating_score=2.0)
    blob = _build_analyst_blob_eod(new, prev_blob=None)
    assert blob["target_mean"] == 205.0
    assert blob["target_mean_prev_eod"] is None
    assert blob["target_price_events"] == []


def test_target_price_event_upside_refreshes_against_current_close():
    events = [{"target_price": 110.0}, {"target_price": 90.0}]
    enriched = _enrich_target_price_events(events, close=100.0)
    assert enriched[0]["upside_pct"] == pytest.approx(0.1)
    assert enriched[1]["upside_pct"] == pytest.approx(-0.1)
    assert "upside_pct" not in events[0]


def test_target_events_for_history_adds_symbol_close_and_event_id():
    rows = _target_events_for_history(
        symbol="AAPL",
        market="us",
        close=400.0,
        fetched_at="2026-05-25T22:00:00+00:00",
        events=[
            {
                "date": "2026-05-25",
                "published_at": "2026-05-25T13:20:00+00:00",
                "firm": "JPMorgan",
                "target_price": 500.0,
                "previous_target": 450.0,
                "raise_pct": 500.0 / 450.0 - 1.0,
                "headline": "JPMorgan Raises Apple Price Target to $500 From $450",
                "source": "Dow Jones",
                "url": "https://example.com/aapl",
            }
        ],
    )

    assert rows[0]["event_id"]
    assert rows[0]["symbol"] == "AAPL"
    assert rows[0]["close_at_fetch"] == 400.0
    assert rows[0]["upside_pct"] == pytest.approx(0.25)
