import pandas as pd

from screener.fetch import (
    _eps_surprise_from_earnings,
    _margins_from_income_stmt,
    _merge_intraday_latest,
    _parse_float,
    _parse_target_event,
    _tw_valuation_from_rows,
)


def test_parse_float_handles_blank_and_dash_cells():
    assert _parse_float("") is None
    assert _parse_float("--") is None
    assert _parse_float("1,234.5") == 1234.5
    assert _parse_float("0.78") == 0.78
    assert _parse_float(None) is None


def test_tw_valuation_from_rows_merges_twse_and_tpex():
    twse = [
        {"Code": "2330", "PEratio": "32.7", "PBratio": "10.6"},
        {"Code": "1101", "PEratio": "", "PBratio": "0.78"},  # empty PE → None
    ]
    tpex = [
        {
            "SecuritiesCompanyCode": "5483",
            "PriceEarningRatio": "12.5",
            "PriceBookRatio": "1.66",
        }
    ]
    out = _tw_valuation_from_rows(twse, tpex)
    assert out["2330"] == {"pe": 32.7, "pb": 10.6, "source": "twse"}
    assert out["1101"]["pe"] is None and out["1101"]["pb"] == 0.78
    assert out["5483"] == {"pe": 12.5, "pb": 1.66, "source": "tpex"}


def test_eps_surprise_picks_latest_reported_quarter():
    earnings = [
        {"period": "2026-03-31", "surprisePercent": 3.62},
        {"period": "2026-06-30", "surprisePercent": 4.34},
        {"period": "2025-12-31", "surprisePercent": -1.5},
    ]
    pct, period = _eps_surprise_from_earnings(earnings)
    assert period == "2026-06-30"
    assert pct == 4.34


def test_eps_surprise_handles_empty():
    assert _eps_surprise_from_earnings(None) == (None, None)
    assert _eps_surprise_from_earnings([]) == (None, None)


def test_margins_from_income_stmt_computes_newest_first_and_skips_nan():
    # yfinance shape: rows = line items, columns = quarter-end timestamps newest
    # first. Oldest column has NaN revenue and should be skipped.
    df = pd.DataFrame(
        {
            pd.Timestamp("2026-03-31"): [1000.0, 500.0, 300.0, 250.0],
            pd.Timestamp("2025-12-31"): [800.0, 360.0, 160.0, 140.0],
            pd.Timestamp("2024-12-31"): [float("nan")] * 4,
        },
        index=["Total Revenue", "Gross Profit", "Operating Income", "Net Income"],
    )
    out = _margins_from_income_stmt(df)
    assert [r["period"] for r in out] == ["2026-03-31", "2025-12-31"]
    assert out[0] == {"period": "2026-03-31", "gm": 50.0, "om": 30.0, "nm": 25.0}
    assert out[1]["gm"] == 45.0 and out[1]["nm"] == 17.5


def test_margins_from_income_stmt_handles_missing_rows_and_empty():
    assert _margins_from_income_stmt(None) == []
    assert _margins_from_income_stmt(pd.DataFrame()) == []
    # Net Income row missing → nm is None but others still computed.
    df = pd.DataFrame(
        {pd.Timestamp("2026-03-31"): [1000.0, 400.0, 120.0]},
        index=["Total Revenue", "Gross Profit", "Operating Income"],
    )
    out = _margins_from_income_stmt(df)
    assert out[0]["gm"] == 40.0 and out[0]["om"] == 12.0 and out[0]["nm"] is None


def test_parse_target_price_raise_news_event():
    event = _parse_target_event(
        {
            "datetime": 1779710400,
            "headline": "JPMorgan Raises Apple Price Target to $500 From $450",
            "source": "Dow Jones",
            "url": "https://example.com/aapl",
        }
    )

    assert event is not None
    assert event["firm"] == "JPMorgan"
    assert event["target_price"] == 500.0
    assert event["previous_target"] == 450.0
    assert event["raise_pct"] == 500.0 / 450.0 - 1.0


def test_parse_target_price_event_requires_raise_language():
    event = _parse_target_event(
        {
            "headline": "Apple Price Target Set at $500 by JPMorgan",
            "source": "Dow Jones",
        }
    )

    assert event is None


def test_merge_intraday_latest_overlays_current_session_close():
    daily = pd.DataFrame(
        {
            "Open": [90.0, 100.0],
            "High": [95.0, 105.0],
            "Low": [89.0, 99.0],
            "Close": [94.0, 101.0],
            "Volume": [1000.0, 2000.0],
        },
        index=pd.to_datetime(["2026-05-26", "2026-05-27"]),
    )
    intraday = pd.DataFrame(
        {
            "Open": [102.0, 104.0],
            "High": [106.0, 108.0],
            "Low": [101.0, 103.0],
            "Close": [104.0, 107.0],
            "Volume": [300.0, 400.0],
        },
        index=pd.to_datetime(
            ["2026-05-27 01:00:00+00:00", "2026-05-27 01:05:00+00:00"]
        ),
    )

    merged = _merge_intraday_latest(daily, intraday)

    assert merged.loc[pd.Timestamp("2026-05-27"), "Open"] == 102.0
    assert merged.loc[pd.Timestamp("2026-05-27"), "High"] == 108.0
    assert merged.loc[pd.Timestamp("2026-05-27"), "Low"] == 101.0
    assert merged.loc[pd.Timestamp("2026-05-27"), "Close"] == 107.0
    assert merged.loc[pd.Timestamp("2026-05-27"), "Volume"] == 700.0
