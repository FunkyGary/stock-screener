"""Rule-based scoring."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from .chip import ChipSnapshot
from .fetch import AnalystSnapshot
from .indicators import IndicatorSnapshot

TARGET_RAISE_THRESHOLD = 1.03  # 3% jump in mean target
TRUST_BUY_STREAK = 3
FOREIGN_PCT_OF_VOLUME = 0.05
FOREIGN_BUY_STREAK = 3


@dataclass
class Reason:
    rule: str
    passed: bool
    detail: str
    weight: float


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


def _target_raise_is_active(analyst: AnalystSnapshot | None) -> bool:
    if analyst is None:
        return False
    valid_until = _parse_iso_datetime(analyst.target_raise_valid_until)
    return valid_until is not None and datetime.now(timezone.utc) <= valid_until


def _above_all_mas(ind: IndicatorSnapshot) -> bool:
    vals = (ind.ma5, ind.ma10, ind.ma20, ind.ma240)
    return all(v is not None and ind.close > v for v in vals)


def _was_above_all_mas_prev_day(ind: IndicatorSnapshot) -> bool:
    vals = (ind.prev_ma5, ind.prev_ma10, ind.prev_ma20, ind.prev_ma240)
    return (
        ind.prev_close is not None
        and all(v is not None and ind.prev_close > v for v in vals)
    )


def _has_prev_all_ma_data(ind: IndicatorSnapshot) -> bool:
    return ind.prev_close is not None and all(
        v is not None for v in (ind.prev_ma5, ind.prev_ma10, ind.prev_ma20, ind.prev_ma240)
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

    if ind.ma5 is not None and ind.ma20 is not None:
        passed = ind.close > ind.ma5 and ind.ma5 > ind.ma20
        reasons.append(
            Reason(
                rule="短線趨勢確認 (close > MA5 且 MA5 > MA20)",
                passed=passed,
                detail=(
                    f"close={ind.close:.2f} MA5={ind.ma5:.2f} "
                    f"MA20={ind.ma20:.2f}"
                ),
                weight=1.5,
            )
        )

    if ind.vol_ratio is not None and ind.today_return is not None:
        passed = ind.vol_ratio > 1.5 and ind.today_return > 0
        reasons.append(
            Reason(
                rule="放量上漲 (vol>1.5x & up day)",
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
        passed = (
            ind.macd > ind.macd_signal and ind.macd_prev <= ind.macd_signal_prev
        )
        reasons.append(
            Reason(
                rule="MACD 上穿 signal (golden cross)",
                passed=passed,
                detail=(
                    f"macd={ind.macd:.3f} signal={ind.macd_signal:.3f} "
                    f"(prev macd={ind.macd_prev:.3f} signal={ind.macd_signal_prev:.3f})"
                ),
                weight=1.0,
            )
        )

    if is_us:
        prev_str = f"{prev_target_mean:.2f}" if prev_target_mean is not None else "n/a"
        current_target = analyst.target_mean if analyst else None
        cur_str = f"{current_target:.2f}" if current_target is not None else "n/a"
        passed_target = _target_raise_is_active(analyst)
        pct = (
            analyst.target_raise_pct * 100
            if analyst and analyst.target_raise_pct is not None
            else (current_target / prev_target_mean - 1.0) * 100
            if current_target is not None and prev_target_mean
            else None
        )
        pct_str = f"{pct:+.2f}%" if pct is not None else "n/a"
        if analyst and analyst.target_raise_from is not None:
            prev_str = f"{analyst.target_raise_from:.2f}"
        if analyst and analyst.target_raise_to is not None:
            cur_str = f"{analyst.target_raise_to:.2f}"
        valid_until = analyst.target_raise_valid_until if analyst else None
        valid_str = valid_until[:10] if isinstance(valid_until, str) else "n/a"
        reasons.append(
            Reason(
                rule="目標價單日跳升 > 3%（3日內有效）",
                passed=passed_target,
                detail=f"target {prev_str} -> {cur_str} ({pct_str}), valid_until={valid_str}",
                weight=2.0,
            )
        )

    if is_tw:
        # 投信連續買超 ≥ 3 日
        trust_streak = chip.trust_streak_days if chip else 0
        passed_trust = chip is not None and trust_streak >= TRUST_BUY_STREAK
        reasons.append(
            Reason(
                rule=f"投信連續買超 ≥ {TRUST_BUY_STREAK} 日",
                passed=passed_trust,
                detail=(
                    f"streak={trust_streak} 日"
                    if chip is not None
                    else "chip data unavailable"
                ),
                weight=2.0,
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
            pct_str = (
                f"{foreign_pct * 100:+.2f}%" if foreign_pct is not None else "n/a"
            )
            detail = f"streak={foreign_streak} 日, 佔量={pct_str}"
        reasons.append(
            Reason(
                rule=f"外資大買 (>5% 量 或 連{FOREIGN_BUY_STREAK}日買超)",
                passed=passed_foreign,
                detail=detail,
                weight=1.5,
            )
        )

    max_score = sum(r.weight for r in reasons)
    total = sum(r.weight for r in reasons if r.passed)
    return ScoreResult(score=total, max_score=max_score, reasons=reasons)
