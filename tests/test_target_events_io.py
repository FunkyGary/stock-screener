import json

from screener import io


def test_merge_target_events_writes_jsonl_and_dedupes(tmp_path, monkeypatch):
    path = tmp_path / "analyst_target_events.jsonl"
    monkeypatch.setattr(io, "target_events_path", lambda: path)

    event = {
        "symbol": "AAPL",
        "market": "us",
        "event_date": "2026-05-25",
        "published_at": "2026-05-25T13:20:00+00:00",
        "firm": "JPMorgan",
        "action": "raise",
        "previous_target": 450.0,
        "target_price": 500.0,
        "headline": "JPMorgan Raises Apple Price Target to $500 From $450",
        "url": "https://example.com/aapl",
    }

    assert io.merge_target_events([event]) == 1
    assert io.merge_target_events([event]) == 0

    lines = path.read_text().splitlines()
    assert len(lines) == 1
    saved = json.loads(lines[0])
    assert saved["symbol"] == "AAPL"
    assert saved["event_id"]
    assert saved["target_price"] == 500.0


def test_normalize_target_event_uses_published_at_date():
    event = io.normalize_target_event(
        {
            "symbol": "MSFT",
            "market": "us",
            "published_at": "2026-05-25T13:20:00+00:00",
            "firm": "Morgan Stanley",
            "target_price": 600.0,
        }
    )

    assert event["event_date"] == "2026-05-25"
    assert event["event_id"]


def test_merge_tw_target_events_writes_separate_jsonl(tmp_path, monkeypatch):
    path = tmp_path / "tw_target_events.jsonl"
    monkeypatch.setattr(io, "tw_target_events_path", lambda: path)

    event = {
        "symbol": "2330.TW",
        "market": "tw",
        "event_date": "2026-05-25",
        "firm": "凱基投顧",
        "action": "raise",
        "previous_target": 1200.0,
        "target_price": 1300.0,
        "source": "manual",
    }

    assert io.merge_tw_target_events([event]) == 1
    assert io.merge_tw_target_events([event]) == 0

    saved = json.loads(path.read_text().splitlines()[0])
    assert saved["symbol"] == "2330.TW"
    assert saved["market"] == "tw"
    assert saved["event_id"]
