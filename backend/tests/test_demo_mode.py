"""
tests/test_demo_mode.py
Pure tests: no database, no torch, no Atlas. Run from the backend folder:
    python -m pytest tests/test_demo_mode.py -q
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import demo_mode as dm  # noqa: E402


def _status(**kw):
    base = {"available": True, "trained_on_simulated": True, "quality_gate_passed": False,
            "gate_override_active": False, "simulated_override_active": False}
    base.update(kw)
    return base


def test_off_when_no_override_is_active():
    assert dm.demo_info(_status()) is None


def test_off_when_the_model_did_not_load_even_if_overrides_are_set():
    assert dm.demo_info(_status(available=False, simulated_override_active=True,
                                gate_override_active=True)) is None


def test_on_for_simulated_override():
    info = dm.demo_info(_status(simulated_override_active=True))
    assert info["active"] is True and info["trained_on_simulated"] is True
    assert "synthetic" in info["message"].lower()
    assert info["quality_gate_passed"] is False


def test_on_for_gate_override_alone():
    info = dm.demo_info(_status(gate_override_active=True, trained_on_simulated=False))
    assert info["active"] is True and info["trained_on_simulated"] is False
    assert "quality gate" in info["message"].lower()


def test_never_raises_on_bad_input():
    for bad in (None, 5, "x", [], {}, {"available": True, "gate_override_active": object()}):
        assert dm.demo_info(bad) is None or isinstance(dm.demo_info(bad), dict)


def test_get_demo_info_is_none_when_sequential_module_is_missing(monkeypatch):
    monkeypatch.setitem(sys.modules, "sequential_recommender", None)   # import now fails
    assert dm.get_demo_info() is None