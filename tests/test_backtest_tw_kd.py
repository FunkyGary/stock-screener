import pandas as pd

from tests._scripts import load_script

bt = load_script("backtest_tw_strategy")


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
                "ret20": 0.1,
                "ext": 0.05,
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
    for _, weights, _, _, _ in bt._kd_variants(base):
        assert weights.get("kd_cross", 0.0) in (0.0, 0.75, 1.5)
    label, weights, gate, exit_rule, entry_gate = bt._kd_variants(base)[0]
    assert "kd_cross" not in weights and gate is None and exit_rule == "ma5"
    assert entry_gate is None
    assert label.startswith("kd_kd_cross0_")


def _gate(sq10=None, sq20=None, above_upper=None, rsi=None) -> dict:
    return {"sq10": sq10, "sq20": sq20, "above_upper": above_upper, "rsi": rsi}


def test_entry_gates_reject_unknown_inputs():
    unknown = _gate()
    for name, check in bt.ENTRY_GATES.items():
        assert not check(unknown), name


def test_squeeze_gates_need_recent_squeeze_and_optionally_upper_band_break():
    squeezed = _gate(sq10=0.15, sq20=0.15, above_upper=False)
    loose = _gate(sq10=0.5, sq20=0.5, above_upper=True)
    assert bt.ENTRY_GATES["sq10_20"](squeezed)
    assert not bt.ENTRY_GATES["sq10_20"](loose)
    assert not bt.ENTRY_GATES["sq10_20_up"](squeezed)
    assert bt.ENTRY_GATES["sq10_20_up"](_gate(sq10=0.15, above_upper=True))
    assert bt.ENTRY_GATES["sq20_35"](_gate(sq20=0.3))
    assert not bt.ENTRY_GATES["sq20_20"](_gate(sq20=0.3))


def test_band_and_rsi_gates():
    assert bt.ENTRY_GATES["above_upper"](_gate(above_upper=True))
    assert bt.ENTRY_GATES["below_upper"](_gate(above_upper=False))
    assert not bt.ENTRY_GATES["below_upper"](_gate(above_upper=True))
    assert bt.ENTRY_GATES["rsi_le70"](_gate(rsi=70.0))
    assert not bt.ENTRY_GATES["rsi_le70"](_gate(rsi=71.0))
    assert bt.ENTRY_GATES["rsi_le80"](_gate(rsi=75.0))


def test_squeeze_lookback_excludes_today():
    # Tight range for 200 bars, then a violent breakout on the last bar.
    n = 201
    close = [100.0 + (0.1 if i % 2 else 0.0) for i in range(n - 1)] + [130.0]
    frame = pd.DataFrame(
        {"Open": close, "High": close, "Low": close, "Close": close, "Volume": 1000.0},
        index=pd.date_range("2024-01-01", periods=n),
    )
    ind = bt._indicators_for_frame(frame)
    last = ind.iloc[-1]
    assert last["sq10"] <= 0.5  # prior bars were squeezed; today's expansion not included
    assert last["close"] > last["bb_upper"]


def test_gate_variants_start_with_baseline_and_cover_every_gate():
    variants = bt._gate_variants(bt.DEFAULT_WEIGHTS)
    assert variants[0][0] == "gate_none" and variants[0][4] is None
    assert {v[4] for v in variants[1:]} == set(bt.ENTRY_GATES)
