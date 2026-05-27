import pandas as pd
import pytest

from screener.sectors import SectorMapEntry, build_sector_snapshots, sector_score


def _frame(closes):
    index = pd.date_range("2026-01-01", periods=len(closes), freq="D")
    return pd.DataFrame(
        {
            "Open": closes,
            "High": closes,
            "Low": closes,
            "Close": closes,
            "Volume": [1000] * len(closes),
        },
        index=index,
    )


def test_sector_score_tapers_after_five_days():
    assert sector_score(0) == 0.0
    assert sector_score(1) == 0.5
    assert sector_score(2) == 1.0
    assert sector_score(3) == 1.5
    assert sector_score(5) == 1.5
    assert sector_score(6) == 1.0


def test_build_sector_snapshots_requires_three_member_group_and_counts_streak():
    sector_map = {
        symbol: SectorMapEntry(
            symbol=symbol,
            market="tw",
            sector_official="半導體業",
            industry_group="半導體業",
            industry="半導體業",
            source="test",
        )
        for symbol in ("2330.TW", "2303.TW", "3711.TW")
    }
    ohlcv = {
        "2330.TW": _frame([100, 101, 102, 103, 104, 105, 108, 111]),
        "2303.TW": _frame([50, 51, 52, 53, 54, 55, 57, 59]),
        "3711.TW": _frame([80, 81, 82, 83, 84, 85, 87, 89]),
    }
    benchmark = _frame([100, 100, 100, 100, 100, 101, 102, 103])

    snapshots = build_sector_snapshots(
        "tw", sector_map, ohlcv, benchmark, lookback_days=5
    )

    snap = snapshots["2330.TW"]
    assert snap.group_name == "半導體業"
    assert snap.member_count == 3
    assert snap.strong_days >= 2
    assert snap.return_5d > snap.benchmark_return_5d
    assert snap.breadth_above_ma5 == pytest.approx(1.0)
