import pandas as pd

from screener.fetch import _merge_intraday_latest, _parse_target_event


def test_parse_target_price_raise_news_event():
    event = _parse_target_event(
        {
            "datetime": 1779710400,
            "headline": "JPMorgan Raises Apple Price Target to $500 From $450",
            "source": "Dow Jones",
            "url": "https://example.com/aapl",
        }
    )

    assert event is not None
    assert event["firm"] == "JPMorgan"
    assert event["target_price"] == 500.0
    assert event["previous_target"] == 450.0
    assert event["raise_pct"] == 500.0 / 450.0 - 1.0


def test_parse_target_price_event_requires_raise_language():
    event = _parse_target_event(
        {
            "headline": "Apple Price Target Set at $500 by JPMorgan",
            "source": "Dow Jones",
        }
    )

    assert event is None


def test_merge_intraday_latest_overlays_current_session_close():
    daily = pd.DataFrame(
        {
            "Open": [90.0, 100.0],
            "High": [95.0, 105.0],
            "Low": [89.0, 99.0],
            "Close": [94.0, 101.0],
            "Volume": [1000.0, 2000.0],
        },
        index=pd.to_datetime(["2026-05-26", "2026-05-27"]),
    )
    intraday = pd.DataFrame(
        {
            "Open": [102.0, 104.0],
            "High": [106.0, 108.0],
            "Low": [101.0, 103.0],
            "Close": [104.0, 107.0],
            "Volume": [300.0, 400.0],
        },
        index=pd.to_datetime(
            ["2026-05-27 01:00:00+00:00", "2026-05-27 01:05:00+00:00"]
        ),
    )

    merged = _merge_intraday_latest(daily, intraday)

    assert merged.loc[pd.Timestamp("2026-05-27"), "Open"] == 102.0
    assert merged.loc[pd.Timestamp("2026-05-27"), "High"] == 108.0
    assert merged.loc[pd.Timestamp("2026-05-27"), "Low"] == 101.0
    assert merged.loc[pd.Timestamp("2026-05-27"), "Close"] == 107.0
    assert merged.loc[pd.Timestamp("2026-05-27"), "Volume"] == 700.0
