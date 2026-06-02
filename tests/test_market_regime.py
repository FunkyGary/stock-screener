from screener import market_regime
import pandas as pd


def test_build_market_regime_only_returns_index_trends(monkeypatch):
    def fake_snapshot(index):
        return {
            "symbol": index.symbol,
            "name": index.name,
            "above_all_mas": index.symbol in {"^TWII", "^GSPC"},
        }

    monkeypatch.setattr(market_regime, "build_index_snapshot", fake_snapshot)
    monkeypatch.setattr(
        market_regime,
        "build_tw_strategy_snapshot",
        lambda: {"strategy": "range", "label": "區間震盪"},
    )
    monkeypatch.setattr(
        market_regime,
        "build_us_strategy_snapshot",
        lambda: {"strategy": "bull", "label": "多頭"},
    )

    result = market_regime.build_market_regime()

    tw = result["markets"]["tw"]
    us = result["markets"]["us"]
    assert [row["name"] for row in tw["indexes"]] == ["加權指數", "櫃買指數"]
    assert [row["above_all_mas"] for row in tw["indexes"]] == [True, False]
    assert [row["name"] for row in us["indexes"]] == ["S&P 500", "NASDAQ", "費半"]
    assert [row["above_all_mas"] for row in us["indexes"]] == [True, False, False]
    assert tw["strategy"]["strategy"] == "range"
    assert us["strategy"]["strategy"] == "bull"
    assert "exposure" not in tw
    assert "sentiment" not in tw


def test_build_market_regime_keeps_failed_index_as_x_source(monkeypatch):
    def fake_snapshot(index):
        if index.symbol == "^TWOII":
            raise RuntimeError("no data")
        return {"symbol": index.symbol, "name": index.name, "above_all_mas": True}

    monkeypatch.setattr(market_regime, "build_index_snapshot", fake_snapshot)
    monkeypatch.setattr(
        market_regime,
        "build_tw_strategy_snapshot",
        lambda: {"strategy": "range", "label": "區間震盪"},
    )
    monkeypatch.setattr(
        market_regime,
        "build_us_strategy_snapshot",
        lambda: {"strategy": "range", "label": "區間震盪"},
    )

    result = market_regime.build_market_regime()

    failed = result["markets"]["tw"]["indexes"][1]
    assert failed["name"] == "櫃買指數"
    assert failed["status"] == "fetch_failed"


def _ohlcv(closes: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Open": closes,
            "High": closes,
            "Low": closes,
            "Close": closes,
            "Volume": [1000] * len(closes),
        },
        index=pd.date_range("2024-01-01", periods=len(closes), freq="D"),
    )


def test_classify_tw_strategy_uses_deep_drawdown_for_bear_crash():
    closes = [100.0] * 180 + [88.0] * 60

    result = market_regime.classify_tw_strategy_from_ohlcv(_ohlcv(closes))

    assert result["strategy"] == "bear_crash"
    assert result["drawdown_120d"] <= -0.12


def test_classify_tw_strategy_uses_bear_downtrend_for_slow_decline():
    closes = [100.0] * 180 + [90.0] * 80

    result = market_regime.classify_tw_strategy_from_ohlcv(_ohlcv(closes))

    assert result["strategy"] == "bear_downtrend"
    assert result["close"] < result["ma240"]
    assert result["ma60"] < result["ma240"]


def test_classify_tw_strategy_requires_clean_trend_for_bull():
    closes = [100.0] * 180 + list(range(101, 161))

    result = market_regime.classify_tw_strategy_from_ohlcv(_ohlcv(closes))

    assert result["strategy"] == "bull"
    assert result["return_60d"] > 0.03


def test_classify_tw_strategy_falls_back_to_range():
    closes = [100.0] * 260

    result = market_regime.classify_tw_strategy_from_ohlcv(_ohlcv(closes))

    assert result["strategy"] == "range"


def test_classify_us_strategy_uses_spy_benchmark_metadata():
    closes = [100.0] * 180 + list(range(101, 161))

    result = market_regime.classify_us_strategy_from_ohlcv(_ohlcv(closes))

    assert result["strategy"] == "bull"
    assert result["market"] == "us"
    assert result["benchmark"] == "SPY"
    assert result["ma5"] is not None
    assert result["prev_ma5"] is not None
