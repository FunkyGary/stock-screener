import pandas as pd
import pytest

from screener.indicators import compute


def _df(closes: list[float], volumes: list[float]) -> pd.DataFrame:
    dates = pd.date_range("2024-01-01", periods=len(closes))
    return pd.DataFrame(
        {
            "Open": closes,
            "High": closes,
            "Low": closes,
            "Close": closes,
            "Volume": volumes,
        },
        index=dates,
    )


def test_ma5_uses_last_five_closes():
    df = _df([10, 11, 12, 13, 14, 15], [100] * 6)
    snap = compute(df)
    assert snap.close == 15
    assert snap.ma5 == pytest.approx((11 + 12 + 13 + 14 + 15) / 5)


def test_vol_ratio_doubles_when_today_doubles_average():
    closes = list(range(1, 25))
    volumes = [100] * 23 + [200]
    df = _df(closes, volumes)
    snap = compute(df)
    assert snap.vol_ratio is not None
    assert snap.vol_ratio > 1.5


def test_returns_none_when_not_enough_history():
    df = _df([1, 2, 3], [10] * 3)
    snap = compute(df)
    assert snap.ma5 is None
    assert snap.ma20 is None
    assert snap.high_20d is None


def test_pct_of_high_20d_when_close_below_high():
    closes = list(range(1, 21))
    snap = compute(_df(closes, [100] * 20))
    assert snap.high_20d == 20
    assert snap.pct_of_high_20d == pytest.approx(1.0)
