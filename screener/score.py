"""Rule-based scoring."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from .chip import ChipSnapshot
from .fetch import AnalystSnapshot
from .indicators import IndicatorSnapshot
from .sectors import SECTOR_MAX_SCORE, SectorSnapshot, sector_score

TARGET_UPSIDE_THRESHOLD = 0.10
TARGET_EVENT_DAYS = 7
TRUST_BUY_STREAK = 3
FOREIGN_PCT_OF_VOLUME = 0.05
FOREIGN_BUY_STREAK = 3
VOLUME_UP_RATIO = 1.2
VOLUME_DOWN_RATIO = 1.3
INTRADAY_SAME_TIME_VOLUME_RATIO = 1.2
SELL_PRESSURE_WEIGHTS = {
    "below_ma10": 0.08,
    "below_ma20": 0.12,
    "below_big_bull_low": 0.12,
    "below_prev2_low": 0.06,
    "below_prev5_low": 0.08,
    "volume_down": 0.10,
    "market_below_ma10": 0.06,
}

DEFAULT_RULE_WEIGHTS = {
    "above_all": 3.0,
    "new_high": 1.5,
    "trend": 1.5,
    "volume": 1.5,
    "obv": 1.0,
    "relative_strength": 2.0,
    "macd": 1.5,
    "target": 2.0,
    "sector": SECTOR_MAX_SCORE,
    "trust": 2.0,
    "foreign": 1.0,
}

TW_STRATEGY_RULE_WEIGHTS = {
    "bear_crash": {
        "above_all": 1.5,
        "new_high": 2.25,
        "trend": 1.5,
        "volume": 1.5,
        "obv": 0.5,
        "relative_strength": 1.0,
        "macd": 0.75,
        "target": 1.0,
        "sector": 0.75,
        "trust": 1.0,
        "foreign": 0.5,
    },
    "bear_downtrend": {
        "above_all": 1.5,
        "new_high": 2.25,
        "trend": 1.5,
        "volume": 1.5,
        "obv": 0.5,
        "relative_strength": 1.0,
        "macd": 0.75,
        "target": 1.0,
        "sector": 0.75,
        "trust": 1.0,
        "foreign": 0.5,
    },
    "range": {
        "above_all": 4.5,
        "new_high": 0.75,
        "trend": 2.25,
        "volume": 1.5,
        "obv": 0.5,
        "relative_strength": 1.0,
        "macd": 1.5,
        "target": 2.0,
        "sector": SECTOR_MAX_SCORE,
        "trust": 2.0,
        "foreign": 1.0,
    },
    "bull": {
        "above_all": 4.5,
        "new_high": 0.75,
        "trend": 0.75,
        "volume": 2.25,
        "obv": 0.5,
        "relative_strength": 3.0,
        "macd": 1.5,
        "target": 2.0,
        "sector": SECTOR_MAX_SCORE,
        "trust": 2.0,
        "foreign": 1.0,
    },
}

US_STRATEGY_RULE_WEIGHTS = {
    "bear_crash": {
        "above_all": 1.5,
        "new_high": 2.25,
        "trend": 0.75,
        "volume": 0.75,
        "obv": 0.5,
        "relative_strength": 1.0,
        "macd": 0.75,
        "target": 2.0,
        "sector": SECTOR_MAX_SCORE,
        "trust": 2.0,
        "foreign": 1.0,
    },
    "bear_downtrend": {
        "above_all": 1.5,
        "new_high": 2.25,
        "trend": 0.75,
        "volume": 0.75,
        "obv": 0.5,
        "relative_strength": 1.0,
        "macd": 0.75,
        "target": 2.0,
        "sector": SECTOR_MAX_SCORE,
        "trust": 2.0,
        "foreign": 1.0,
    },
    "range": {
        "above_all": 3.0,
        "new_high": 1.5,
        "trend": 0.75,
        "volume": 2.25,
        "obv": 0.5,
        "relative_strength": 1.0,
        "macd": 0.75,
        "target": 2.0,
        "sector": SECTOR_MAX_SCORE,
        "trust": 2.0,
        "foreign": 1.0,
    },
    "bull": {
        "above_all": 4.5,
        "new_high": 0.75,
        "trend": 1.5,
        "volume": 0.75,
        "obv": 1.0,
        "relative_strength": 3.0,
        "macd": 0.75,
        "target": 2.0,
        "sector": SECTOR_MAX_SCORE,
        "trust": 2.0,
        "foreign": 1.0,
    },
}


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


def strategy_rule_weights(
    strategy: str | None = None, market: str | None = None
) -> dict[str, float]:
    if not strategy:
        return DEFAULT_RULE_WEIGHTS
    if (market or "").lower() == "us":
        return US_STRATEGY_RULE_WEIGHTS.get(strategy, DEFAULT_RULE_WEIGHTS)
    return TW_STRATEGY_RULE_WEIGHTS.get(strategy, DEFAULT_RULE_WEIGHTS)


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


def _volume_up_signal(ind: IndicatorSnapshot) -> tuple[bool, str] | None:
    if ind.today_return is None:
        return None
    ratio = ind.vol_ratio
    detail_parts = []
    if ratio is not None:
        detail_parts.append(f"vol_ratio={ratio:.2f}")
    if (
        ind.volume_projection_reliable
        and ind.projected_vol_ratio is not None
        and ind.same_time_vol_ratio is not None
    ):
        detail_parts.append(f"projected_vol_ratio={ind.projected_vol_ratio:.2f}")
        detail_parts.append(f"same_time_vol_ratio={ind.same_time_vol_ratio:.2f}")
        if ind.volume_projection_source:
            detail_parts.append(f"projection={ind.volume_projection_source}")
        if ind.volume_projection_capped:
            detail_parts.append("capped")
        passed = (
            ind.projected_vol_ratio > VOLUME_UP_RATIO
            and ind.same_time_vol_ratio >= INTRADAY_SAME_TIME_VOLUME_RATIO
            and ind.today_return > 0
        )
    elif ratio is not None:
        passed = ratio > VOLUME_UP_RATIO and ind.today_return > 0
        if ind.projected_vol_ratio is not None:
            detail_parts.append(
                f"projected_vol_ratio={ind.projected_vol_ratio:.2f} unreliable"
            )
    else:
        return None
    detail_parts.append(f"return={ind.today_return * 100:.2f}%")
    return passed, " ".join(detail_parts)


def _append_sell_pressure_reasons(
    reasons: list[Reason],
    ind: IndicatorSnapshot,
    *,
    market_below_ma10: bool | None = None,
) -> None:
    checks = [
        (
            "賣壓扣分：跌破 10 日線",
            ind.ma10 is not None and ind.close < ind.ma10,
            (
                f"close={ind.close:.2f} MA10={ind.ma10:.2f}"
                if ind.ma10 is not None
                else "MA10 unavailable"
            ),
            SELL_PRESSURE_WEIGHTS["below_ma10"],
        ),
        (
            "賣壓扣分：跌破 20 日線",
            ind.ma20 is not None and ind.close < ind.ma20,
            (
                f"close={ind.close:.2f} MA20={ind.ma20:.2f}"
                if ind.ma20 is not None
                else "MA20 unavailable"
            ),
            SELL_PRESSURE_WEIGHTS["below_ma20"],
        ),
        (
            "賣壓扣分：跌破大量長紅 K 低點",
            ind.big_bull_low is not None and ind.close < ind.big_bull_low,
            (
                f"close={ind.close:.2f} big_bull_low={ind.big_bull_low:.2f}"
                if ind.big_bull_low is not None
                else "big bull low unavailable"
            ),
            SELL_PRESSURE_WEIGHTS["below_big_bull_low"],
        ),
        (
            "賣壓扣分：跌破前天低點",
            ind.prev2_low is not None and ind.close < ind.prev2_low,
            (
                f"close={ind.close:.2f} prev2_low={ind.prev2_low:.2f}"
                if ind.prev2_low is not None
                else "prev2 low unavailable"
            ),
            SELL_PRESSURE_WEIGHTS["below_prev2_low"],
        ),
        (
            "賣壓扣分：跌破近 5 日低點",
            ind.prev_5d_low is not None and ind.close < ind.prev_5d_low,
            (
                f"close={ind.close:.2f} prev_5d_low={ind.prev_5d_low:.2f}"
                if ind.prev_5d_low is not None
                else "prev 5d low unavailable"
            ),
            SELL_PRESSURE_WEIGHTS["below_prev5_low"],
        ),
        (
            f"賣壓扣分：放量下跌 (vol>{VOLUME_DOWN_RATIO:.1f}x)",
            (
                ind.vol_ratio is not None
                and ind.today_return is not None
                and ind.vol_ratio >= VOLUME_DOWN_RATIO
                and ind.today_return < 0
            ),
            (
                f"vol_ratio={ind.vol_ratio:.2f} return={ind.today_return * 100:.2f}%"
                if ind.vol_ratio is not None and ind.today_return is not None
                else "volume or return unavailable"
            ),
            SELL_PRESSURE_WEIGHTS["volume_down"],
        ),
        (
            "賣壓扣分：大盤跌破 10 日線",
            market_below_ma10 is True,
            "benchmark close < MA10"
            if market_below_ma10 is not None
            else "benchmark MA10 unavailable",
            SELL_PRESSURE_WEIGHTS["market_below_ma10"],
        ),
    ]
    for rule, passed, detail, penalty in checks:
        reasons.append(
            Reason(
                rule=rule,
                passed=passed,
                detail=detail,
                weight=0.0,
                score=-penalty if passed else 0.0,
            )
        )


def score(
    market: str,
    ind: IndicatorSnapshot,
    analyst: Optional[AnalystSnapshot],
    prev_target_mean: Optional[float],
    chip: Optional[ChipSnapshot] = None,
    benchmark_return_20d: Optional[float] = None,
    sector: Optional[SectorSnapshot] = None,
    strategy: str | None = None,
    market_below_ma10: bool | None = None,
) -> ScoreResult:
    reasons: list[Reason] = []
    is_us = market.lower() == "us"
    is_tw = market.lower() == "tw"
    weights = strategy_rule_weights(strategy if is_us or is_tw else None, market)

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
                weight=weights["above_all"],
            )
        )

    if ind.prev_high_20d is not None:
        passed = ind.close > ind.prev_high_20d
        reasons.append(
            Reason(
                rule="20日收盤新高",
                passed=passed,
                detail=f"close={ind.close:.2f} prev_20d_high={ind.prev_high_20d:.2f}",
                weight=weights["new_high"],
            )
        )

    if ind.ma5 is not None and ind.ma20 is not None:
        passed = ind.close > ind.ma5 and ind.ma5 > ind.ma20
        reasons.append(
            Reason(
                rule="短線趨勢確認 (close > MA5 且 MA5 > MA20)",
                passed=passed,
                detail=(f"close={ind.close:.2f} MA5={ind.ma5:.2f} MA20={ind.ma20:.2f}"),
                weight=weights["trend"],
            )
        )

    volume_up = _volume_up_signal(ind)
    if volume_up is not None:
        passed, volume_detail = volume_up
        reasons.append(
            Reason(
                rule=f"放量上漲 (vol>{VOLUME_UP_RATIO:.1f}x & up day)",
                passed=passed,
                detail=volume_detail,
                weight=weights["volume"],
            )
        )

    if ind.obv_ma5 is not None and ind.obv_ma20 is not None:
        passed = ind.obv_ma5 > ind.obv_ma20
        reasons.append(
            Reason(
                rule="OBV 5d > OBV 20d",
                passed=passed,
                detail=f"OBV_MA5={ind.obv_ma5:.0f} OBV_MA20={ind.obv_ma20:.0f}",
                weight=weights["obv"],
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
                weight=weights["relative_strength"],
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
        macd_base_weight = DEFAULT_RULE_WEIGHTS["macd"]
        macd_weight = weights["macd"]
        earned = earned / macd_base_weight * macd_weight
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
                weight=macd_weight,
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
                weight=weights["target"],
            )
        )

    if sector is not None:
        sector_weight = weights["sector"]
        earned_sector = sector_score(sector.strong_days) / SECTOR_MAX_SCORE * sector_weight
        passed_sector = earned_sector > 0
        detail = (
            f"{sector.group_name}: 連續強勢 {sector.strong_days} 日, "
            f"members={sector.member_count}, "
            f"1d={sector.return_1d * 100:+.2f}% vs "
            f"benchmark={sector.benchmark_return_1d * 100:+.2f}%, "
            f"5d={sector.return_5d * 100:+.2f}% vs "
            f"benchmark={sector.benchmark_return_5d * 100:+.2f}%, "
            f"MA5上方={sector.breadth_above_ma5 * 100:.0f}%"
        )
        reasons.append(
            Reason(
                rule="強勢板塊延續",
                passed=passed_sector,
                detail=detail,
                weight=sector_weight,
                score=earned_sector,
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
        trust_weight = weights["trust"]
        earned_trust = (
            trust_weight
            if trust_first_day
            else 1.5 / DEFAULT_RULE_WEIGHTS["trust"] * trust_weight
            if passed_trust_streak
            else 0.0
        )
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
                weight=trust_weight,
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
                weight=weights["foreign"],
            )
        )

    _append_sell_pressure_reasons(
        reasons, ind, market_below_ma10=market_below_ma10 if is_tw else None
    )

    max_score = sum(r.weight for r in reasons)
    total = 0.0
    for reason in reasons:
        if not reason.passed:
            continue
        earned = reason.score if reason.score is not None else reason.weight
        if earned < 0 and reason.weight == 0:
            total += earned * max_score
        else:
            total += earned
    total = max(0.0, total)
    return ScoreResult(score=total, max_score=max_score, reasons=reasons)
