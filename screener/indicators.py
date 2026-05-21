"""Indicator calculations on a yfinance OHLCV DataFrame."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd


@dataclass
class IndicatorSnapshot:
    close: float
    prev_close: Optional[float]
    today_return: Optional[float]
    ma5: Optional[float]
    ma10: Optional[float]
    ma20: Optional[float]
    ma240: Optional[float]  # 年線 (annual MA)
    volume: float
    vol_ratio: Optional[float]
    high_20d: Optional[float]
    pct_of_high_20d: Optional[float]
    obv: Optional[float]
    obv_ma5: Optional[float]
    obv_ma20: Optional[float]
    macd: Optional[float]
    macd_signal: Optional[float]
    macd_hist: Optional[float]
    macd_prev: Optional[float]
    macd_signal_prev: Optional[float]


def _safe_last(series: pd.Series) -> Optional[float]:
    if series.empty:
        return None
    value = series.iloc[-1]
    if pd.isna(value):
        return None
    return float(value)


def _safe_at(series: pd.Series, idx: int) -> Optional[float]:
    if len(series) <= abs(idx):
        return None
    value = series.iloc[idx]
    if pd.isna(value):
        return None
    return float(value)


def _obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    """On-Balance Volume: cumulative signed volume by daily price direction."""
    direction = np.sign(close.diff().fillna(0.0))
    return (direction * volume).cumsum()


def _macd(close: pd.Series) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Return (macd_line, signal_line, histogram). Standard 12/26/9."""
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


def compute(df: pd.DataFrame) -> IndicatorSnapshot:
    """Compute snapshot from daily OHLCV DataFrame sorted ascending by date."""
    close = df["Close"]
    vol = df["Volume"]
    last_close = float(close.iloc[-1])
    last_vol = float(vol.iloc[-1])
    prev_close = _safe_at(close, -2)
    today_return = (last_close / prev_close - 1.0) if prev_close else None

    ma5 = _safe_last(close.rolling(5).mean()) if len(close) >= 5 else None
    ma10 = _safe_last(close.rolling(10).mean()) if len(close) >= 10 else None
    ma20 = _safe_last(close.rolling(20).mean()) if len(close) >= 20 else None
    ma240 = _safe_last(close.rolling(240).mean()) if len(close) >= 240 else None

    ma20_vol = _safe_last(vol.rolling(20).mean()) if len(vol) >= 20 else None
    vol_ratio = last_vol / ma20_vol if ma20_vol and ma20_vol > 0 else None

    high_20d = _safe_last(close.rolling(20).max()) if len(close) >= 20 else None
    pct_of_high_20d = last_close / high_20d if high_20d and high_20d > 0 else None

    obv_series = _obv(close, vol)
    obv = _safe_last(obv_series)
    obv_ma5 = _safe_last(obv_series.rolling(5).mean()) if len(obv_series) >= 5 else None
    obv_ma20 = (
        _safe_last(obv_series.rolling(20).mean()) if len(obv_series) >= 20 else None
    )

    # MACD needs ~35 bars to be meaningful (EMA26 + 9-period signal).
    if len(close) >= 35:
        macd_line, signal_line, hist_series = _macd(close)
        macd = _safe_last(macd_line)
        macd_signal = _safe_last(signal_line)
        macd_hist = _safe_last(hist_series)
        macd_prev = _safe_at(macd_line, -2)
        macd_signal_prev = _safe_at(signal_line, -2)
    else:
        macd = macd_signal = macd_hist = macd_prev = macd_signal_prev = None

    return IndicatorSnapshot(
        close=last_close,
        prev_close=prev_close,
        today_return=today_return,
        ma5=ma5,
        ma10=ma10,
        ma20=ma20,
        ma240=ma240,
        volume=last_vol,
        vol_ratio=vol_ratio,
        high_20d=high_20d,
        pct_of_high_20d=pct_of_high_20d,
        obv=obv,
        obv_ma5=obv_ma5,
        obv_ma20=obv_ma20,
        macd=macd,
        macd_signal=macd_signal,
        macd_hist=macd_hist,
        macd_prev=macd_prev,
        macd_signal_prev=macd_signal_prev,
    )
