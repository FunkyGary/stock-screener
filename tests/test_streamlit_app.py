from datetime import datetime, timedelta, timezone

from streamlit_app import (
    _downside_attention_reason,
    _has_recent_research_report,
    _is_downside_attention,
    _is_special_attention,
)


def test_recent_research_report_uses_target_price_event_dates():
    row = {
        "analyst": {
            "target_price_events": [
                {
                    "published_at": (
                        datetime.now(timezone.utc) - timedelta(days=2)
                    ).isoformat(),
                    "firm": "JPMorgan",
                    "target_price": 500.0,
                }
            ]
        }
    }

    assert _has_recent_research_report(row) is True


def test_recent_research_report_expires_after_seven_days():
    row = {
        "analyst": {
            "target_price_events": [
                {
                    "event_date": (datetime.now(timezone.utc) - timedelta(days=8))
                    .date()
                    .isoformat(),
                    "firm": "JPMorgan",
                    "target_price": 500.0,
                }
            ]
        }
    }

    assert _has_recent_research_report(row) is False


def test_downside_attention_bull_requires_ma5_break_and_low_score():
    row = {
        "score": 3.0,
        "max_score": 10.0,
        "score_regime": {"strategy": "bull"},
        "indicators": {
            "close": 95.0,
            "ma5": 100.0,
        }
    }

    assert _is_downside_attention(row) is False


def test_downside_attention_bull_passes_on_ma5_break_and_score_below_20pct():
    row = {
        "score": 1.9,
        "max_score": 10.0,
        "score_regime": {"strategy": "bull"},
        "indicators": {
            "close": 95.0,
            "ma5": 100.0,
        }
    }

    assert _is_downside_attention(row) is True
    assert _downside_attention_reason(row) == "多頭：跌破 MA5 且分數 < 20%"


def test_downside_attention_bear_uses_prev_5d_low_break():
    row = {
        "market": "tw",
        "score_regime": {"strategy": "bear_crash"},
        "indicators": {"close": 95.0, "prev_5d_low": 96.0},
    }

    assert _is_downside_attention(row) is True
    assert _downside_attention_reason(row) == "空頭：跌破近 5 日低點"


def test_downside_attention_bear_downtrend_uses_penalty_adjusted_score():
    row = {
        "market": "tw",
        "score": 1.9,
        "max_score": 10.0,
        "score_regime": {"strategy": "bear_downtrend"},
        "indicators": {"close": 95.0},
        "reasons": [
            {
                "rule": "賣壓扣分：跌破 20 日線",
                "passed": True,
                "detail": "",
                "weight": 0.0,
                "score": -0.12,
            }
        ],
    }

    assert _is_downside_attention(row) is True
    assert _downside_attention_reason(row) == "震盪走低：賣壓扣分後 < 20%"


def test_downside_attention_us_bear_uses_big_bull_low_with_volume():
    row = {
        "market": "us",
        "score_regime": {"strategy": "bear_crash"},
        "indicators": {
            "close": 95.0,
            "big_bull_low": 96.0,
            "vol_ratio": 1.3,
        },
    }

    assert _is_downside_attention(row) is True
    assert (
        _downside_attention_reason(row)
        == "美股空頭：跌破大量長紅 K 低點且放量 > 1.3x"
    )


def test_us_bear_special_attention_requires_spy_above_ma10_and_55pct():
    row = {
        "market": "us",
        "score": 5.5,
        "max_score": 10.0,
        "score_regime": {
            "strategy": "bear_downtrend",
            "close": 101.0,
            "ma10": 100.0,
        },
        "indicators": {
            "close": 101.0,
            "ma5": 100.0,
            "ma10": 99.0,
            "ma20": 98.0,
            "ma240": 97.0,
            "prev_close": 96.0,
            "prev_ma5": 100.0,
            "prev_ma10": 99.0,
            "prev_ma20": 98.0,
            "prev_ma240": 97.0,
        },
    }

    assert _is_special_attention(row) is True
    row["score"] = 5.4
    assert _is_special_attention(row) is False
    row["score"] = 5.5
    row["score_regime"]["close"] = 99.0
    assert _is_special_attention(row) is False


def test_tw_special_attention_bear_downtrend_only_requires_higher_score():
    row = {
        "market": "tw",
        "score": 6.9,
        "max_score": 10.0,
        "score_regime": {"strategy": "bear_downtrend"},
        "indicators": {
            "close": 101.0,
            "ma5": 100.0,
            "ma10": 99.0,
            "ma20": 98.0,
            "ma240": 97.0,
            "prev_close": 101.0,
            "prev_ma5": 100.0,
            "prev_ma10": 99.0,
            "prev_ma20": 98.0,
            "prev_ma240": 97.0,
        },
    }

    assert _is_special_attention(row) is False
    row["score"] = 7.0
    assert _is_special_attention(row) is True


def test_us_special_attention_still_requires_newly_above_all_mas():
    row = {
        "market": "us",
        "score": 10.0,
        "max_score": 10.0,
        "score_regime": {"strategy": "bull"},
        "indicators": {
            "close": 101.0,
            "ma5": 100.0,
            "ma10": 99.0,
            "ma20": 98.0,
            "ma240": 97.0,
            "prev_close": 101.0,
            "prev_ma5": 100.0,
            "prev_ma10": 99.0,
            "prev_ma20": 98.0,
            "prev_ma240": 97.0,
        },
    }

    assert _is_special_attention(row) is False


def test_downside_attention_range_uses_penalty_adjusted_score():
    row = {
        "score": 1.9,
        "max_score": 10.0,
        "score_regime": {"strategy": "range"},
        "indicators": {"close": 95.0},
        "reasons": [
            {
                "rule": "賣壓扣分：放量下跌 (vol>1.3x)",
                "passed": True,
                "detail": "",
                "weight": 0.0,
                "score": -0.10,
            }
        ],
    }

    assert _is_downside_attention(row) is True
    assert _downside_attention_reason(row) == "震盪：賣壓扣分後 < 20%"
