from datetime import datetime, timedelta, timezone

from streamlit_app import (
    _downside_attention_reason,
    _filter_rows_by_search,
    _format_generated_at_for_display,
    _has_recent_research_report,
    _is_downside_attention,
    _is_newly_above_all_special_attention,
    _is_score_only_special_attention,
    _is_special_attention,
    _is_top_pick,
)


def test_generated_at_display_uses_dublin_timezone():
    assert _format_generated_at_for_display("2026-06-03T12:30:00+00:00") == (
        "2026-06-03 13:30"
    )


def test_generated_at_display_handles_missing_values():
    assert _format_generated_at_for_display(None) == "n/a"


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


def test_stock_search_matches_tw_symbol_and_name():
    rows = [
        {"symbol": "2330.TW", "name": "台積電", "tradingview_symbol": "TWSE:2330"},
        {"symbol": "2454.TW", "name": "聯發科", "tradingview_symbol": "TWSE:2454"},
    ]

    assert _filter_rows_by_search(rows, "2330") == [rows[0]]
    assert _filter_rows_by_search(rows, "台積") == [rows[0]]


def test_stock_search_matches_us_symbol_and_name_case_insensitive():
    rows = [
        {"symbol": "NVDA", "name": "NVIDIA", "tradingview_symbol": "NASDAQ:NVDA"},
        {"symbol": "MSFT", "name": "Microsoft", "tradingview_symbol": "NASDAQ:MSFT"},
    ]

    assert _filter_rows_by_search(rows, "nvda") == [rows[0]]
    assert _filter_rows_by_search(rows, "micro") == [rows[1]]


def test_stock_search_blank_query_returns_original_rows():
    rows = [
        {"symbol": "NVDA", "name": "NVIDIA"},
        {"symbol": "MSFT", "name": "Microsoft"},
    ]

    assert _filter_rows_by_search(rows, "  ") is rows


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
        "score_regime": {"strategy": "bear_downtrend"},
        "indicators": {
            "close": 95.0,
            "big_bull_low": 96.0,
            "vol_ratio": 1.3,
        },
    }

    assert _is_downside_attention(row) is True
    assert (
        _downside_attention_reason(row)
        == "美股震盪走低：跌破大量長紅 K 低點且放量 > 1.3x"
    )


def test_downside_attention_us_bear_crash_uses_big_bull_low_without_volume():
    row = {
        "market": "us",
        "score_regime": {"strategy": "bear_crash"},
        "indicators": {
            "close": 95.0,
            "big_bull_low": 96.0,
            "vol_ratio": 1.0,
        },
    }

    assert _is_downside_attention(row) is True
    assert _downside_attention_reason(row) == "美股急跌修復：跌破大量長紅 K 低點"


def test_us_bear_downtrend_special_attention_requires_spy_above_ma10_and_55pct():
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


def test_us_bear_crash_special_attention_requires_spy_ma5_repair_and_60pct():
    row = {
        "market": "us",
        "score": 5.9,
        "max_score": 10.0,
        "score_regime": {
            "strategy": "bear_crash",
            "close": 101.0,
            "ma5": 100.0,
            "prev_ma5": 99.0,
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

    assert _is_special_attention(row) is False
    row["score"] = 6.0
    assert _is_special_attention(row) is True
    row["score_regime"]["prev_ma5"] = 100.5
    assert _is_special_attention(row) is False


def test_us_bear_crash_top_pick_requires_spy_ma5_repair():
    row = {
        "market": "us",
        "score": 8.0,
        "max_score": 10.0,
        "score_regime": {
            "strategy": "bear_crash",
            "close": 99.0,
            "ma5": 100.0,
            "prev_ma5": 99.0,
            "ma10": 98.0,
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

    assert _is_top_pick(row) is False
    row["score_regime"]["close"] = 101.0
    assert _is_top_pick(row) is True


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


def test_us_bull_special_attention_only_requires_score_threshold():
    row = {
        "market": "us",
        "score": 4.9,
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
    row["score"] = 5.0
    assert _is_special_attention(row) is True


def test_us_range_special_attention_does_not_require_newly_above_all_mas():
    row = {
        "market": "us",
        "score": 5.0,
        "max_score": 10.0,
        "score_regime": {"strategy": "range"},
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

    assert _is_special_attention(row) is True
    row["score"] = 4.9
    assert _is_special_attention(row) is False


def test_special_attention_newly_above_all_bucket_requires_both_conditions():
    row = {
        "market": "tw",
        "score": 5.0,
        "max_score": 10.0,
        "score_regime": {"strategy": "bull"},
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

    assert _is_newly_above_all_special_attention(row) is True
    assert _is_score_only_special_attention(row) is False
    row["score"] = 4.9
    assert _is_newly_above_all_special_attention(row) is False


def test_special_attention_score_only_bucket_excludes_newly_above_all():
    row = {
        "market": "us",
        "score": 5.0,
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

    assert _is_score_only_special_attention(row) is True
    assert _is_newly_above_all_special_attention(row) is False
    row["score"] = 4.9
    assert _is_score_only_special_attention(row) is False


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


def test_valuation_badges_flag_low_pb_and_high_pe():
    from streamlit_app import _valuation_badges

    badges = _valuation_badges(
        {"pb": 1.05, "pe": 48.0, "eps_surprise_pct": 4.3, "eps_period": "2026-06-30"}
    )
    joined = " | ".join(badges)
    assert "PB 1.05x" in joined and "下檔保護" in joined
    assert "PE 48.0x" in joined and "雙殺風險" in joined
    assert "EPS surprise +4.3%" in joined and "超預期" in joined
    assert "2026-06-30" in joined


def test_valuation_badges_no_flags_when_neutral_and_negative_surprise():
    from streamlit_app import _valuation_badges

    badges = _valuation_badges({"pb": 3.0, "pe": 15.0, "eps_surprise_pct": -2.0})
    joined = " | ".join(badges)
    assert "下檔保護" not in joined and "雙殺風險" not in joined
    assert "不如預期" in joined


def test_valuation_badges_empty_for_no_data():
    from streamlit_app import _valuation_badges

    assert _valuation_badges(None) == []
    assert _valuation_badges({}) == []


def test_margin_trend_lines_flag_improving_margins():
    from streamlit_app import _margin_trend_lines

    # NVDA-like structural improvement, newest first.
    lines = _margin_trend_lines(
        {
            "margins": [
                {"period": "2026-04-30", "gm": 74.9, "om": 65.6, "nm": 71.5},
                {"period": "2025-04-30", "gm": 60.5, "om": 49.1, "nm": 42.6},
            ]
        }
    )
    joined = " | ".join(lines)
    assert "毛利率 60.5% → 74.9%" in joined and "↑改善" in joined
    assert "營益率 49.1% → 65.6%" in joined


def test_margin_trend_lines_flag_one_time_inflated_net():
    from streamlit_app import _margin_trend_lines

    # BA-like: operating loss but huge positive net → 業外/一次性灌水.
    lines = _margin_trend_lines(
        {
            "margins": [
                {"period": "2025-12-31", "gm": 7.6, "om": -3.4, "nm": 34.3},
                {"period": "2025-03-31", "gm": 12.4, "om": 2.4, "nm": -0.2},
            ]
        }
    )
    joined = " | ".join(lines)
    assert "稅後淨利率高於營益率" in joined and "sell-the-news" in joined


def test_margin_trend_lines_flag_one_time_depressed_net():
    from streamlit_app import _margin_trend_lines

    # INTC-like: operating profit but one-time charge pushes net deeply negative.
    lines = _margin_trend_lines(
        {
            "margins": [
                {"period": "2026-03-31", "gm": 39.4, "om": 6.9, "nm": -27.5},
                {"period": "2025-03-31", "gm": 36.9, "om": -1.1, "nm": -6.5},
            ]
        }
    )
    joined = " | ".join(lines)
    assert "稅後淨利率低於營益率" in joined and "錯殺" in joined


def test_margin_trend_lines_empty_with_insufficient_quarters():
    from streamlit_app import _margin_trend_lines

    assert _margin_trend_lines(None) == []
    assert _margin_trend_lines({}) == []
    assert _margin_trend_lines({"margins": []}) == []
    assert (
        _margin_trend_lines(
            {"margins": [{"period": "2026-03-31", "gm": 50.0, "om": 10.0, "nm": 8.0}]}
        )
        == []
    )
