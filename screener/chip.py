"""Taiwan institutional investor (三大法人) buy/sell data from TWSE.

Source: https://www.twse.com.tw/rwd/zh/fund/T86?date=YYYYMMDD&selectType=ALL&response=json
Returns the full day's institutional trades for ALL listed stocks; we fetch
the last N trading days once per market run and slice per symbol.

Only 上市 (.TW) is supported here. 上櫃 (.TWO) would need a separate TPEx endpoint.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

import requests

logger = logging.getLogger(__name__)

TWSE_T86_URL = (
    "https://www.twse.com.tw/rwd/zh/fund/T86"
    "?date={date}&selectType=ALL&response=json"
)
TWSE_DISPOSITION_URL = "https://openapi.twse.com.tw/v1/announcement/punish"
TWSE_REQ_SPACING_SEC = 2.0  # well under TWSE's 3 req / 5s limit
LOOKBACK_CALENDAR_DAYS = 10  # cover ~5 trading days plus holidays


@dataclass
class ChipDay:
    """One trading day's institutional net buy for one stock (shares)."""

    date: str  # YYYYMMDD
    foreign_net: int  # 外陸資買賣超股數 (不含外資自營商)
    trust_net: int  # 投信買賣超股數
    total_volume: int  # 三大法人總買進 + 賣出 is not directly given;
    # we use foreign_buy + foreign_sell + trust_buy + trust_sell + dealer cols.
    # For "外資佔成交量 %" we instead need the stock's own daily volume,
    # which the OHLCV pipeline already provides — see compute_chip_snapshot.


@dataclass
class ChipSnapshot:
    """Per-symbol summary used by the scoring rules."""

    trust_streak_days: int  # consecutive days with 投信買賣超 > 0 (most recent)
    trust_buy_first_day: bool  # latest day is buy after two non-buy days
    foreign_streak_days: int  # consecutive days with 外資買賣超 > 0 (most recent)
    foreign_net_today: Optional[int]  # shares
    daily_volume_today: Optional[int]  # shares; from yfinance
    foreign_pct_of_volume: Optional[float]  # foreign_net_today / daily_volume_today


class ChipFetchError(RuntimeError):
    pass


def _strip_tw_suffix(symbol: str) -> Optional[str]:
    """'2330.TW' -> '2330'. Return None for non-TWSE symbols."""
    if symbol.endswith(".TW"):
        return symbol[: -len(".TW")]
    return None


def _parse_int(cell: str) -> int:
    cell = cell.strip().replace(",", "")
    if not cell or cell == "--":
        return 0
    return int(cell)


def _fetch_t86_day(yyyymmdd: str) -> Optional[dict]:
    """Fetch one day's T86 JSON. Return None if no data (weekend/holiday/early)."""
    url = TWSE_T86_URL.format(date=yyyymmdd)
    try:
        resp = requests.get(
            url,
            timeout=15,
            headers={"User-Agent": "stock-screener/1.0"},
        )
        resp.raise_for_status()
        payload = resp.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning("TWSE T86 fetch failed for %s: %s", yyyymmdd, exc)
        return None
    if payload.get("stat") != "OK":
        # On non-trading days TWSE returns stat="很抱歉，沒有符合條件的資料!"
        return None
    return payload


def _index_fields(fields: list[str]) -> dict[str, int]:
    """TWSE may reorder/rename columns; resolve indexes by substring."""
    idx = {}
    for i, name in enumerate(fields):
        if name.startswith("證券代號"):
            idx["symbol"] = i
        elif name.startswith("外陸資買賣超股數"):
            # "外陸資買賣超股數(不含外資自營商)" — distinct from "外資自營商買賣超股數"
            idx["foreign_net"] = i
        elif name == "投信買賣超股數":
            idx["trust_net"] = i
    missing = {"symbol", "foreign_net", "trust_net"} - idx.keys()
    if missing:
        raise ChipFetchError(f"TWSE T86 missing expected columns: {missing}")
    return idx


def fetch_disposition_codes() -> set[str]:
    """Return bare stock codes currently under TWSE disposition (處置), e.g. {'1409', '1568'}.

    On any fetch error returns an empty set so callers degrade gracefully.
    """
    try:
        resp = requests.get(
            TWSE_DISPOSITION_URL,
            timeout=15,
            headers={"User-Agent": "stock-screener/1.0"},
        )
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning("TWSE disposition fetch failed: %s", exc)
        return set()
    return {row["Code"].strip() for row in data if row.get("Code")}


def fetch_recent_chip_data(
    lookback_calendar_days: int = LOOKBACK_CALENDAR_DAYS,
    today: Optional[date] = None,
) -> dict[str, list[ChipDay]]:
    """Fetch the last N calendar days of T86. Returns {symbol_no_suffix: [days...]}
    sorted newest-first per symbol. Days with no data (weekends/holidays) are skipped.
    """
    today = today or date.today()
    by_symbol: dict[str, list[ChipDay]] = {}
    days_fetched = 0

    for offset in range(lookback_calendar_days):
        d = today - timedelta(days=offset)
        yyyymmdd = d.strftime("%Y%m%d")
        payload = _fetch_t86_day(yyyymmdd)
        if days_fetched > 0:
            # space out remaining requests; first is free
            time.sleep(TWSE_REQ_SPACING_SEC)
        if payload is None:
            continue
        days_fetched += 1

        try:
            cols = _index_fields(payload["fields"])
        except ChipFetchError as exc:
            logger.warning("%s — skipping %s", exc, yyyymmdd)
            continue

        for row in payload.get("data", []):
            sym = row[cols["symbol"]].strip()
            try:
                foreign = _parse_int(row[cols["foreign_net"]])
                trust = _parse_int(row[cols["trust_net"]])
            except (ValueError, IndexError):
                continue
            by_symbol.setdefault(sym, []).append(
                ChipDay(
                    date=yyyymmdd,
                    foreign_net=foreign,
                    trust_net=trust,
                    total_volume=0,
                )
            )

    # Each symbol's list is appended in calendar-descending order already.
    logger.info(
        "TWSE T86 loaded %d trading days, %d symbols", days_fetched, len(by_symbol)
    )
    return by_symbol


def _streak(days: list[ChipDay], pick: str) -> int:
    """Count consecutive most-recent days where the net field is > 0."""
    n = 0
    for d in days:
        val = getattr(d, pick)
        if val > 0:
            n += 1
        else:
            break
    return n


def _first_buy_day_after_two_non_buy(days: list[ChipDay], pick: str) -> bool:
    if len(days) < 3:
        return False
    return (
        getattr(days[0], pick) > 0
        and getattr(days[1], pick) <= 0
        and getattr(days[2], pick) <= 0
    )


def compute_chip_snapshot(
    symbol: str,
    chip_by_symbol: dict[str, list[ChipDay]],
    today_volume: Optional[float],
) -> Optional[ChipSnapshot]:
    """Build a ChipSnapshot for a watchlist symbol like '2330.TW'.
    Returns None for non-TWSE symbols or if no chip data was found.
    `today_volume` is the stock's daily volume (from yfinance) used to compute %.
    """
    bare = _strip_tw_suffix(symbol)
    if bare is None:
        return None
    days = chip_by_symbol.get(bare)
    if not days:
        return None

    trust_streak = _streak(days, "trust_net")
    trust_buy_first_day = _first_buy_day_after_two_non_buy(days, "trust_net")
    foreign_streak = _streak(days, "foreign_net")
    foreign_today = days[0].foreign_net
    vol = int(today_volume) if today_volume else None
    pct = (foreign_today / vol) if vol and vol > 0 else None

    return ChipSnapshot(
        trust_streak_days=trust_streak,
        trust_buy_first_day=trust_buy_first_day,
        foreign_streak_days=foreign_streak,
        foreign_net_today=foreign_today,
        daily_volume_today=vol,
        foreign_pct_of_volume=pct,
    )
