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
    assert snap.ma10 is None
    assert snap.ma20 is None
    assert snap.ma240 is None
    assert snap.high_5d is None
    assert snap.high_20d is None
    assert snap.prev_high_20d is None
    assert snap.prev_3d_low is None
    assert snap.prev_5d_low is None
    assert snap.big_bull_low is None
    assert snap.macd is None
    assert snap.obv_ma20 is None


def test_ma10_and_ma240_compute_when_history_sufficient():
    # 250 ascending closes → MA240 should equal mean(closes[-240:]).
    closes = list(range(1, 251))
    snap = compute(_df(closes, [100] * 250))
    assert snap.ma10 == pytest.approx(sum(closes[-10:]) / 10)
    assert snap.ma240 is not None
    assert snap.ma240 == pytest.approx(sum(closes[-240:]) / 240)


def test_prev_mas_use_previous_bar():
    closes = list(range(1, 251))
    snap = compute(_df(closes, [100] * 250))
    assert snap.prev_ma5 == pytest.approx(sum(closes[-6:-1]) / 5)
    assert snap.prev_ma10 == pytest.approx(sum(closes[-11:-1]) / 10)
    assert snap.prev_ma20 == pytest.approx(sum(closes[-21:-1]) / 20)
    assert snap.prev_ma240 == pytest.approx(sum(closes[-241:-1]) / 240)


def test_ma240_none_when_under_240_bars():
    closes = list(range(1, 100))
    snap = compute(_df(closes, [100] * 99))
    assert snap.ma10 is not None  # 99 ≥ 10
    assert snap.ma240 is None  # 99 < 240
    assert snap.prev_ma240 is None


def test_pct_of_high_20d_when_close_below_high():
    closes = list(range(1, 21))
    snap = compute(_df(closes, [100] * 20))
    assert snap.high_5d == 20
    assert snap.high_20d == 20
    assert snap.pct_of_high_20d == pytest.approx(1.0)


def test_prev_high_20d_excludes_current_close():
    closes = list(range(1, 22))
    snap = compute(_df(closes, [100] * 21))
    assert snap.high_20d == 21
    assert snap.prev_high_20d == 20


def test_high_5d_uses_last_five_closes():
    snap = compute(_df([10, 12, 11, 13, 9, 14], [100] * 6))
    assert snap.high_5d == 14


def test_prev_lows_exclude_current_bar():
    snap = compute(_df([10, 12, 11, 13, 9, 14], [100] * 6))
    assert snap.prev2_low == 13
    assert snap.prev_3d_low == 9
    assert snap.prev_5d_low == 9


def test_big_bull_low_tracks_last_high_volume_long_red_bar():
    closes = [10.0] * 20 + [11.0, 10.7]
    volumes = [100.0] * 20 + [300.0, 120.0]
    df = _df(closes, volumes)
    df.loc[df.index[-2], "Open"] = 10.5
    df.loc[df.index[-2], "Low"] = 10.4

    snap = compute(df)

    assert snap.big_bull_low == 10.4


def test_today_return_uses_prev_close():
    df = _df([10, 10, 10, 10, 10, 11], [100] * 6)
    snap = compute(df)
    assert snap.prev_close == 10
    assert snap.today_return == pytest.approx(0.1)


def test_return_20d_uses_close_20_trading_days_ago():
    closes = list(range(100, 121))
    snap = compute(_df(closes, [100] * 21))
    assert snap.return_20d == pytest.approx(120 / 100 - 1.0)


def test_obv_accumulates_signed_volume():
    # Up, up, down → OBV should be +100 +100 -100 = 100
    df = _df([10, 11, 12, 11], [100, 100, 100, 100])
    snap = compute(df)
    assert snap.obv == pytest.approx(100.0)


def test_macd_available_with_enough_history():
    # 50 bars of a clean uptrend → MACD should be defined and positive.
    closes = [float(i) for i in range(1, 51)]
    df = _df(closes, [100] * 50)
    snap = compute(df)
    assert snap.macd is not None
    assert snap.macd_signal is not None
    assert snap.macd > 0
