import pandas as pd

from tests._scripts import load_script
import pytest

bt = load_script("backtest_tw_strategy")

DATES = list(pd.date_range("2024-01-01", periods=8))


def _frame(prices: list[float]) -> pd.DataFrame:
    return pd.DataFrame({"Open": prices, "Close": prices}, index=DATES)


def _data() -> dict:
    flat = [100.0] * 8
    return {bt.BENCHMARK: _frame(flat), "AAA.TW": _frame(flat), "BBB.TW": _frame(flat)}


def _row(special=False, sell=False, ratio=0.6) -> dict:
    return {"score": ratio * 10, "max_score": 10.0, "ratio": ratio, "special": special,
            "sell": sell, "ret20": 0.1, "ext": 0.05}


def _count(trades, action):
    return sum(1 for t in trades if t["action"] == action)


def test_defaults_buy_every_candidate_and_sell_on_flag():
    signals = {DATES[0]: {"AAA.TW": _row(special=True), "BBB.TW": _row(special=True)},
               DATES[1]: {"AAA.TW": _row(sell=True), "BBB.TW": _row(sell=True)}}
    _, trades, holdings = bt.run_backtest(_data(), signals, {}, DATES)
    assert _count(trades, "buy") == 2 and _count(trades, "sell") == 2 and not holdings


def test_max_positions_caps_entries_and_rank_key_picks_which():
    signals = {DATES[0]: {"AAA.TW": _row(special=True, ratio=0.9), "BBB.TW": _row(special=True, ratio=0.6)}}
    _, trades, holdings = bt.run_backtest(_data(), signals, {}, DATES, max_positions=1)
    assert list(holdings) == ["AAA.TW"]  # default ranking: highest score first
    _, trades, holdings = bt.run_backtest(
        _data(), signals, {}, DATES, max_positions=1, rank_key=lambda _s, row: -row["ratio"]
    )
    assert list(holdings) == ["BBB.TW"]


def test_min_hold_days_ignores_early_sell_flags():
    signals = {DATES[0]: {"AAA.TW": _row(special=True)},
               DATES[1]: {"AAA.TW": _row(sell=True)},
               DATES[4]: {"AAA.TW": _row(sell=True)}}
    _, trades, holdings = bt.run_backtest(_data(), signals, {}, DATES, min_hold_days=3)
    sells = [t for t in trades if t["action"] == "sell"]
    assert len(sells) == 1 and sells[0]["date"] == DATES[5]
    assert not holdings
    # without the minimum hold the first sell flag exits one day earlier
    _, trades, _ = bt.run_backtest(_data(), signals, {}, DATES)
    assert [t["date"] for t in trades if t["action"] == "sell"] == [DATES[2]]


def test_round_trip_cost_is_charged_half_each_side():
    signals = {DATES[0]: {"AAA.TW": _row(special=True)},
               DATES[1]: {"AAA.TW": _row(sell=True)}}
    _, free, _ = bt.run_backtest(_data(), signals, {}, DATES)
    _, paid, _ = bt.run_backtest(_data(), signals, {}, DATES, round_trip_cost=0.004)
    buy_free, sell_free = (next(t["amount"] for t in free if t["action"] == a) for a in ("buy", "sell"))
    sell_paid = next(t["amount"] for t in paid if t["action"] == "sell")
    assert sell_free == pytest.approx(buy_free)
    assert sell_paid == pytest.approx(buy_free * (1 - 0.002) ** 2)
