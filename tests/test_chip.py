from screener.chip import (
    ChipDay,
    _index_fields,
    _parse_int,
    _streak,
    _strip_tw_suffix,
    compute_chip_snapshot,
)


def test_strip_tw_suffix():
    assert _strip_tw_suffix("2330.TW") == "2330"
    assert _strip_tw_suffix("AAPL") is None
    assert _strip_tw_suffix("3008.TWO") is None  # TPEx not supported here


def test_parse_int_handles_commas_and_blanks():
    assert _parse_int("1,234,567") == 1234567
    assert _parse_int("-5,000") == -5000
    assert _parse_int(" 0 ") == 0
    assert _parse_int("--") == 0
    assert _parse_int("") == 0


def test_index_fields_resolves_by_substring():
    fields = [
        "證券代號",
        "證券名稱",
        "外陸資買進股數(不含外資自營商)",
        "外陸資賣出股數(不含外資自營商)",
        "外陸資買賣超股數(不含外資自營商)",
        "外資自營商買進股數",
        "外資自營商賣出股數",
        "外資自營商買賣超股數",
        "投信買進股數",
        "投信賣出股數",
        "投信買賣超股數",
        "自營商買賣超股數",
    ]
    idx = _index_fields(fields)
    assert idx["symbol"] == 0
    assert idx["foreign_net"] == 4
    assert idx["trust_net"] == 10


def test_streak_counts_consecutive_positive_from_newest():
    days = [
        ChipDay(date="20260520", foreign_net=100, trust_net=50, total_volume=0),
        ChipDay(date="20260519", foreign_net=200, trust_net=30, total_volume=0),
        ChipDay(date="20260516", foreign_net=10, trust_net=-5, total_volume=0),
        ChipDay(date="20260515", foreign_net=-50, trust_net=20, total_volume=0),
    ]
    assert _streak(days, "foreign_net") == 3  # 100, 200, 10 then -50 breaks
    assert _streak(days, "trust_net") == 2  # 50, 30 then -5 breaks


def test_streak_zero_when_today_is_sell():
    days = [
        ChipDay(date="20260520", foreign_net=-100, trust_net=0, total_volume=0),
        ChipDay(date="20260519", foreign_net=200, trust_net=30, total_volume=0),
    ]
    assert _streak(days, "foreign_net") == 0
    # trust_net == 0 is not > 0 → streak 0
    assert _streak(days, "trust_net") == 0


def test_compute_chip_snapshot_for_us_symbol_returns_none():
    chip_by_symbol = {"2330": [ChipDay("20260520", 100, 50, 0)]}
    assert compute_chip_snapshot("AAPL", chip_by_symbol, today_volume=1000) is None


def test_compute_chip_snapshot_computes_pct_of_volume():
    chip_by_symbol = {
        "2330": [
            ChipDay("20260520", foreign_net=500, trust_net=100, total_volume=0),
            ChipDay("20260519", foreign_net=300, trust_net=80, total_volume=0),
        ]
    }
    snap = compute_chip_snapshot("2330.TW", chip_by_symbol, today_volume=10000)
    assert snap is not None
    assert snap.trust_streak_days == 2
    assert snap.foreign_streak_days == 2
    assert snap.foreign_net_today == 500
    assert snap.foreign_pct_of_volume == 0.05


def test_compute_chip_snapshot_missing_symbol_returns_none():
    snap = compute_chip_snapshot("9999.TW", {}, today_volume=1000)
    assert snap is None
