"""Indicator calculations on a yfinance OHLCV DataFrame."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd


@dataclass
class IndicatorSnapshot:
    close: float
    ma5: Optional[float]
    ma20: Optional[float]
    volume: float
    vol_ratio: Optional[float]
    high_20d: Optional[float]
    pct_of_high_20d: Optional[float]


def _safe_last(series: pd.Series) -> Optional[float]:
    if series.empty:
        return None
    value = series.iloc[-1]
    if pd.isna(value):
        return None
    return float(value)


def compute(df: pd.DataFrame) -> IndicatorSnapshot:
    """Compute snapshot from daily OHLCV DataFrame sorted ascending by date."""
    close = df["Close"]
    vol = df["Volume"]
    last_close = float(close.iloc[-1])
    last_vol = float(vol.iloc[-1])

    ma5 = _safe_last(close.rolling(5).mean()) if len(close) >= 5 else None
    ma20 = _safe_last(close.rolling(20).mean()) if len(close) >= 20 else None

    ma20_vol = _safe_last(vol.rolling(20).mean()) if len(vol) >= 20 else None
    vol_ratio = last_vol / ma20_vol if ma20_vol and ma20_vol > 0 else None

    high_20d = _safe_last(close.rolling(20).max()) if len(close) >= 20 else None
    pct_of_high_20d = last_close / high_20d if high_20d and high_20d > 0 else None

    return IndicatorSnapshot(
        close=last_close,
        ma5=ma5,
        ma20=ma20,
        volume=last_vol,
        vol_ratio=vol_ratio,
        high_20d=high_20d,
        pct_of_high_20d=pct_of_high_20d,
    )
