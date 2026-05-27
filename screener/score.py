"""Rule-based scoring."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from .chip import ChipSnapshot
from .fetch import AnalystSnapshot
from .indicators import IndicatorSnapshot

TARGET_UPSIDE_THRESHOLD = 0.10
TARGET_EVENT_DAYS = 7
TRUST_BUY_STREAK = 3
FOREIGN_PCT_OF_VOLUME = 0.05
FOREIGN_BUY_STREAK = 3
VOLUME_UP_RATIO = 1.2


@dataclass
class Reason:
    rule: str
    passed: bool
    detail: str
    weight: float
    score: Optional[float] = None


@dataclass
class ScoreResult:
    score: float
    max_score: float
    reasons: list[Reason]


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(
            timezone.utc
        )
    except ValueError:
        return None


def _target_event_is_recent(event: dict) -> bool:
    published_at = _parse_iso_datetime(
        event.get("published_at") or event.get("event_date") or event.get("date")
    )
    if published_at is None:
        return False
    age = datetime.now(timezone.utc) - published_at
    return age.total_seconds() >= 0 and age.days < TARGET_EVENT_DAYS


def _target_event_upside(event: dict, close: float) -> float | None:
    target_price = event.get("target_price")
    if target_price is None or close <= 0:
        return None
    return float(target_price) / close - 1.0


def _qualifying_target_events(
    analyst: AnalystSnapshot | None, close: float
) -> list[dict]:
    if analyst is None or not analyst.target_price_events:
        return []
    passed: list[dict] = []
    for event in analyst.target_price_events:
        upside = _target_event_upside(event, close)
        if (
            _target_event_is_recent(event)
            and upside is not None
            and upside >= TARGET_UPSIDE_THRESHOLD
        ):
            passed.append(event)
    return passed


def _format_target_event(event: dict, close: float) -> str:
    date = event.get("date") or event.get("event_date") or "n/a"
    firm = event.get("firm") or event.get("source") or "unknown"
    target = event.get("target_price")
    previous = event.get("previous_target")
    upside = _target_event_upside(event, close)
    target_str = f"{float(target):.2f}" if target is not None else "n/a"
    if previous:
        target_str = f"{float(previous):.2f}->{target_str}"
    upside_str = f"+{upside * 100:.2f}%" if upside is not None else "n/a"
    return f"{date} {firm} target {target_str}, upside={upside_str}"


def _above_all_mas(ind: IndicatorSnapshot) -> bool:
    vals = (ind.ma5, ind.ma10, ind.ma20, ind.ma240)
    return all(v is not None and ind.close > v for v in vals)


def _was_above_all_mas_prev_day(ind: IndicatorSnapshot) -> bool:
    vals = (ind.prev_ma5, ind.prev_ma10, ind.prev_ma20, ind.prev_ma240)
    return ind.prev_close is not None and all(
        v is not None and ind.prev_close > v for v in vals
    )


def _has_prev_all_ma_data(ind: IndicatorSnapshot) -> bool:
    return ind.prev_close is not None and all(
        v is not None
        for v in (ind.prev_ma5, ind.prev_ma10, ind.prev_ma20, ind.prev_ma240)
    )


def score(
    market: str,
    ind: IndicatorSnapshot,
    analyst: Optional[AnalystSnapshot],
    prev_target_mean: Optional[float],
    chip: Optional[ChipSnapshot] = None,
    benchmark_return_20d: Optional[float] = None,
) -> ScoreResult:
    reasons: list[Reason] = []
    is_us = market.lower() == "us"
    is_tw = market.lower() == "tw"

    if _has_prev_all_ma_data(ind) and all(
        v is not None for v in (ind.ma5, ind.ma10, ind.ma20, ind.ma240)
    ):
        passed = _above_all_mas(ind) and not _was_above_all_mas_prev_day(ind)
        reasons.append(
            Reason(
                rule="今日站上全均線",
                passed=passed,
                detail=(
                    f"close={ind.close:.2f} MA5={ind.ma5:.2f} "
                    f"MA10={ind.ma10:.2f} MA20={ind.ma20:.2f} MA240={ind.ma240:.2f}"
                ),
                weight=3.0,
            )
        )

    if ind.prev_high_20d is not None:
        passed = ind.close > ind.prev_high_20d
        reasons.append(
            Reason(
                rule="20日收盤新高",
                passed=passed,
                detail=f"close={ind.close:.2f} prev_20d_high={ind.prev_high_20d:.2f}",
                weight=1.5,
            )
        )

    if ind.ma5 is not None and ind.ma20 is not None:
        passed = ind.close > ind.ma5 and ind.ma5 > ind.ma20
        reasons.append(
            Reason(
                rule="短線趨勢確認 (close > MA5 且 MA5 > MA20)",
                passed=passed,
                detail=(f"close={ind.close:.2f} MA5={ind.ma5:.2f} MA20={ind.ma20:.2f}"),
                weight=1.5,
            )
        )

    if ind.vol_ratio is not None and ind.today_return is not None:
        passed = ind.vol_ratio > VOLUME_UP_RATIO and ind.today_return > 0
        reasons.append(
            Reason(
                rule=f"放量上漲 (vol>{VOLUME_UP_RATIO:.1f}x & up day)",
                passed=passed,
                detail=f"vol_ratio={ind.vol_ratio:.2f} return={ind.today_return * 100:.2f}%",
                weight=1.5,
            )
        )

    if ind.obv_ma5 is not None and ind.obv_ma20 is not None:
        passed = ind.obv_ma5 > ind.obv_ma20
        reasons.append(
            Reason(
                rule="OBV 5d > OBV 20d",
                passed=passed,
                detail=f"OBV_MA5={ind.obv_ma5:.0f} OBV_MA20={ind.obv_ma20:.0f}",
                weight=1.0,
            )
        )

    if ind.return_20d is not None and benchmark_return_20d is not None:
        passed = ind.return_20d > benchmark_return_20d
        reasons.append(
            Reason(
                rule="相對強度 20日 > 大盤",
                passed=passed,
                detail=(
                    f"stock_20d={ind.return_20d * 100:+.2f}% "
                    f"benchmark_20d={benchmark_return_20d * 100:+.2f}%"
                ),
                weight=2.0,
            )
        )

    if (
        ind.macd is not None
        and ind.macd_signal is not None
        and ind.macd_prev is not None
        and ind.macd_signal_prev is not None
    ):
        crossed_today = (
            ind.macd > ind.macd_signal and ind.macd_prev <= ind.macd_signal_prev
        )
        above_signal = ind.macd > ind.macd_signal
        earned = 1.5 if crossed_today else 1.0 if above_signal else 0.0
        if crossed_today:
            rule = "MACD 今日上穿 signal (golden cross)"
        elif above_signal:
            rule = "MACD 位於 signal 上方"
        else:
            rule = "MACD 位於 signal 上方或今日上穿"
        reasons.append(
            Reason(
                rule=rule,
                passed=above_signal,
                detail=(
                    f"macd={ind.macd:.3f} signal={ind.macd_signal:.3f} "
                    f"(prev macd={ind.macd_prev:.3f} signal={ind.macd_signal_prev:.3f})"
                ),
                weight=1.5,
                score=earned,
            )
        )

    if is_us or is_tw:
        target_events = _qualifying_target_events(analyst, ind.close)
        passed_target = bool(target_events)
        detail = (
            "； ".join(
                _format_target_event(event, ind.close) for event in target_events[:3]
            )
            if target_events
            else f"no 7d target raise with target >= close * {1 + TARGET_UPSIDE_THRESHOLD:.2f}"
        )
        reasons.append(
            Reason(
                rule="法人目標價上調且高於現價10%（7日內）",
                passed=passed_target,
                detail=detail,
                weight=2.0,
            )
        )

    if is_tw:
        # 投信買進第一天與連續買超 ≥ 3 日互斥。
        trust_streak = chip.trust_streak_days if chip else 0
        trust_first_day = bool(chip and chip.trust_buy_first_day)
        passed_trust_streak = (
            chip is not None and not trust_first_day and trust_streak >= TRUST_BUY_STREAK
        )
        passed_trust = trust_first_day or passed_trust_streak
        earned_trust = 2.0 if trust_first_day else 1.5 if passed_trust_streak else 0.0
        trust_rule = (
            "投信買進第一天"
            if trust_first_day
            else f"投信連續買超 ≥ {TRUST_BUY_STREAK} 日"
        )
        reasons.append(
            Reason(
                rule=trust_rule,
                passed=passed_trust,
                detail=(
                    f"streak={trust_streak} 日, first_day={trust_first_day}"
                    if chip is not None
                    else "chip data unavailable"
                ),
                weight=2.0,
                score=earned_trust,
            )
        )

        # 外資單日買超 > 成交量 5%  或  外資連續買超 ≥ 3 日
        foreign_streak = chip.foreign_streak_days if chip else 0
        foreign_pct = chip.foreign_pct_of_volume if chip else None
        passed_big_pct = foreign_pct is not None and foreign_pct > FOREIGN_PCT_OF_VOLUME
        passed_streak = foreign_streak >= FOREIGN_BUY_STREAK
        passed_foreign = chip is not None and (passed_big_pct or passed_streak)
        if chip is None:
            detail = "chip data unavailable"
        else:
            pct_str = f"{foreign_pct * 100:+.2f}%" if foreign_pct is not None else "n/a"
            detail = f"streak={foreign_streak} 日, 佔量={pct_str}"
        reasons.append(
            Reason(
                rule=f"外資大買 (>5% 量 或 連{FOREIGN_BUY_STREAK}日買超)",
                passed=passed_foreign,
                detail=detail,
                weight=1.0,
            )
        )

    max_score = sum(r.weight for r in reasons)
    total = sum(
        (r.score if r.score is not None else r.weight) for r in reasons if r.passed
    )
    return ScoreResult(score=total, max_score=max_score, reasons=reasons)
