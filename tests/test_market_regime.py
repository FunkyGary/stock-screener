from screener import market_regime


def _index(above: bool = False, below: bool = False) -> dict:
    return {"status": "ok", "above_all_mas": above, "below_all_mas": below}


def test_tw_recommends_full_exposure_when_indexes_above_and_retail_short():
    result = market_regime.recommend_exposure(
        "tw",
        [_index(above=True), _index(above=True)],
        {"retail_mtx_bias": "short"},
    )

    assert result["exposure_pct"] == 100
    assert result["trend_state"] == "all_above"
    assert result["sentiment_signal"] == "retail_short"


def test_tw_recommends_80_when_indexes_below_and_retail_long():
    result = market_regime.recommend_exposure(
        "tw",
        [_index(below=True), _index(below=True)],
        {"retail_mtx_bias": "long"},
    )

    assert result["exposure_pct"] == 80
    assert result["trend_state"] == "all_below"
    assert result["sentiment_signal"] == "retail_long"


def test_us_recommends_full_exposure_on_extreme_fear_with_indexes_above():
    result = market_regime.recommend_exposure(
        "us",
        [_index(above=True), _index(above=True), _index(above=True)],
        {"fear_greed": "extreme_fear"},
    )

    assert result["exposure_pct"] == 100
    assert result["trend_state"] == "all_above"
    assert result["sentiment_signal"] == "extreme_fear"


def test_us_recommends_80_on_extreme_greed_with_indexes_below():
    result = market_regime.recommend_exposure(
        "us",
        [_index(below=True), _index(below=True), _index(below=True)],
        {"fear_greed": "extreme_greed"},
    )

    assert result["exposure_pct"] == 80
    assert result["trend_state"] == "all_below"
    assert result["sentiment_signal"] == "extreme_greed"


def test_exposure_waits_when_rule_is_not_defined():
    result = market_regime.recommend_exposure(
        "tw",
        [_index(above=True), _index(above=True)],
        {"retail_mtx_bias": "neutral"},
    )

    assert result["exposure_pct"] is None
    assert result["reason"] == "未命中已定義的部位規則"


def test_exposure_waits_when_any_index_fetch_fails():
    result = market_regime.recommend_exposure(
        "us",
        [_index(above=True), {"status": "fetch_failed"}],
        {"fear_greed": "extreme_fear"},
    )

    assert result["exposure_pct"] is None
    assert result["reason"] == "部分指數資料抓取失敗"
