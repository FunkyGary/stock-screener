from datetime import datetime, timedelta, timezone

from streamlit_app import _has_recent_research_report, _is_downside_attention


def test_recent_research_report_uses_target_price_event_dates():
    row = {
        "analyst": {
            "target_price_events": [
                {
                    "published_at": (
                        datetime.now(timezone.utc) - timedelta(days=2)
                    ).isoformat(),
                    "firm": "JPMorgan",
                    "target_price": 500.0,
                }
            ]
        }
    }

    assert _has_recent_research_report(row) is True


def test_recent_research_report_expires_after_seven_days():
    row = {
        "analyst": {
            "target_price_events": [
                {
                    "event_date": (datetime.now(timezone.utc) - timedelta(days=8))
                    .date()
                    .isoformat(),
                    "firm": "JPMorgan",
                    "target_price": 500.0,
                }
            ]
        }
    }

    assert _has_recent_research_report(row) is False


def test_downside_attention_requires_actual_ma5_cross():
    row = {
        "indicators": {
            "close": 95.0,
            "ma5": 100.0,
            "prev_close": 99.0,
            "prev_ma5": 100.0,
            "prev_ma10": 90.0,
            "prev_ma20": 80.0,
            "prev_ma240": 70.0,
        }
    }

    assert _is_downside_attention(row) is False


def test_downside_attention_passes_when_crossing_below_ma5_today():
    row = {
        "indicators": {
            "close": 95.0,
            "ma5": 100.0,
            "prev_close": 101.0,
            "prev_ma5": 100.0,
            "prev_ma10": 90.0,
            "prev_ma20": 80.0,
            "prev_ma240": 70.0,
        }
    }

    assert _is_downside_attention(row) is True
