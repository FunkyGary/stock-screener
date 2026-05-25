from screener import market_regime


def test_build_market_regime_only_returns_index_trends(monkeypatch):
    def fake_snapshot(index):
        return {
            "symbol": index.symbol,
            "name": index.name,
            "above_all_mas": index.symbol in {"^TWII", "^GSPC"},
        }

    monkeypatch.setattr(market_regime, "build_index_snapshot", fake_snapshot)

    result = market_regime.build_market_regime()

    tw = result["markets"]["tw"]
    us = result["markets"]["us"]
    assert [row["name"] for row in tw["indexes"]] == ["加權指數", "櫃買指數"]
    assert [row["above_all_mas"] for row in tw["indexes"]] == [True, False]
    assert [row["name"] for row in us["indexes"]] == ["S&P 500", "NASDAQ", "費半"]
    assert [row["above_all_mas"] for row in us["indexes"]] == [True, False, False]
    assert "exposure" not in tw
    assert "sentiment" not in tw


def test_build_market_regime_keeps_failed_index_as_x_source(monkeypatch):
    def fake_snapshot(index):
        if index.symbol == "^TWOII":
            raise RuntimeError("no data")
        return {"symbol": index.symbol, "name": index.name, "above_all_mas": True}

    monkeypatch.setattr(market_regime, "build_index_snapshot", fake_snapshot)

    result = market_regime.build_market_regime()

    failed = result["markets"]["tw"]["indexes"][1]
    assert failed["name"] == "櫃買指數"
    assert failed["status"] == "fetch_failed"
