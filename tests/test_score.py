from datetime import datetime, timedelta, timezone

from screener.chip import ChipSnapshot
from screener.fetch import AnalystSnapshot
from screener.indicators import IndicatorSnapshot
from screener.score import score


def _ind(**overrides) -> IndicatorSnapshot:
    defaults = dict(
        close=100.0,
        prev_close=98.0,
        today_return=0.02,
        return_20d=0.12,
        ma5=99.0,
        ma10=97.0,
        ma20=95.0,
        ma240=80.0,
        prev_ma5=98.0,
        prev_ma10=96.0,
        prev_ma20=94.0,
        prev_ma240=79.0,
        volume=2000.0,
        vol_ratio=2.0,
        high_20d=105.0,
        pct_of_high_20d=0.99,
        obv=50000.0,
        obv_ma5=48000.0,
        obv_ma20=40000.0,
        macd=1.0,
        macd_signal=0.8,
        macd_hist=0.2,
        macd_prev=0.5,
        macd_signal_prev=0.7,
    )
    defaults.update(overrides)
    return IndicatorSnapshot(**defaults)


def _chip(**overrides) -> ChipSnapshot:
    defaults = dict(
        trust_streak_days=5,
        foreign_streak_days=5,
        foreign_net_today=1000,
        daily_volume_today=2000,
        foreign_pct_of_volume=0.5,
    )
    defaults.update(overrides)
    return ChipSnapshot(**defaults)


def test_full_bullish_us_with_target_raise_scores_max():
    analyst = AnalystSnapshot(
        target_mean=120.0,
        rating="Buy",
        rating_score=2.0,
        target_raise_valid_until=(
            datetime.now(timezone.utc) + timedelta(days=1)
        ).isoformat(),
        target_raise_from=110.0,
        target_raise_to=120.0,
        target_raise_pct=120.0 / 110.0 - 1.0,
    )
    result = score(
        "us", _ind(), analyst, prev_target_mean=110.0, benchmark_return_20d=0.05
    )
    assert result.max_score == 8
    assert result.score == 8


def test_tw_max_score_is_seven_with_chip():
    result = score(
        "tw",
        _ind(),
        analyst=None,
        prev_target_mean=None,
        chip=_chip(),
        benchmark_return_20d=0.05,
    )
    assert result.max_score == 8
    assert result.score == 8


def test_tw_without_chip_still_emits_rules_but_zeroed():
    # chip data unavailable: rules emit, both fail -> max_score still 7, score lower.
    result = score("tw", _ind(), analyst=None, prev_target_mean=None, chip=None)
    assert result.max_score == 7
    trust = next(r for r in result.reasons if r.rule.startswith("投信"))
    foreign = next(r for r in result.reasons if r.rule.startswith("外資"))
    assert trust.passed is False
    assert foreign.passed is False
    assert "unavailable" in trust.detail


def test_relative_strength_requires_stock_to_beat_benchmark():
    result = score(
        "tw",
        _ind(return_20d=0.04),
        None,
        None,
        chip=_chip(),
        benchmark_return_20d=0.05,
    )
    rule = next(r for r in result.reasons if r.rule.startswith("相對強度"))
    assert rule.passed is False


def test_short_trend_requires_close_above_ma5():
    result = score(
        "tw", _ind(close=90.0, ma5=99.0), None, None, chip=_chip()
    )
    assert result.score == 6
    assert result.max_score == 7
    rule = next(r for r in result.reasons if r.rule.startswith("短線趨勢"))
    assert rule.passed is False


def test_short_trend_requires_ma5_above_ma20():
    result = score(
        "tw", _ind(ma5=94.0, ma20=95.0), None, None, chip=_chip()
    )
    assert result.score == 6
    assert result.max_score == 7
    rule = next(r for r in result.reasons if r.rule.startswith("短線趨勢"))
    assert rule.passed is False


def test_volume_up_requires_positive_return():
    result = score("tw", _ind(today_return=-0.01), None, None, chip=_chip())
    rule = next(r for r in result.reasons if r.rule.startswith("放量上漲"))
    assert rule.passed is False


def test_obv_trend_down_loses_point():
    result = score(
        "tw", _ind(obv_ma5=30000.0, obv_ma20=40000.0), None, None, chip=_chip()
    )
    rule = next(r for r in result.reasons if r.rule == "OBV 5d > OBV 20d")
    assert rule.passed is False


def test_macd_golden_cross_requires_prev_below():
    result = score(
        "tw",
        _ind(macd_prev=1.0, macd_signal_prev=0.5),
        None,
        None,
        chip=_chip(),
    )
    rule = next(r for r in result.reasons if r.rule.startswith("MACD"))
    assert rule.passed is False


def test_target_raise_under_three_percent_does_not_pass():
    analyst = AnalystSnapshot(target_mean=112.0, rating="Buy", rating_score=2.0)
    result = score("us", _ind(), analyst, prev_target_mean=110.0)
    rule = next(r for r in result.reasons if r.rule.startswith("目標價"))
    assert rule.passed is False


def test_target_raise_expires_after_valid_until():
    analyst = AnalystSnapshot(
        target_mean=120.0,
        rating="Buy",
        rating_score=2.0,
        target_raise_valid_until=(
            datetime.now(timezone.utc) - timedelta(days=1)
        ).isoformat(),
        target_raise_from=110.0,
        target_raise_to=120.0,
        target_raise_pct=120.0 / 110.0 - 1.0,
    )
    result = score("us", _ind(), analyst, prev_target_mean=110.0)
    rule = next(r for r in result.reasons if r.rule.startswith("目標價"))
    assert rule.passed is False


def test_no_prior_target_does_not_score_target_raise():
    analyst = AnalystSnapshot(target_mean=120.0, rating="Buy", rating_score=2.0)
    result = score("us", _ind(), analyst, prev_target_mean=None)
    rule = next(r for r in result.reasons if r.rule.startswith("目標價"))
    assert rule.passed is False


def test_hold_rating_does_not_score():
    analyst = AnalystSnapshot(target_mean=120.0, rating="Hold", rating_score=3.0)
    result = score("us", _ind(), analyst, prev_target_mean=110.0)
    rule = next(r for r in result.reasons if r.rule == "rating in {Buy, Strong Buy}")
    assert rule.passed is False


def test_trust_streak_below_threshold_fails():
    result = score("tw", _ind(), None, None, chip=_chip(trust_streak_days=2))
    rule = next(r for r in result.reasons if r.rule.startswith("投信"))
    assert rule.passed is False


def test_foreign_passes_on_streak_alone_even_with_low_pct():
    # streak ok, pct below threshold -> still passes on OR
    chip = _chip(foreign_streak_days=4, foreign_pct_of_volume=0.01)
    result = score("tw", _ind(), None, None, chip=chip)
    rule = next(r for r in result.reasons if r.rule.startswith("外資"))
    assert rule.passed is True


def test_foreign_passes_on_pct_alone_even_with_short_streak():
    chip = _chip(foreign_streak_days=1, foreign_pct_of_volume=0.08)
    result = score("tw", _ind(), None, None, chip=chip)
    rule = next(r for r in result.reasons if r.rule.startswith("外資"))
    assert rule.passed is True


def test_foreign_fails_when_both_conditions_fail():
    chip = _chip(foreign_streak_days=1, foreign_pct_of_volume=0.01)
    result = score("tw", _ind(), None, None, chip=chip)
    rule = next(r for r in result.reasons if r.rule.startswith("外資"))
    assert rule.passed is False
