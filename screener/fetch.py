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
import requests
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


@dataclass
class FundamentalSnapshot:
    """Display-only valuation/fundamental context. Not fed into scoring."""

    pe: Optional[float] = None
    pb: Optional[float] = None
    eps_surprise_pct: Optional[float] = None  # US only, latest reported quarter
    eps_period: Optional[str] = None  # quarter end (YYYY-MM-DD) of that surprise
    # US only, recent quarterly margins newest-first: [{period, gm, om, nm}].
    # Display-only profitability trend (Jeff 獲利性分析); never scored.
    margins: list[dict] | None = None
    source: Optional[str] = None


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


# ---------------------------------------------------------------------------
# Fundamentals (PE / PB / US EPS surprise) — display-only, not scored.
# ---------------------------------------------------------------------------

TWSE_BWIBBU_URL = "https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL"
TPEX_PERATIO_URL = (
    "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_peratio_analysis"
)
_HTTP_HEADERS = {"User-Agent": "stock-screener/1.0"}


def _parse_float(value) -> Optional[float]:
    """Parse a possibly-empty/dashed numeric cell to float, else None."""
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if text in {"", "-", "--", "N/A", "n/a"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _tw_valuation_from_rows(
    twse_rows: list[dict], tpex_rows: list[dict]
) -> dict[str, dict]:
    """Build {bare_code: {pe, pb, source}} from TWSE BWIBBU + TPEx peratio rows.

    Pure (no network) so it can be unit tested.
    """
    out: dict[str, dict] = {}
    for row in twse_rows or []:
        code = str(row.get("Code", "")).strip()
        if not code:
            continue
        out[code] = {
            "pe": _parse_float(row.get("PEratio")),
            "pb": _parse_float(row.get("PBratio")),
            "source": "twse",
        }
    for row in tpex_rows or []:
        code = str(row.get("SecuritiesCompanyCode", "")).strip()
        if not code:
            continue
        out[code] = {
            "pe": _parse_float(row.get("PriceEarningRatio")),
            "pb": _parse_float(row.get("PriceBookRatio")),
            "source": "tpex",
        }
    return out


def fetch_tw_valuation_map() -> dict[str, dict]:
    """Fetch one-shot PE/PB for all TWSE + TPEx stocks, keyed by bare code.

    Returns an empty dict if both endpoints fail; partial data is kept when only
    one endpoint succeeds.
    """
    twse_rows: list[dict] = []
    tpex_rows: list[dict] = []
    try:
        resp = requests.get(TWSE_BWIBBU_URL, timeout=20, headers=_HTTP_HEADERS)
        resp.raise_for_status()
        twse_rows = resp.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning("TWSE BWIBBU fetch failed: %s", exc)
    try:
        resp = requests.get(TPEX_PERATIO_URL, timeout=20, headers=_HTTP_HEADERS)
        resp.raise_for_status()
        tpex_rows = resp.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning("TPEx peratio fetch failed: %s", exc)
    return _tw_valuation_from_rows(twse_rows, tpex_rows)


def _eps_surprise_from_earnings(earnings: list[dict] | None) -> tuple[
    Optional[float], Optional[str]
]:
    """Return (surprise_pct, period) for the latest reported quarter.

    Finnhub `company_earnings` rows carry `period`, `surprisePercent`. Pure so it
    can be unit tested.
    """
    if not earnings:
        return None, None
    dated = [r for r in earnings if r.get("period")]
    if not dated:
        return None, None
    latest = max(dated, key=lambda r: str(r.get("period")))
    pct = latest.get("surprisePercent")
    try:
        pct = float(pct) if pct is not None else None
    except (TypeError, ValueError):
        pct = None
    return pct, str(latest.get("period"))


def _finite_float(value) -> Optional[float]:
    """`_parse_float` but rejecting NaN (pandas blank cells parse as NaN)."""
    val = _parse_float(value)
    if val is None or val != val:  # NaN is the only value not equal to itself
        return None
    return val


def _margins_from_income_stmt(
    income_stmt: "pd.DataFrame | None", max_quarters: int = 6
) -> list[dict]:
    """Quarterly gross/operating/net margins (%) newest-first.

    Built from a yfinance quarterly income statement. Quarters with missing or
    zero revenue are skipped (no meaningful margin). Pure so it can be unit
    tested. Display-only profitability trend; never scored.
    """
    if income_stmt is None or getattr(income_stmt, "empty", True):
        return []
    if "Total Revenue" not in income_stmt.index:
        return []
    rows: list[dict] = []
    for period in list(income_stmt.columns)[:max_quarters]:
        rev = _finite_float(income_stmt.loc["Total Revenue", period])
        if not rev:  # None or 0 → cannot form a margin
            continue

        def margin(label: str) -> Optional[float]:
            if label not in income_stmt.index:
                return None
            val = _finite_float(income_stmt.loc[label, period])
            return round(val / rev * 100, 1) if val is not None else None

        date_attr = getattr(period, "date", None)
        rows.append(
            {
                "period": str(date_attr() if callable(date_attr) else period),
                "gm": margin("Gross Profit"),
                "om": margin("Operating Income"),
                "nm": margin("Net Income"),
            }
        )
    return rows


def fetch_us_fundamental(symbol: str) -> FundamentalSnapshot:
    """US PE/PB via yfinance `.info`, EPS surprise via Finnhub, margin trend via
    the yfinance quarterly income statement.

    Each source is best-effort; partial data is returned rather than failing.
    """
    pe: Optional[float] = None
    pb: Optional[float] = None
    margins: list[dict] = []
    try:
        ticker = yf.Ticker(symbol)
        info = ticker.info
        pe = _parse_float(info.get("trailingPE"))
        pb = _parse_float(info.get("priceToBook"))
    except Exception as exc:  # yfinance raises a variety of errors
        logger.warning("yfinance valuation failed for %s: %s", symbol, exc)
        ticker = None

    if ticker is not None:
        try:
            margins = _margins_from_income_stmt(ticker.quarterly_income_stmt)
        except Exception as exc:
            logger.warning("yfinance income stmt failed for %s: %s", symbol, exc)

    eps_surprise_pct: Optional[float] = None
    eps_period: Optional[str] = None
    try:
        earnings = _finnhub_client().company_earnings(symbol)
        eps_surprise_pct, eps_period = _eps_surprise_from_earnings(earnings)
    except Exception as exc:
        logger.warning("finnhub earnings failed for %s: %s", symbol, exc)

    return FundamentalSnapshot(
        pe=pe,
        pb=pb,
        eps_surprise_pct=eps_surprise_pct,
        eps_period=eps_period,
        margins=margins or None,
        source="yfinance/finnhub",
    )
