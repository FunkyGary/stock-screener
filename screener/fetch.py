"""Data fetchers for yfinance (price OHLCV) and Finnhub (analyst data)."""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import finnhub
import pandas as pd
import yfinance as yf
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)


@dataclass
class OHLCV:
    symbol: str
    df: pd.DataFrame
    intraday_df: pd.DataFrame | None = None


@dataclass
class AnalystSnapshot:
    rating: Optional[str]
    rating_score: Optional[float]
    target_price_events: list[dict] | None = None
    target_raise_detected_at: Optional[str] = None
    target_raise_valid_until: Optional[str] = None
    target_raise_from: Optional[float] = None
    target_raise_to: Optional[float] = None
    target_raise_pct: Optional[float] = None


class FetchError(RuntimeError):
    pass


def _normalize_yfinance_columns(df: pd.DataFrame) -> pd.DataFrame:
    if isinstance(df.columns, pd.MultiIndex):
        df = df.copy()
        df.columns = df.columns.get_level_values(0)
    return df


def _validate_ohlcv(df: pd.DataFrame, symbol: str) -> None:
    needed = {"Open", "High", "Low", "Close", "Volume"}
    if not needed.issubset(set(df.columns)):
        raise FetchError(f"missing OHLCV columns for {symbol}: {list(df.columns)}")


def _merge_intraday_latest(daily: pd.DataFrame, intraday: pd.DataFrame) -> pd.DataFrame:
    """Overlay the latest intraday bar onto the daily frame's current session."""
    if intraday.empty:
        return daily

    intraday = intraday.dropna(subset=["Close"])
    if intraday.empty:
        return daily

    session_date = pd.Timestamp(intraday.index[-1]).date()
    day_index = pd.Timestamp(session_date)
    day = intraday.loc[[idx.date() == session_date for idx in intraday.index]]
    if day.empty:
        return daily

    latest = day.iloc[-1]
    merged = daily.copy()
    merged.loc[day_index, "Open"] = float(day["Open"].dropna().iloc[0])
    merged.loc[day_index, "High"] = float(day["High"].max())
    merged.loc[day_index, "Low"] = float(day["Low"].min())
    merged.loc[day_index, "Close"] = float(latest["Close"])
    merged.loc[day_index, "Volume"] = float(day["Volume"].fillna(0).sum())
    if "Adj Close" in merged.columns:
        merged.loc[day_index, "Adj Close"] = float(
            latest.get("Adj Close", latest["Close"])
        )
    return merged.sort_index()


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    reraise=True,
)
def fetch_ohlcv(
    symbol: str,
    period: str = "2y",
    intraday: bool = False,
    intraday_interval: str = "1m",
    intraday_history_period: str = "60d",
    intraday_history_interval: str = "5m",
) -> OHLCV:
    df = yf.download(
        symbol,
        period=period,
        interval="1d",
        auto_adjust=False,
        progress=False,
        threads=False,
    )
    if df is None or df.empty:
        raise FetchError(f"no OHLCV data for {symbol}")
    df = _normalize_yfinance_columns(df)
    _validate_ohlcv(df, symbol)
    intraday_history_df = None
    if intraday:
        history_intervals = [intraday_history_interval]
        if intraday_interval not in history_intervals:
            history_intervals.append(intraday_interval)
        for interval in history_intervals:
            try:
                period_arg = (
                    intraday_history_period
                    if interval == intraday_history_interval
                    else "1d"
                )
                intraday_df = yf.download(
                    symbol,
                    period=period_arg,
                    interval=interval,
                    auto_adjust=False,
                    progress=False,
                    threads=False,
                )
                if intraday_df is None or intraday_df.empty:
                    continue
                intraday_df = _normalize_yfinance_columns(intraday_df)
                _validate_ohlcv(intraday_df, symbol)
                intraday_history_df = intraday_df
                df = _merge_intraday_latest(df, intraday_df)
                break
            except Exception as exc:
                logger.warning(
                    "intraday %s fetch failed for %s: %s", interval, symbol, exc
                )
    return OHLCV(symbol=symbol, df=df, intraday_df=intraday_history_df)


def _finnhub_client() -> finnhub.Client:
    key = os.environ.get("FINNHUB_API_KEY")
    if not key:
        raise FetchError("FINNHUB_API_KEY not set")
    return finnhub.Client(api_key=key)


def _rating_label(score: float) -> str:
    if score < 1.5:
        return "Strong Buy"
    if score < 2.5:
        return "Buy"
    if score < 3.5:
        return "Hold"
    if score < 4.5:
        return "Sell"
    return "Strong Sell"


def _fetch_rating_finnhub(symbol: str) -> tuple[Optional[str], Optional[float]]:
    """Finnhub free tier exposes recommendation_trends but not price_target."""
    client = _finnhub_client()
    recs = client.recommendation_trends(symbol) or []
    if not recs:
        return None, None
    latest = recs[0]
    buckets = {
        "strongBuy": (latest.get("strongBuy") or 0, 1.0),
        "buy": (latest.get("buy") or 0, 2.0),
        "hold": (latest.get("hold") or 0, 3.0),
        "sell": (latest.get("sell") or 0, 4.0),
        "strongSell": (latest.get("strongSell") or 0, 5.0),
    }
    total = sum(count for count, _ in buckets.values())
    if total == 0:
        return None, None
    rating_score = sum(count * weight for count, weight in buckets.values()) / total
    return _rating_label(rating_score), rating_score


_RAISE_WORDS = re.compile(
    r"\b(raise[sd]?|raised|boost[sed]?|lift[sed]?|increase[sd]?|hike[sd]?|"
    r"ups|up[s]?|raises)\b",
    flags=re.IGNORECASE,
)
_TARGET_WORDS = re.compile(r"\b(price target|pt|target price)\b", flags=re.IGNORECASE)
_TARGET_TO = re.compile(
    r"\b(?:price target|pt|target price)\b[^$]{0,60}?\b(?:to|at|of)\s*\$?([0-9][0-9,]*(?:\.[0-9]+)?)",
    flags=re.IGNORECASE,
)
_TARGET_FROM = re.compile(
    r"\bfrom\s*\$?([0-9][0-9,]*(?:\.[0-9]+)?)", flags=re.IGNORECASE
)
_FIRM_PREFIX = re.compile(
    r"^\s*(?:([A-Z][A-Za-z&.\- ]{1,40}?)\s+)?"
    r"(?:raise[sd]?|raised|boost[sed]?|lift[sed]?|increase[sd]?|hike[sd]?|ups)\b",
    flags=re.IGNORECASE,
)


def _parse_money(value: str) -> float:
    return float(value.replace(",", ""))


def _extract_firm(headline: str, source: str | None) -> str | None:
    match = _FIRM_PREFIX.search(headline)
    if match and match.group(1):
        firm = match.group(1).strip(" -:")
        if firm:
            return firm
    return source


def _parse_target_event(item: dict) -> dict | None:
    headline = item.get("headline") or ""
    if (
        not headline
        or not _TARGET_WORDS.search(headline)
        or not _RAISE_WORDS.search(headline)
    ):
        return None

    target_match = _TARGET_TO.search(headline)
    if not target_match:
        return None

    previous_match = _TARGET_FROM.search(headline)
    target_price = _parse_money(target_match.group(1))
    previous_target = (
        _parse_money(previous_match.group(1)) if previous_match is not None else None
    )

    published_at: str | None = None
    timestamp = item.get("datetime")
    if timestamp:
        try:
            published_at = datetime.fromtimestamp(
                int(timestamp), tz=timezone.utc
            ).isoformat()
        except (TypeError, ValueError, OSError):
            published_at = None

    return {
        "date": published_at[:10] if published_at else None,
        "published_at": published_at,
        "firm": _extract_firm(headline, item.get("source")),
        "target_price": target_price,
        "previous_target": previous_target,
        "raise_pct": (
            target_price / previous_target - 1.0
            if previous_target and previous_target > 0
            else None
        ),
        "headline": headline,
        "source": item.get("source"),
        "url": item.get("url"),
    }


def _fetch_target_price_events_finnhub(symbol: str, days: int = 7) -> list[dict]:
    client = _finnhub_client()
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=days)
    news = (
        client.company_news(symbol, _from=start.isoformat(), to=end.isoformat()) or []
    )
    events: list[dict] = []
    seen: set[tuple[str | None, float, str | None]] = set()
    for item in news:
        event = _parse_target_event(item)
        if event is None:
            continue
        key = (event.get("date"), event["target_price"], event.get("firm"))
        if key in seen:
            continue
        seen.add(key)
        events.append(event)
    events.sort(key=lambda e: e.get("published_at") or "", reverse=True)
    return events


def fetch_analyst(symbol: str) -> AnalystSnapshot:
    """Fetch Finnhub analyst rating and target-price raise news."""
    rating: Optional[str] = None
    rating_score: Optional[float] = None
    target_price_events: list[dict] = []

    try:
        rating, rating_score = _fetch_rating_finnhub(symbol)
    except Exception as exc:
        logger.warning("finnhub rating failed for %s: %s", symbol, exc)

    try:
        target_price_events = _fetch_target_price_events_finnhub(symbol)
    except Exception as exc:
        logger.warning("finnhub target price news failed for %s: %s", symbol, exc)

    return AnalystSnapshot(
        rating=rating,
        rating_score=rating_score,
        target_price_events=target_price_events,
    )
