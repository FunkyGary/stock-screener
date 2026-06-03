"""Intraday volume projection from historical cumulative-volume curves."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from statistics import median
from zoneinfo import ZoneInfo

import pandas as pd

from .sectors import SectorMapEntry

MIN_CURVE_DAYS = 5
MIN_FALLBACK_SAMPLES = 10
OPEN_GUARD_MINUTES = 30
PROJECTED_VOL_RATIO_CAP = 5.0

MARKET_SESSIONS = {
    "tw": ("Asia/Taipei", time(9, 0), time(13, 30)),
    "us": ("America/New_York", time(9, 30), time(16, 0)),
}


@dataclass(frozen=True)
class IntradayVolumeProjection:
    current_volume: float
    projected_volume: float | None
    projected_vol_ratio: float | None
    same_time_vol_ratio: float | None
    historical_cum_share: float | None
    source: str | None
    reliable: bool
    capped: bool = False


def _market_session(market: str) -> tuple[ZoneInfo, time, time] | None:
    config = MARKET_SESSIONS.get(market.lower())
    if config is None:
        return None
    timezone_name, open_time, close_time = config
    return ZoneInfo(timezone_name), open_time, close_time


def _with_local_session_index(df: pd.DataFrame, tz: ZoneInfo) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    out = df.copy()
    index = pd.to_datetime(out.index)
    if index.tz is None:
        index = index.tz_localize(tz)
    else:
        index = index.tz_convert(tz)
    out.index = index
    out = out.sort_index()
    out = out[out["Volume"].fillna(0) > 0]
    return out


def _session_minute(ts: pd.Timestamp, open_time: time) -> int:
    return (ts.hour * 60 + ts.minute) - (open_time.hour * 60 + open_time.minute)


def _session_frame(
    intraday_df: pd.DataFrame, tz: ZoneInfo, open_time: time, close_time: time
) -> pd.DataFrame:
    frame = _with_local_session_index(intraday_df, tz)
    if frame.empty:
        return frame
    return frame.between_time(open_time, close_time, inclusive="left")


def _current_day(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    session_date = frame.index[-1].date()
    return frame[[idx.date() == session_date for idx in frame.index]]


def _historical_share_samples(
    frame: pd.DataFrame,
    current_session_date,
    current_minute: int,
    open_time: time,
) -> list[float]:
    samples: list[float] = []
    for session_date, day in frame.groupby(frame.index.date):
        if session_date == current_session_date:
            continue
        total = float(day["Volume"].fillna(0).sum())
        if total <= 0:
            continue
        day_minutes = pd.Series(
            [_session_minute(pd.Timestamp(idx), open_time) for idx in day.index],
            index=day.index,
        )
        cum = float(day.loc[day_minutes <= current_minute, "Volume"].fillna(0).sum())
        if cum > 0:
            samples.append(min(cum / total, 1.0))
    return samples


def _same_time_volume_samples(
    frame: pd.DataFrame,
    current_session_date,
    current_minute: int,
    open_time: time,
) -> list[float]:
    samples: list[float] = []
    for session_date, day in frame.groupby(frame.index.date):
        if session_date == current_session_date:
            continue
        day_minutes = pd.Series(
            [_session_minute(pd.Timestamp(idx), open_time) for idx in day.index],
            index=day.index,
        )
        cum = float(day.loc[day_minutes <= current_minute, "Volume"].fillna(0).sum())
        if cum > 0:
            samples.append(cum)
    return samples


def _previous_daily_volume_avg(daily_df: pd.DataFrame, days: int = 20) -> float | None:
    if daily_df is None or daily_df.empty or "Volume" not in daily_df:
        return None
    volumes = daily_df["Volume"].astype(float)
    if len(volumes) > days:
        volumes = volumes.iloc[-days - 1 : -1]
    else:
        volumes = volumes.iloc[:-1]
    volumes = volumes[volumes > 0]
    if volumes.empty:
        return None
    return float(volumes.mean())


def _fallback_samples(
    symbol: str,
    market: str,
    current_minute: int,
    current_session_date,
    frames_by_symbol: dict[str, pd.DataFrame],
    sector_map: dict[str, SectorMapEntry],
    open_time: time,
    industry_only: bool,
) -> list[float]:
    entry = sector_map.get(symbol)
    samples: list[float] = []
    for other_symbol, frame in frames_by_symbol.items():
        if frame.empty:
            continue
        other_entry = sector_map.get(other_symbol)
        if industry_only:
            if entry is None or other_entry is None:
                continue
            if other_entry.industry_group != entry.industry_group:
                continue
        elif other_entry is not None and other_entry.market != market:
            continue
        samples.extend(
            _historical_share_samples(
                frame, current_session_date, current_minute, open_time
            )
        )
    return samples


def project_intraday_volumes(
    market: str,
    daily_by_symbol: dict[str, pd.DataFrame],
    intraday_by_symbol: dict[str, pd.DataFrame | None],
    sector_map: dict[str, SectorMapEntry],
) -> dict[str, IntradayVolumeProjection]:
    """Project full-day volume for symbols with intraday bars.

    The primary curve is symbol-specific. If it has too few historical sessions,
    fall back to same-industry watchlist curves, then same-market watchlist curves.
    """
    session = _market_session(market)
    if session is None:
        return {}
    tz, open_time, close_time = session
    frames_by_symbol = {
        symbol: _session_frame(df, tz, open_time, close_time)
        for symbol, df in intraday_by_symbol.items()
        if df is not None and not df.empty
    }
    out: dict[str, IntradayVolumeProjection] = {}
    for symbol, frame in frames_by_symbol.items():
        current_day = _current_day(frame)
        if current_day.empty:
            continue
        current_volume = float(current_day["Volume"].fillna(0).sum())
        if current_volume <= 0:
            continue
        latest_ts = pd.Timestamp(current_day.index[-1])
        current_minute = _session_minute(latest_ts, open_time)
        current_session_date = latest_ts.date()

        samples = _historical_share_samples(
            frame, current_session_date, current_minute, open_time
        )
        source = "symbol" if len(samples) >= MIN_CURVE_DAYS else None
        if source is None:
            samples = _fallback_samples(
                symbol,
                market,
                current_minute,
                current_session_date,
                frames_by_symbol,
                sector_map,
                open_time,
                industry_only=True,
            )
            source = "industry" if len(samples) >= MIN_FALLBACK_SAMPLES else None
        if source is None:
            samples = _fallback_samples(
                symbol,
                market,
                current_minute,
                current_session_date,
                frames_by_symbol,
                sector_map,
                open_time,
                industry_only=False,
            )
            source = "market" if len(samples) >= MIN_FALLBACK_SAMPLES else None

        same_time_samples = _same_time_volume_samples(
            frame, current_session_date, current_minute, open_time
        )
        same_time_baseline = (
            median(same_time_samples)
            if len(same_time_samples) >= MIN_CURVE_DAYS
            else None
        )
        same_time_ratio = (
            current_volume / same_time_baseline
            if same_time_baseline and same_time_baseline > 0
            else None
        )
        historical_share = median(samples) if source is not None and samples else None
        projected_volume = (
            current_volume / historical_share
            if historical_share and historical_share > 0
            else None
        )
        avg_volume = _previous_daily_volume_avg(daily_by_symbol.get(symbol))
        projected_vol_ratio = (
            projected_volume / avg_volume
            if projected_volume is not None and avg_volume and avg_volume > 0
            else None
        )
        capped = False
        if (
            projected_vol_ratio is not None
            and projected_vol_ratio > PROJECTED_VOL_RATIO_CAP
        ):
            projected_vol_ratio = PROJECTED_VOL_RATIO_CAP
            projected_volume = (
                avg_volume * PROJECTED_VOL_RATIO_CAP
                if avg_volume and avg_volume > 0
                else projected_volume
            )
            capped = True
        reliable = bool(
            projected_volume is not None
            and source is not None
            and current_minute >= OPEN_GUARD_MINUTES
        )
        out[symbol] = IntradayVolumeProjection(
            current_volume=current_volume,
            projected_volume=projected_volume,
            projected_vol_ratio=projected_vol_ratio,
            same_time_vol_ratio=same_time_ratio,
            historical_cum_share=historical_share,
            source=source,
            reliable=reliable,
            capped=capped,
        )
    return out
