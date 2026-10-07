import importlib.util
import sys
from pathlib import Path

import pandas as pd

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "backtest_tw_strategy.py"
_spec = importlib.util.spec_from_file_location("backtest_tw_strategy", _SCRIPT)
bt = importlib.util.module_from_spec(_spec)
sys.modules["backtest_tw_strategy"] = bt
_spec.loader.exec_module(bt)


def _row(sell: bool, kd: dict) -> dict:
    return {"sell": sell, "k": None, "kd": kd}


def test_kd_state_level_checks_ignore_missing_previous_bar():
    state = bt._kd_state(30.0, 40.0, None, None)
    assert state["has_now"] and not state["has_prev"]
    assert state["bear"] and not state["above"]
    assert not state["golden"] and not state["dead"]


def test_kd_state_crosses_need_previous_bar():
    golden = bt._kd_state(45.0, 40.0, 38.0, 40.0)
    assert golden["golden"] and golden["golden_low"] and not golden["dead"]
    high_dead = bt._kd_state(78.0, 80.0, 85.0, 82.0)
    assert high_dead["dead"] and high_dead["dead_from_high"] and not high_dead["golden"]
    assert not bt._kd_state(60.0, 55.0, 58.0, 59.0)["golden_low"]


def test_kd_state_unknown_when_today_missing():
    state = bt._kd_state(None, None, 30.0, 40.0)
    assert not any(state.values())


def test_confirmed_exit_not_suppressed_when_previous_kd_missing():
    # MA5 break with K < D today but no yesterday KD: still a valid exit.
    bear_today = bt._kd_state(30.0, 40.0, None, None)
    assert bt._sell_flag(_row(True, bear_today), "ma5_and_kd_bear")
    # MA5 break while K still leads D: ignored by the confirmed exit.
    bull_today = bt._kd_state(50.0, 40.0, 48.0, 41.0)
    assert not bt._sell_flag(_row(True, bull_today), "ma5_and_kd_bear")
    # KD entirely unknown falls back to the plain MA5 exit.
    unknown = bt._kd_state(None, None, None, None)
    assert bt._sell_flag(_row(True, unknown), "ma5_and_kd_bear")


def test_kd_exit_never_coexists_with_entry():
    facts = {
        pd.Timestamp("2021-01-04"): {
            "AAA.TW": {
                "facts": [("above_all", 1.0)],
                "newly_above": True,
                "above_ma5": True,
                "sell": False,
                "k": 60.0,
                "kd": bt._kd_state(70.0, 75.0, 82.0, 78.0),  # dead cross from high
            }
        }
    }
    weights = {"above_all": 1.0}
    plain = bt._signals_from_facts(facts, weights)
    kd_exit = bt._signals_from_facts(facts, weights, exit_rule="ma5_or_kd_dead80")
    row = next(iter(plain.values()))["AAA.TW"]
    assert row["special"] and not row["sell"]
    row = next(iter(kd_exit.values()))["AAA.TW"]
    assert row["sell"] and not row["special"]


def test_kd_variants_zero_weights_override_base():
    base = {**bt.DEFAULT_WEIGHTS, "kd_cross": 1.5}
    for _, weights, _, _ in bt._kd_variants(base):
        assert weights.get("kd_cross", 0.0) in (0.0, 0.75, 1.5)
    label, weights, gate, exit_rule = bt._kd_variants(base)[0]
    assert "kd_cross" not in weights and gate is None and exit_rule == "ma5"
    assert label.startswith("kd_kd_cross0_")
