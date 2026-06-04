"""Cnyes TW FactSet valuation article target-price parsing."""

from __future__ import annotations

import html
import logging
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

logger = logging.getLogger(__name__)

CNYES_NEWS_CATEGORY_URLS = (
    "https://news.cnyes.com/news/cat/report",
    "https://news.cnyes.com/news/cat/tw_stock_valuation",
)
CNYES_TZ = ZoneInfo("Asia/Taipei")
USER_AGENT = "stock-screener/0.1 (+daily personal target-price refresh)"

_ARTICLE_LINK = re.compile(r"https?://news\.cnyes\.com/news/id/\d+/?|/news/id/\d+/?")
_SCRIPT_STYLE = re.compile(
    r"<(?:script|style)\b[^>]*>.*?</(?:script|style)>",
    flags=re.IGNORECASE | re.DOTALL,
)
_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"[ \t\r\f\v]+")
_BLANK_LINES = re.compile(r"\n{2,}")
_TITLE = re.compile(r"鉅亨速報\s*-\s*Factset\s*最新調查：([^\n]+)", re.IGNORECASE)
_SYMBOL = re.compile(r"(?P<name>[\w\u4e00-\u9fff・\-]+)\((?P<code>\d{4})-TW\)")
_PUBLISHED_AT = re.compile(r"鉅亨網新聞中心\s*(\d{4}-\d{2}-\d{2})\s*(\d{2}:\d{2})")
_TARGET_RAISE = re.compile(
    r"目標價調升至(?P<target>[0-9][0-9,]*(?:\.[0-9]+)?)元"
)
_TARGET_VALUATION_RAISE = re.compile(
    r"目標價估值[:：]\s*中位數由(?P<previous>[0-9][0-9,]*(?:\.[0-9]+)?)元"
    r"\s*上修至(?P<target>[0-9][0-9,]*(?:\.[0-9]+)?)元"
)
_TARGET_ESTIMATE = re.compile(
    r"預估目標價為(?P<target>[0-9][0-9,]*(?:\.[0-9]+)?)元"
)
_ANALYST_COUNT = re.compile(r"共(?P<count>\d+)位分析師")
_HIGH_LOW = re.compile(
    r"最高估值(?P<high>[0-9][0-9,]*(?:\.[0-9]+)?)元，"
    r"最低估值(?P<low>[0-9][0-9,]*(?:\.[0-9]+)?)元"
)


def _num(value: str) -> float:
    return float(value.replace(",", ""))


def _html_to_text(body: str) -> str:
    body = _SCRIPT_STYLE.sub("\n", body)
    body = re.sub(r"</(?:p|div|h1|h2|h3|li|br|tr)>\s*", "\n", body, flags=re.I)
    body = _TAG.sub(" ", body)
    body = html.unescape(body)
    body = _SPACE.sub(" ", body)
    body = _BLANK_LINES.sub("\n", body)
    return "\n".join(line.strip() for line in body.splitlines() if line.strip())


def extract_article_urls(category_html: str) -> list[str]:
    seen: set[str] = set()
    urls: list[str] = []
    for match in _ARTICLE_LINK.finditer(category_html):
        url = match.group(0)
        if url.startswith("/"):
            url = f"https://news.cnyes.com{url}"
        url = url.rstrip("/")
        if url in seen:
            continue
        seen.add(url)
        urls.append(url)
    return urls


def _parse_published_at(text: str) -> str | None:
    match = _PUBLISHED_AT.search(text)
    if match is None:
        return None
    local_dt = datetime.fromisoformat(f"{match.group(1)}T{match.group(2)}")
    return local_dt.replace(tzinfo=CNYES_TZ).isoformat()


def _first_float(pattern: re.Pattern[str], text: str, group: str) -> float | None:
    match = pattern.search(text)
    if match is None:
        return None
    return _num(match.group(group))


def parse_tw_valuation_article(article_html: str, url: str) -> dict | None:
    text = _html_to_text(article_html)
    title_match = _TITLE.search(text)
    title = title_match.group(0).strip() if title_match else ""
    if "FactSet" not in text and "Factset" not in text:
        return None
    if "目標價" not in text:
        return None

    symbol_match = _SYMBOL.search(title or text)
    if symbol_match is None:
        return None

    raise_match = _TARGET_RAISE.search(title or text)
    valuation_raise_match = _TARGET_VALUATION_RAISE.search(text)
    estimate_match = _TARGET_ESTIMATE.search(title or text)
    if raise_match is not None:
        target_price = _num(raise_match.group("target"))
        previous_target = (
            _num(valuation_raise_match.group("previous"))
            if valuation_raise_match is not None
            else None
        )
        action = "raise"
    elif estimate_match is not None:
        target_price = _num(estimate_match.group("target"))
        previous_target = None
        action = "update"
    else:
        return None

    published_at = _parse_published_at(text)
    event: dict = {
        "symbol": f"{symbol_match.group('code')}.TW",
        "market": "tw",
        "event_date": published_at[:10] if published_at else None,
        "published_at": published_at,
        "firm": "FactSet",
        "action": action,
        "previous_target": previous_target,
        "target_price": target_price,
        "raise_pct": (
            target_price / previous_target - 1.0
            if previous_target and previous_target > 0
            else None
        ),
        "source": "cnyes_factset",
        "headline": title or None,
        "url": url,
    }

    analyst_count = _first_float(_ANALYST_COUNT, text, "count")
    if analyst_count is not None:
        event["analyst_count"] = int(analyst_count)

    high_low = _HIGH_LOW.search(text)
    if high_low is not None:
        event["target_high"] = _num(high_low.group("high"))
        event["target_low"] = _num(high_low.group("low"))

    return event


def _get_text(session: requests.Session, url: str, timeout: float) -> str:
    resp = session.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
    resp.raise_for_status()
    return resp.text


def fetch_recent_tw_valuation_events(
    days: int = 2,
    session: requests.Session | None = None,
    now: datetime | None = None,
    timeout: float = 10.0,
) -> list[dict]:
    """Fetch recent Cnyes TW FactSet valuation articles and parse target events."""
    session = session or requests.Session()
    now = now or datetime.now(timezone.utc)
    cutoff = now.astimezone(CNYES_TZ) - timedelta(days=days)

    article_urls: list[str] = []
    seen_urls: set[str] = set()
    for category_url in CNYES_NEWS_CATEGORY_URLS:
        try:
            category_html = _get_text(session, category_url, timeout)
        except requests.RequestException as exc:
            logger.warning("Cnyes category fetch failed for %s: %s", category_url, exc)
            continue
        for url in extract_article_urls(category_html):
            if url in seen_urls:
                continue
            seen_urls.add(url)
            article_urls.append(url)

    events: list[dict] = []
    seen_event_keys: set[tuple[str | None, str | None, float | None, str | None]] = set()
    for url in article_urls:
        try:
            article_html = _get_text(session, url, timeout)
        except requests.RequestException as exc:
            logger.warning("Cnyes article fetch failed for %s: %s", url, exc)
            continue

        event = parse_tw_valuation_article(article_html, url)
        if event is None:
            continue
        published_at = event.get("published_at")
        if published_at:
            try:
                published_dt = datetime.fromisoformat(published_at)
            except ValueError:
                published_dt = None
            if published_dt is not None and published_dt < cutoff:
                continue
        key = (
            event.get("symbol"),
            event.get("published_at"),
            event.get("target_price"),
            event.get("url"),
        )
        if key in seen_event_keys:
            continue
        seen_event_keys.add(key)
        events.append(event)

    return sorted(events, key=lambda e: e.get("published_at") or "", reverse=True)
