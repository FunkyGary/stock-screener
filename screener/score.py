"""Rule-based scoring."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .fetch import AnalystSnapshot
from .indicators import IndicatorSnapshot


@dataclass
class Reason:
    rule: str
    passed: bool
    detail: str


@dataclass
class ScoreResult:
    score: int
    max_score: int
    reasons: list[Reason]


def score(
    market: str,
    ind: IndicatorSnapshot,
    analyst: Optional[AnalystSnapshot],
    prev_target_mean: Optional[float],
) -> ScoreResult:
    reasons: list[Reason] = []
    is_us = market.lower() == "us"

    if ind.ma5 is not None:
        passed = ind.close > ind.ma5
        reasons.append(
            Reason(
                rule="close > MA5",
                passed=passed,
                detail=f"close={ind.close:.2f} MA5={ind.ma5:.2f}",
            )
        )

    if ind.ma5 is not None and ind.ma20 is not None:
        passed = ind.ma5 > ind.ma20
        reasons.append(
            Reason(
                rule="MA5 > MA20",
                passed=passed,
                detail=f"MA5={ind.ma5:.2f} MA20={ind.ma20:.2f}",
            )
        )

    if ind.vol_ratio is not None:
        passed = ind.vol_ratio > 1.5
        reasons.append(
            Reason(
                rule="volume > 1.5x MA20",
                passed=passed,
                detail=f"vol_ratio={ind.vol_ratio:.2f}",
            )
        )

    if ind.pct_of_high_20d is not None:
        passed = ind.pct_of_high_20d >= 0.98
        reasons.append(
            Reason(
                rule="within 2% of 20d high",
                passed=passed,
                detail=f"close/high20d={ind.pct_of_high_20d:.3f}",
            )
        )

    if is_us:
        prev_str = f"{prev_target_mean:.2f}" if prev_target_mean is not None else "n/a"
        current_target = analyst.target_mean if analyst else None
        cur_str = f"{current_target:.2f}" if current_target is not None else "n/a"
        passed_target = (
            current_target is not None
            and prev_target_mean is not None
            and current_target > prev_target_mean
        )
        reasons.append(
            Reason(
                rule="analyst target raised",
                passed=passed_target,
                detail=f"target {prev_str} -> {cur_str}",
            )
        )

        rating = analyst.rating if analyst else None
        passed_rating = rating in ("Buy", "Strong Buy")
        reasons.append(
            Reason(
                rule="rating in {Buy, Strong Buy}",
                passed=passed_rating,
                detail=f"rating={rating or 'n/a'}",
            )
        )

    max_score = len(reasons)
    total = sum(1 for r in reasons if r.passed)
    return ScoreResult(score=total, max_score=max_score, reasons=reasons)
