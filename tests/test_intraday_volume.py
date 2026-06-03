import pandas as pd
import pytest

from screener.intraday_volume import project_intraday_volumes
from screener.sectors import SectorMapEntry


def _daily(symbol_days: int = 25, volume: float = 1000.0) -> pd.DataFrame:
    dates = pd.date_range("2026-04-01", periods=symbol_days)
    return pd.DataFrame(
        {
            "Open": [10.0] * symbol_days,
            "High": [10.0] * symbol_days,
            "Low": [10.0] * symbol_days,
            "Close": [10.0] * symbol_days,
            "Volume": [volume] * symbol_days,
        },
        index=dates,
    )


def _intraday(symbol: str, history_days: int, current_volume: float) -> pd.DataFrame:
    rows = []
    index = []
    for day in pd.date_range("2026-05-01", periods=history_days):
        for ts, volume in (
            ("09:00", 100.0),
            ("09:30", 200.0),
            ("13:25", 700.0),
        ):
            index.append(pd.Timestamp(f"{day.date()} {ts}", tz="Asia/Taipei"))
            rows.append([10.0, 10.0, 10.0, 10.0, volume])
    current_day = pd.Timestamp("2026-05-20")
    for ts, volume in (("09:00", current_volume / 2), ("09:30", current_volume / 2)):
        index.append(pd.Timestamp(f"{current_day.date()} {ts}", tz="Asia/Taipei"))
        rows.append([10.0, 10.0, 10.0, 10.0, volume])
    return pd.DataFrame(
        rows,
        columns=["Open", "High", "Low", "Close", "Volume"],
        index=pd.DatetimeIndex(index, name=symbol),
    )


def _entry(symbol: str, group: str) -> SectorMapEntry:
    return SectorMapEntry(
        symbol=symbol,
        market="tw",
        sector_official=group,
        industry_group=group,
        industry=group,
        source="test",
    )


def test_symbol_curve_projects_volume_from_historical_cumulative_share():
    projections = project_intraday_volumes(
        market="tw",
        daily_by_symbol={"2330.TW": _daily()},
        intraday_by_symbol={"2330.TW": _intraday("2330.TW", 5, 600.0)},
        sector_map={"2330.TW": _entry("2330.TW", "半導體業")},
    )

    projection = projections["2330.TW"]

    assert projection.source == "symbol"
    assert projection.reliable is True
    assert projection.historical_cum_share == pytest.approx(0.3)
    assert projection.projected_volume == pytest.approx(2000.0)
    assert projection.projected_vol_ratio == pytest.approx(2.0)
    assert projection.same_time_vol_ratio == pytest.approx(2.0)


def test_industry_curve_fallback_when_symbol_history_is_sparse():
    projections = project_intraday_volumes(
        market="tw",
        daily_by_symbol={"2330.TW": _daily(), "2303.TW": _daily()},
        intraday_by_symbol={
            "2330.TW": _intraday("2330.TW", 0, 600.0),
            "2303.TW": _intraday("2303.TW", 10, 300.0),
        },
        sector_map={
            "2330.TW": _entry("2330.TW", "半導體業"),
            "2303.TW": _entry("2303.TW", "半導體業"),
        },
    )

    projection = projections["2330.TW"]

    assert projection.source == "industry"
    assert projection.projected_volume == pytest.approx(2000.0)


def test_open_guard_marks_projection_unreliable_before_thirty_minutes():
    intraday = _intraday("2330.TW", 5, 600.0)
    intraday = intraday[intraday.index.time != pd.Timestamp("09:30").time()]

    projections = project_intraday_volumes(
        market="tw",
        daily_by_symbol={"2330.TW": _daily()},
        intraday_by_symbol={"2330.TW": intraday},
        sector_map={"2330.TW": _entry("2330.TW", "半導體業")},
    )

    projection = projections["2330.TW"]

    assert projection.projected_volume is not None
    assert projection.reliable is False


def test_projected_volume_ratio_is_capped():
    projections = project_intraday_volumes(
        market="tw",
        daily_by_symbol={"2330.TW": _daily(volume=1000.0)},
        intraday_by_symbol={"2330.TW": _intraday("2330.TW", 5, 3000.0)},
        sector_map={"2330.TW": _entry("2330.TW", "半導體業")},
    )

    projection = projections["2330.TW"]

    assert projection.projected_vol_ratio == pytest.approx(5.0)
    assert projection.capped is True
