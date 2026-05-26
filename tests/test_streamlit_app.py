from datetime import datetime, timedelta, timezone

from streamlit_app import _has_recent_research_report


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
