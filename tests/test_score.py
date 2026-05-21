from screener.fetch import AnalystSnapshot
from screener.indicators import IndicatorSnapshot
from screener.score import score


def _ind(**overrides) -> IndicatorSnapshot:
    defaults = dict(
        close=100.0,
        ma5=99.0,
        ma20=95.0,
        volume=2000.0,
        vol_ratio=2.0,
        high_20d=105.0,
        pct_of_high_20d=0.99,
    )
    defaults.update(overrides)
    return IndicatorSnapshot(**defaults)


def test_full_bullish_us_with_target_raise_scores_max():
    analyst = AnalystSnapshot(target_mean=120.0, rating="Buy", rating_score=2.0)
    result = score("us", _ind(), analyst, prev_target_mean=110.0)
    assert result.max_score == 6
    assert result.score == 6


def test_tw_max_score_is_four_no_analyst_rules():
    result = score("tw", _ind(), analyst=None, prev_target_mean=None)
    assert result.max_score == 4
    assert result.score == 4


def test_below_ma5_loses_only_that_point():
    result = score("tw", _ind(close=90.0, ma5=99.0), None, None)
    assert result.score == 3
    assert result.max_score == 4


def test_no_prior_target_does_not_score_target_raise():
    analyst = AnalystSnapshot(target_mean=120.0, rating="Buy", rating_score=2.0)
    result = score("us", _ind(), analyst, prev_target_mean=None)
    rule = next(r for r in result.reasons if r.rule == "analyst target raised")
    assert rule.passed is False


def test_hold_rating_does_not_score():
    analyst = AnalystSnapshot(target_mean=120.0, rating="Hold", rating_score=3.0)
    result = score("us", _ind(), analyst, prev_target_mean=110.0)
    rule = next(r for r in result.reasons if r.rule == "rating in {Buy, Strong Buy}")
    assert rule.passed is False
