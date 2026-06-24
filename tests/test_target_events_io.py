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


def test_append_valuation_snapshots_appends_and_dedupes(tmp_path, monkeypatch):
    path = tmp_path / "valuation_snapshots.jsonl"
    monkeypatch.setattr(io, "valuation_snapshots_path", lambda: path)

    rows = [
        {"date": "2026-06-18", "symbol": "2330.TW", "market": "tw", "pe": 32.7,
         "pb": 10.6, "eps_surprise_pct": None, "eps_period": None},
        {"date": "2026-06-18", "symbol": "NVDA", "market": "us", "pe": 31.7,
         "pb": 25.7, "eps_surprise_pct": 4.34, "eps_period": "2026-06-30"},
    ]
    assert io.append_valuation_snapshots(rows) == 2
    # Same day re-run: no duplicates.
    assert io.append_valuation_snapshots(rows) == 0
    # Next day: new rows for the same symbols are appended.
    rows_next = [dict(r, date="2026-06-19") for r in rows]
    assert io.append_valuation_snapshots(rows_next) == 2

    lines = path.read_text().splitlines()
    assert len(lines) == 4
    saved = json.loads(lines[0])
    assert {saved["date"], saved["symbol"]} <= {"2026-06-18", "2330.TW", "NVDA"}


def test_append_valuation_snapshots_skips_rows_missing_keys(tmp_path, monkeypatch):
    path = tmp_path / "valuation_snapshots.jsonl"
    monkeypatch.setattr(io, "valuation_snapshots_path", lambda: path)

    rows = [
        {"date": None, "symbol": "X", "pe": 1.0},
        {"date": "2026-06-18", "symbol": None, "pe": 1.0},
    ]
    assert io.append_valuation_snapshots(rows) == 0
    assert not path.exists()
