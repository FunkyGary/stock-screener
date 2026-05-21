from screener.chip import ChipSnapshot
from screener.fetch import AnalystSnapshot
from screener.indicators import IndicatorSnapshot
from screener.score import score


def _ind(**overrides) -> IndicatorSnapshot:
    defaults = dict(
        close=100.0,
        prev_close=98.0,
        today_return=0.02,
        ma5=99.0,
        ma20=95.0,
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
    analyst = AnalystSnapshot(target_mean=120.0, rating="Buy", rating_score=2.0)
    result = score("us", _ind(), analyst, prev_target_mean=110.0)
    assert result.max_score == 8
    assert result.score == 8


def test_tw_max_score_is_eight_with_chip():
    result = score("tw", _ind(), analyst=None, prev_target_mean=None, chip=_chip())
    assert result.max_score == 8
    assert result.score == 8


def test_tw_without_chip_still_emits_rules_but_zeroed():
    # chip data unavailable: rules emit, both fail -> max_score still 8, score lower.
    result = score("tw", _ind(), analyst=None, prev_target_mean=None, chip=None)
    assert result.max_score == 8
    trust = next(r for r in result.reasons if r.rule.startswith("投信"))
    foreign = next(r for r in result.reasons if r.rule.startswith("外資"))
    assert trust.passed is False
    assert foreign.passed is False
    assert "unavailable" in trust.detail


def test_below_ma5_loses_only_that_point():
    result = score(
        "tw", _ind(close=90.0, ma5=99.0), None, None, chip=_chip()
    )
    assert result.score == 7
    assert result.max_score == 8


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
