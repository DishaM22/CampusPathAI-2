"""
Phase F/G tests: a blocked sequential model is rejected from its small JSON report,
BEFORE PyTorch is imported. No PyTorch and no database needed.

Run from the backend folder:
    python -m pytest tests/test_phase_f_prechecks.py -q
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sequential_recommender as sr  # noqa: E402


def folder_with(report=None, files=True):
    folder = tempfile.mkdtemp()
    if files:
        for name in ("model.pt", "vocab.json"):
            with open(os.path.join(folder, name), "w") as handle:
                handle.write("not real")                       # unreadable on purpose: loading it would fail differently
    if report is not None:
        with open(os.path.join(folder, "train_report.json"), "w", encoding="utf-8") as handle:
            json.dump(report, handle)
    return folder


def test_the_report_is_read_without_pytorch_and_only_for_what_it_says():
    assert sr.read_report_meta(tempfile.mkdtemp()) is None                          # no report
    folder = folder_with({"trained_on_simulated": False, "trained_at": "t",
                          "quality_gate": {"passed": True, "reasons": ["ok"]}})
    assert sr.read_report_meta(folder) == {"trained_on_simulated": False, "trained_at": "t",
                                           "quality_gate_passed": True, "quality_gate_reasons": ["ok"]}
    old = folder_with({"trained_on_simulated": True, "trained_at": "t"})            # an older report without a gate
    assert "quality_gate_passed" not in sr.read_report_meta(old)
    broken = tempfile.mkdtemp()
    with open(os.path.join(broken, "train_report.json"), "w") as handle:
        handle.write("{not json")
    assert sr.read_report_meta(broken) is None


def test_a_failed_gate_is_decided_from_the_report_before_any_model_loading():
    folder = folder_with({"trained_on_simulated": False, "quality_gate": {"passed": False, "reasons": ["too weak"]}})
    rec = sr.SequentialRecommender(model_dir=folder)
    status = rec.status()
    assert status["available"] is False and "failed the quality gate" in status["reason"] and "too weak" in status["reason"]
    assert "PyTorch" not in status["reason"] and "could not load" not in status["reason"]     # it never got that far
    assert status["quality_gate_passed"] is False and status["trained_on_simulated"] is False


def test_simulated_or_gate_less_reports_are_also_decided_early():
    simulated = sr.SequentialRecommender(model_dir=folder_with(
        {"trained_on_simulated": True, "quality_gate": {"passed": True, "reasons": []}})).status()
    assert "simulated" in simulated["reason"] and "could not load" not in simulated["reason"]

    older = sr.SequentialRecommender(model_dir=folder_with({"trained_on_simulated": True})).status()
    assert "no quality-gate record" in older["reason"] and "PyTorch" not in older["reason"]


def test_overrides_let_it_proceed_past_the_report_to_the_real_loading_step():
    folder = folder_with({"trained_on_simulated": True, "quality_gate": {"passed": False, "reasons": []}})
    rec = sr.SequentialRecommender(model_dir=folder, require_gate=False, allow_simulated=True)
    reason = rec.status()["reason"]
    assert "failed the quality gate" not in reason and "simulated" not in reason            # the report no longer blocks
    assert "PyTorch could not be imported" in reason or "could not load the model" in reason  # the dummy files do


def test_a_report_never_unlocks_a_model_by_itself():
    folder = folder_with({"trained_on_simulated": False, "quality_gate": {"passed": True, "reasons": []}})
    rec = sr.SequentialRecommender(model_dir=folder)
    status = rec.status()
    assert status["available"] is False                                                     # the checkpoint itself must still load and agree
    assert rec.score_events([], [{"_id": "e"}]) == {}