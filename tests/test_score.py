from datetime import datetime, timedelta, timezone

from screener.chip import ChipSnapshot
from screener.fetch import AnalystSnapshot
from screener.indicators import IndicatorSnapshot
from screener.score import score
from screener.sectors import SectorSnapshot


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
        prev2_low=97.0,
        prev_3d_low=96.0,
        prev_5d_low=95.0,
        big_bull_low=90.0,
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
        "us", _ind(), analyst, benchmark_return_20d=0.05
    )
    assert result.max_score == 14.0
    assert result.score == 14.0


def test_tw_max_score_is_seventeen_with_chip():
    result = score(
        "tw",
        _ind(),
        analyst=None,
        chip=_chip(),
        benchmark_return_20d=0.05,
    )
    assert result.max_score == 17.0
    assert result.score == 14.5


def test_tw_range_strategy_uses_balanced_weights():
    result = score(
        "tw",
        _ind(),
        analyst=None,
        chip=_chip(),
        benchmark_return_20d=0.05,
        strategy="range",
    )

    above = next(r for r in result.reasons if r.rule.startswith("今日站上"))
    new_high = next(r for r in result.reasons if r.rule == "20日收盤新高")
    trend = next(r for r in result.reasons if r.rule.startswith("短線趨勢"))
    assert above.weight == 4.5
    assert new_high.weight == 0.75
    assert trend.weight == 2.25
    assert result.max_score == 17.0


def test_tw_bear_crash_strategy_reduces_technical_max_score():
    result = score(
        "tw",
        _ind(),
        analyst=None,
        chip=_chip(),
        benchmark_return_20d=0.05,
        strategy="bear_crash",
    )

    above = next(r for r in result.reasons if r.rule.startswith("今日站上"))
    macd = next(r for r in result.reasons if r.rule.startswith("MACD"))
    assert above.weight == 1.5
    assert macd.weight == 0.75
    assert macd.score == 0.75
    target = next(r for r in result.reasons if "目標價" in r.rule)
    trust = next(r for r in result.reasons if r.rule.startswith("投信"))
    foreign = next(r for r in result.reasons if r.rule.startswith("外資"))
    assert target.weight == 1.0
    assert trust.weight == 1.0
    assert trust.score == 0.75
    assert foreign.weight == 0.5
    assert result.max_score == 11.5


def test_us_bull_strategy_uses_backtested_weights():
    result = score(
        "us",
        _ind(),
        analyst=None,
        benchmark_return_20d=0.05,
        strategy="bull",
    )

    above = next(r for r in result.reasons if r.rule.startswith("今日站上"))
    new_high = next(r for r in result.reasons if r.rule == "20日收盤新高")
    volume = next(r for r in result.reasons if r.rule.startswith("放量"))
    relative_strength = next(r for r in result.reasons if r.rule.startswith("相對強度"))
    macd = next(r for r in result.reasons if r.rule.startswith("MACD"))
    assert above.weight == 4.5
    assert new_high.weight == 0.75
    assert volume.weight == 0.75
    assert relative_strength.weight == 3.0
    assert macd.weight == 0.75
    assert result.max_score == 14.25


def test_us_range_strategy_uses_rotation_weights():
    result = score(
        "us",
        _ind(),
        analyst=None,
        benchmark_return_20d=0.05,
        strategy="range",
    )

    trend = next(r for r in result.reasons if r.rule.startswith("短線趨勢"))
    volume = next(r for r in result.reasons if r.rule.startswith("放量"))
    obv = next(r for r in result.reasons if r.rule == "OBV 5d > OBV 20d")
    assert trend.weight == 0.75
    assert volume.weight == 2.25
    assert obv.weight == 0.5
    assert result.max_score == 11.75


def test_us_bear_crash_strategy_does_not_use_tw_chip_weight_changes():
    result = score(
        "us",
        _ind(),
        analyst=None,
        benchmark_return_20d=0.05,
        strategy="bear_crash",
    )

    target = next(r for r in result.reasons if "目標價" in r.rule)
    trend = next(r for r in result.reasons if r.rule.startswith("短線趨勢"))
    assert target.weight == 2.0
    assert trend.weight == 0.75
    assert result.max_score == 9.5


def test_tw_without_chip_still_emits_rules_but_zeroed():
    # chip data unavailable: rules emit, both fail -> max_score includes chip weights.
    result = score("tw", _ind(), analyst=None, chip=None)
    assert result.max_score == 15.0
    trust = next(r for r in result.reasons if r.rule.startswith("投信"))
    foreign = next(r for r in result.reasons if r.rule.startswith("外資"))
    assert trust.passed is False
    assert foreign.passed is False
    assert "unavailable" in trust.detail


def test_20d_close_high_requires_breaking_prior_20d_high():
    result = score("tw", _ind(close=98.0, prev_high_20d=99.0), None, chip=_chip())
    rule = next(r for r in result.reasons if r.rule == "20日收盤新高")
    assert rule.passed is False


def test_relative_strength_requires_stock_to_beat_benchmark():
    result = score(
        "tw",
        _ind(return_20d=0.04),
        None,
        chip=_chip(),
        benchmark_return_20d=0.05,
    )
    rule = next(r for r in result.reasons if r.rule.startswith("相對強度"))
    assert rule.passed is False


def test_short_trend_requires_close_above_ma5():
    result = score(
        "tw",
        _ind(close=90.0, ma5=99.0, ma10=80.0, ma20=79.0, prev2_low=80.0, prev_5d_low=80.0),
        None,
        chip=_chip(),
    )
    assert result.score == 6.5
    assert result.max_score == 15.0
    rule = next(r for r in result.reasons if r.rule.startswith("短線趨勢"))
    assert rule.passed is False


def test_short_trend_requires_ma5_above_ma20():
    result = score("tw", _ind(ma5=94.0, ma20=95.0), None, chip=_chip())
    assert result.score == 11.0
    assert result.max_score == 15.0
    rule = next(r for r in result.reasons if r.rule.startswith("短線趨勢"))
    assert rule.passed is False


def test_volume_up_requires_positive_return():
    result = score("tw", _ind(today_return=-0.01), None, chip=_chip())
    rule = next(r for r in result.reasons if r.rule.startswith("放量上漲"))
    assert rule.passed is False


def test_volume_up_uses_relaxed_volume_threshold():
    result = score("tw", _ind(vol_ratio=1.3), None, chip=_chip())
    rule = next(r for r in result.reasons if r.rule.startswith("放量上漲"))
    assert rule.passed is True
    assert rule.weight == 1.5


def test_intraday_projected_volume_can_pass_volume_up_rule():
    result = score(
        "tw",
        _ind(
            vol_ratio=0.5,
            projected_vol_ratio=1.6,
            same_time_vol_ratio=1.3,
            volume_projection_source="symbol",
            volume_projection_reliable=True,
        ),
        None,
        chip=_chip(),
    )

    rule = next(r for r in result.reasons if r.rule.startswith("放量上漲"))

    assert rule.passed is True
    assert "projected_vol_ratio=1.60" in rule.detail
    assert "same_time_vol_ratio=1.30" in rule.detail


def test_intraday_projected_volume_requires_same_time_confirmation():
    result = score(
        "tw",
        _ind(
            vol_ratio=0.5,
            projected_vol_ratio=1.6,
            same_time_vol_ratio=1.0,
            volume_projection_source="symbol",
            volume_projection_reliable=True,
        ),
        None,
        chip=_chip(),
    )

    rule = next(r for r in result.reasons if r.rule.startswith("放量上漲"))

    assert rule.passed is False


def test_intraday_unreliable_projection_does_not_drive_volume_up_rule():
    result = score(
        "tw",
        _ind(
            vol_ratio=0.5,
            projected_vol_ratio=1.6,
            same_time_vol_ratio=1.3,
            volume_projection_source="symbol",
            volume_projection_reliable=False,
        ),
        None,
        chip=_chip(),
    )

    rule = next(r for r in result.reasons if r.rule.startswith("放量上漲"))

    assert rule.passed is False
    assert "unreliable" in rule.detail


def test_obv_trend_down_loses_point():
    result = score(
        "tw", _ind(obv_ma5=30000.0, obv_ma20=40000.0), None, chip=_chip()
    )
    rule = next(r for r in result.reasons if r.rule == "OBV 5d > OBV 20d")
    assert rule.passed is False


def test_tw_sell_pressure_penalties_reduce_score_by_ratio():
    result = score(
        "tw",
        _ind(
            close=94.0,
            prev_close=96.0,
            today_return=-0.02,
            ma10=95.0,
            ma20=96.0,
            prev2_low=95.0,
            prev_5d_low=95.0,
            vol_ratio=1.4,
            big_bull_low=95.0,
        ),
        None,
        chip=_chip(),
        benchmark_return_20d=0.05,
        market_below_ma10=True,
    )

    penalties = [r for r in result.reasons if r.rule.startswith("賣壓扣分") and r.passed]
    assert {r.rule for r in penalties} >= {
        "賣壓扣分：跌破 10 日線",
        "賣壓扣分：跌破 20 日線",
        "賣壓扣分：跌破大量長紅 K 低點",
        "賣壓扣分：跌破前天低點",
        "賣壓扣分：跌破近 5 日低點",
        "賣壓扣分：放量下跌 (vol>1.3x)",
        "賣壓扣分：大盤跌破 10 日線",
    }
    penalty_ratio = sum(abs(r.score or 0) for r in penalties)
    raw_score = sum(
        (r.score if r.score is not None else r.weight)
        for r in result.reasons
        if r.passed and not r.rule.startswith("賣壓扣分")
    )
    assert round(penalty_ratio, 2) == 0.62
    assert result.score == max(0.0, raw_score - result.max_score * penalty_ratio)


def test_macd_above_signal_scores_one_point_without_cross():
    result = score(
        "tw",
        _ind(macd_prev=1.0, macd_signal_prev=0.5),
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
        chip=_chip(),
    )
    rule = next(r for r in result.reasons if r.rule.startswith("MACD"))
    assert rule.passed is False
    assert rule.score == 0.0
    assert rule.weight == 1.5


def test_target_raise_requires_ten_percent_upside():
    analyst = AnalystSnapshot(
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
    result = score("us", _ind(), analyst)
    rule = next(r for r in result.reasons if "目標價" in r.rule)
    assert rule.passed is False


def test_tw_target_raise_scores_like_us():
    analyst = AnalystSnapshot(
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

    result = score("tw", _ind(), analyst, chip=_chip())

    rule = next(r for r in result.reasons if "目標價" in r.rule)
    assert rule.passed is True
    assert rule.weight == 2.0
    assert result.score == 14.5


def test_target_raise_expires_after_seven_days():
    analyst = AnalystSnapshot(
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
    result = score("us", _ind(), analyst)
    rule = next(r for r in result.reasons if "目標價" in r.rule)
    assert rule.passed is False


def test_target_update_does_not_score_as_target_raise():
    analyst = AnalystSnapshot(
        rating=None,
        rating_score=None,
        target_price_events=[
            {
                "event_date": datetime.now(timezone.utc).date().isoformat(),
                "published_at": datetime.now(timezone.utc).isoformat(),
                "firm": "FactSet",
                "action": "update",
                "target_price": 120.0,
            }
        ],
    )

    result = score("tw", _ind(), analyst, chip=_chip())

    rule = next(r for r in result.reasons if "目標價" in r.rule)
    assert rule.passed is False


def test_no_target_price_events_does_not_score_target_raise():
    analyst = AnalystSnapshot(rating="Buy", rating_score=2.0)
    result = score("us", _ind(), analyst)
    rule = next(r for r in result.reasons if "目標價" in r.rule)
    assert rule.passed is False


def test_rating_is_not_scored():
    analyst = AnalystSnapshot(rating="Hold", rating_score=3.0)
    result = score("us", _ind(), analyst)
    assert not any(r.rule == "rating in {Buy, Strong Buy}" for r in result.reasons)


def test_trust_streak_below_threshold_fails():
    result = score("tw", _ind(), None, chip=_chip(trust_streak_days=2))
    rule = next(r for r in result.reasons if r.rule.startswith("投信"))
    assert rule.passed is False


def test_trust_first_buy_day_scores_two_points():
    result = score(
        "tw",
        _ind(),
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
    result = score("tw", _ind(), None, chip=chip)
    rule = next(r for r in result.reasons if r.rule.startswith("外資"))
    assert rule.passed is True


def test_foreign_passes_on_pct_alone_even_with_short_streak():
    chip = _chip(foreign_streak_days=1, foreign_pct_of_volume=0.08)
    result = score("tw", _ind(), None, chip=chip)
    rule = next(r for r in result.reasons if r.rule.startswith("外資"))
    assert rule.passed is True


def test_foreign_fails_when_both_conditions_fail():
    chip = _chip(foreign_streak_days=1, foreign_pct_of_volume=0.01)
    result = score("tw", _ind(), None, chip=chip)
    rule = next(r for r in result.reasons if r.rule.startswith("外資"))
    assert rule.passed is False


def test_strong_sector_continuation_scores_partial_weight():
    sector = SectorSnapshot(
        sector_official="半導體業",
        industry_group="半導體業",
        industry="半導體業",
        source="test",
        group_name="半導體業",
        member_count=5,
        strong_days=2,
        return_1d=0.03,
        return_5d=0.08,
        benchmark_return_1d=0.01,
        benchmark_return_5d=0.02,
        breadth_above_ma5=0.8,
        breadth_up_day=0.8,
    )

    result = score("tw", _ind(), None, chip=_chip(), sector=sector)

    rule = next(r for r in result.reasons if r.rule == "強勢板塊延續")
    assert rule.passed is True
    assert rule.weight == 1.5
    assert rule.score == 1.0


def test_bear_crash_strategy_reduces_sector_score_proportionally():
    sector = SectorSnapshot(
        sector_official="半導體業",
        industry_group="半導體業",
        industry="半導體業",
        source="test",
        group_name="半導體業",
        member_count=5,
        strong_days=2,
        return_1d=0.03,
        return_5d=0.08,
        benchmark_return_1d=0.01,
        benchmark_return_5d=0.02,
        breadth_above_ma5=0.8,
        breadth_up_day=0.8,
    )

    result = score(
        "tw",
        _ind(),
        None,
        chip=_chip(),
        sector=sector,
        strategy="bear_crash",
    )

    rule = next(r for r in result.reasons if r.rule == "強勢板塊延續")
    assert rule.passed is True
    assert rule.weight == 0.75
    assert rule.score == 0.5


def test_disposition_stock_excludes_volume_rules():
    """處置股應排除爆量上漲規則，max_score 也相應減少，賣壓放量下跌不計入扣分。"""
    ind_normal = _ind(vol_ratio=2.5, today_return=0.03)
    ind_down = _ind(vol_ratio=2.5, today_return=-0.03)

    result_normal = score("tw", ind_normal, None, chip=_chip(), is_disposition=False)
    result_disp = score("tw", ind_normal, None, chip=_chip(), is_disposition=True)
    result_disp_down = score("tw", ind_down, None, chip=_chip(), is_disposition=True)

    # disposition max_score 應比一般股少掉 volume 的 weight
    volume_weight = next(
        r.weight for r in result_normal.reasons if "放量上漲" in r.rule
    )
    assert result_disp.max_score == result_normal.max_score - volume_weight

    # 處置股的 reasons 裡沒有放量上漲，改為說明 reason
    rules_disp = [r.rule for r in result_disp.reasons]
    assert not any("放量上漲" in r for r in rules_disp)
    assert any("處置股" in r for r in rules_disp)

    # 放量下跌時，處置股不應被扣分（passed 仍為 False）
    vol_down_reason = next(
        r for r in result_disp_down.reasons if "放量下跌" in r.rule
    )
    assert vol_down_reason.passed is False
