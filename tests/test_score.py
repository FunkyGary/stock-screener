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
        high_5d=104.0,
        high_20d=105.0,
        prev_high_20d=99.0,
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
        trust_buy_first_day=False,
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
        target_price_events=[
            {
                "date": datetime.now(timezone.utc).date().isoformat(),
                "published_at": datetime.now(timezone.utc).isoformat(),
                "firm": "JPMorgan",
                "target_price": 120.0,
                "previous_target": 105.0,
                "raise_pct": 120.0 / 105.0 - 1.0,
            }
        ],
    )
    result = score(
        "us", _ind(), analyst, prev_target_mean=110.0, benchmark_return_20d=0.05
    )
    assert result.max_score == 13.5
    assert result.score == 13.5


def test_tw_max_score_is_seventeen_with_chip():
    result = score(
        "tw",
        _ind(),
        analyst=None,
        prev_target_mean=None,
        chip=_chip(),
        benchmark_return_20d=0.05,
    )
    assert result.max_score == 17.0
    assert result.score == 14.5


def test_tw_without_chip_still_emits_rules_but_zeroed():
    # chip data unavailable: rules emit, both fail -> max_score includes chip weights.
    result = score("tw", _ind(), analyst=None, prev_target_mean=None, chip=None)
    assert result.max_score == 15.0
    trust = next(r for r in result.reasons if r.rule.startswith("投信"))
    foreign = next(r for r in result.reasons if r.rule.startswith("外資"))
    assert trust.passed is False
    assert foreign.passed is False
    assert "unavailable" in trust.detail


def test_20d_close_high_requires_breaking_prior_20d_high():
    result = score("tw", _ind(close=98.0, prev_high_20d=99.0), None, None, chip=_chip())
    rule = next(r for r in result.reasons if r.rule == "20日收盤新高")
    assert rule.passed is False


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
    result = score("tw", _ind(close=90.0, ma5=99.0), None, None, chip=_chip())
    assert result.score == 7.0
    assert result.max_score == 15.0
    rule = next(r for r in result.reasons if r.rule.startswith("短線趨勢"))
    assert rule.passed is False


def test_short_trend_requires_ma5_above_ma20():
    result = score("tw", _ind(ma5=94.0, ma20=95.0), None, None, chip=_chip())
    assert result.score == 11.0
    assert result.max_score == 15.0
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


def test_macd_above_signal_scores_one_point_without_cross():
    result = score(
        "tw",
        _ind(macd_prev=1.0, macd_signal_prev=0.5),
        None,
        None,
        chip=_chip(),
    )
    rule = next(r for r in result.reasons if r.rule.startswith("MACD"))
    assert rule.passed is True
    assert rule.score == 1.0
    assert rule.weight == 1.5


def test_macd_below_signal_fails():
    result = score(
        "tw",
        _ind(macd=0.5, macd_signal=0.8, macd_prev=0.7, macd_signal_prev=0.6),
        None,
        None,
        chip=_chip(),
    )
    rule = next(r for r in result.reasons if r.rule.startswith("MACD"))
    assert rule.passed is False
    assert rule.score == 0.0
    assert rule.weight == 1.5


def test_target_raise_requires_ten_percent_upside():
    analyst = AnalystSnapshot(
        target_mean=120.0,
        rating="Buy",
        rating_score=2.0,
        target_price_events=[
            {
                "published_at": datetime.now(timezone.utc).isoformat(),
                "firm": "JPMorgan",
                "target_price": 109.0,
            }
        ],
    )
    result = score("us", _ind(), analyst, prev_target_mean=110.0)
    rule = next(r for r in result.reasons if "目標價" in r.rule)
    assert rule.passed is False


def test_tw_target_raise_scores_like_us():
    analyst = AnalystSnapshot(
        target_mean=None,
        rating=None,
        rating_score=None,
        target_price_events=[
            {
                "event_date": datetime.now(timezone.utc).date().isoformat(),
                "firm": "凱基投顧",
                "target_price": 120.0,
                "previous_target": 105.0,
            }
        ],
    )

    result = score("tw", _ind(), analyst, prev_target_mean=None, chip=_chip())

    rule = next(r for r in result.reasons if "目標價" in r.rule)
    assert rule.passed is True
    assert rule.weight == 2.0
    assert result.score == 14.5


def test_target_raise_expires_after_seven_days():
    analyst = AnalystSnapshot(
        target_mean=120.0,
        rating="Buy",
        rating_score=2.0,
        target_price_events=[
            {
                "published_at": (
                    datetime.now(timezone.utc) - timedelta(days=8)
                ).isoformat(),
                "firm": "JPMorgan",
                "target_price": 120.0,
            }
        ],
    )
    result = score("us", _ind(), analyst, prev_target_mean=110.0)
    rule = next(r for r in result.reasons if "目標價" in r.rule)
    assert rule.passed is False


def test_no_target_price_events_does_not_score_target_raise():
    analyst = AnalystSnapshot(target_mean=120.0, rating="Buy", rating_score=2.0)
    result = score("us", _ind(), analyst, prev_target_mean=None)
    rule = next(r for r in result.reasons if "目標價" in r.rule)
    assert rule.passed is False


def test_rating_is_not_scored():
    analyst = AnalystSnapshot(target_mean=120.0, rating="Hold", rating_score=3.0)
    result = score("us", _ind(), analyst, prev_target_mean=110.0)
    assert not any(r.rule == "rating in {Buy, Strong Buy}" for r in result.reasons)


def test_trust_streak_below_threshold_fails():
    result = score("tw", _ind(), None, None, chip=_chip(trust_streak_days=2))
    rule = next(r for r in result.reasons if r.rule.startswith("投信"))
    assert rule.passed is False


def test_trust_first_buy_day_scores_two_points():
    result = score(
        "tw",
        _ind(),
        None,
        None,
        chip=_chip(trust_streak_days=1, trust_buy_first_day=True),
    )
    rule = next(r for r in result.reasons if r.rule.startswith("投信"))
    assert rule.rule == "投信買進第一天"
    assert rule.passed is True
    assert rule.score == 2.0
    assert rule.weight == 2.0


def test_trust_three_day_streak_scores_one_point_five():
    result = score(
        "tw",
        _ind(),
        None,
        None,
        chip=_chip(trust_streak_days=3, trust_buy_first_day=False),
    )
    rule = next(r for r in result.reasons if r.rule.startswith("投信"))
    assert rule.rule == "投信連續買超 ≥ 3 日"
    assert rule.passed is True
    assert rule.score == 1.5
    assert rule.weight == 2.0


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
