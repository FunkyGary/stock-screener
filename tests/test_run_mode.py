"""Tests for run.py EOD/intraday mode behavior — specifically the analyst
target_mean_prev_eod shift used by the US 'target raise >3%' rule."""

from datetime import datetime, timedelta, timezone

from screener.fetch import AnalystSnapshot
from screener.run import _build_analyst_blob_eod


def test_eod_shift_moves_current_target_into_prev_eod_slot():
    prev = {
        "target_mean": 200.0,
        "rating": "Buy",
        "rating_score": 2.0,
        "target_mean_prev_eod": 195.0,
    }
    new = AnalystSnapshot(target_mean=210.0, rating="Buy", rating_score=2.0)
    blob = _build_analyst_blob_eod(new, prev)
    assert blob["target_mean"] == 210.0
    assert blob["target_mean_prev_eod"] == 200.0  # yesterday's EOD
    assert blob["target_raise_detected_at"] is not None
    assert blob["target_raise_from"] == 200.0
    assert blob["target_raise_to"] == 210.0


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
    assert blob["target_raise_detected_at"] is None


def test_target_raise_event_remains_active_for_three_days():
    now = datetime(2026, 5, 25, tzinfo=timezone.utc)
    prev = {
        "target_mean": 210.0,
        "rating": "Buy",
        "rating_score": 2.0,
        "target_raise_detected_at": (now - timedelta(days=1)).isoformat(),
        "target_raise_valid_until": (now + timedelta(days=2)).isoformat(),
        "target_raise_from": 200.0,
        "target_raise_to": 210.0,
        "target_raise_pct": 0.05,
    }
    new = AnalystSnapshot(target_mean=210.0, rating="Buy", rating_score=2.0)
    blob = _build_analyst_blob_eod(new, prev, now=now)
    assert blob["target_raise_detected_at"] == prev["target_raise_detected_at"]
    assert blob["target_raise_valid_until"] == prev["target_raise_valid_until"]


def test_target_raise_event_expires_after_three_days():
    now = datetime(2026, 5, 25, tzinfo=timezone.utc)
    prev = {
        "target_mean": 210.0,
        "rating": "Buy",
        "rating_score": 2.0,
        "target_raise_detected_at": (now - timedelta(days=4)).isoformat(),
        "target_raise_valid_until": (now - timedelta(days=1)).isoformat(),
        "target_raise_from": 200.0,
        "target_raise_to": 210.0,
        "target_raise_pct": 0.05,
    }
    new = AnalystSnapshot(target_mean=210.0, rating="Buy", rating_score=2.0)
    blob = _build_analyst_blob_eod(new, prev, now=now)
    assert blob["target_raise_detected_at"] is None
    assert blob["target_raise_valid_until"] is None
