from screener.fetch import _parse_target_event


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
