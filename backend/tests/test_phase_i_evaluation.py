"""
tests/test_phase_i_evaluation.py
Pure tests: no database, no Atlas, no torch. Run from the backend folder:
    python -m pytest tests/test_phase_i_evaluation.py -q
"""
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))          # backend folder

import phase_i_evaluate as ev                      # noqa: E402


def test_quadratic_kappa_perfect_and_degenerate():
    assert ev.quadratic_kappa([0, 1, 2, 1], [0, 1, 2, 1]) == 1.0
    assert ev.quadratic_kappa([1, 1, 1], [1, 1, 1]) is None      # no variation


def test_quadratic_kappa_matches_sklearn_when_available():
    sk = __import__("pytest").importorskip("sklearn.metrics")
    rng = np.random.default_rng(3)
    a = rng.integers(0, 3, 60)
    b = np.clip(a + rng.integers(-1, 2, 60), 0, 2)
    want = sk.cohen_kappa_score(a, b, weights="quadratic", labels=[0, 1, 2])
    assert abs(ev.quadratic_kappa(a, b) - want) < 1e-9


def test_perfect_ranking_scores_one():
    gains = [2, 0, 1, 0, 0]
    order = np.argsort(-np.array(gains), kind="stable")
    m = ev.student_metrics(order, gains, list("abcde"), k=2, kn=5)
    assert m["precision"] == 1.0 and m["hit"] == 1.0
    assert abs(m["ndcg"] - 1.0) < 1e-9 and abs(m["map"] - 1.0) < 1e-9


def test_worst_ranking_scores_low():
    gains = [2, 1, 0, 0, 0]
    order = np.array([2, 3, 4, 1, 0])
    m = ev.student_metrics(order, gains, list("abcde"), k=3, kn=5)
    assert m["precision"] == 0.0 and m["hit"] == 0.0 and m["ndcg"] < 0.6


def test_student_without_relevant_event_is_nan_not_zero():
    m = ev.student_metrics(np.arange(4), [0, 0, 0, 0], list("abcd"), 3, 4)
    assert m["precision"] == 0.0
    assert math.isnan(m["hit"]) and math.isnan(m["ndcg"]) and math.isnan(m["map"])


def test_ties_get_no_free_ride():
    # All-equal scores must behave like random, not like "first item wins".
    items = [{"pair_id": f"S1-E{i:02d}", "event_id": str(i), "category": "c",
              "scores": {"popularity": 0.0}} for i in range(10)]
    gains = {it["pair_id"]: (2.0 if i == 0 else 0.0) for i, it in enumerate(items)}
    per, _ = ev.evaluate([{"label": "S1", "items": items}], ["popularity"],
                         gains, k=3, kn=5, draws=400, seed=1)
    p = per["popularity"]["hit"][0]
    assert 0.2 < p < 0.4                              # expected 3/10


def test_bootstrap_interval_contains_mean_and_is_wide_for_few_students():
    v = np.array([0.2, 0.9, 0.5, 0.4, 0.8, 0.1])
    idx = np.random.default_rng(0).integers(0, 6, (2000, 6))
    lo, hi = ev.bootstrap_ci(v, idx)
    assert lo < v.mean() < hi and (hi - lo) > 0.2


def test_ratings_validation(tmp_path):
    p = tmp_path / "r.csv"
    p.write_text("pair_id,relevance\nS1-E01,2\nS1-E02,\nS1-E03,3\nS1-E04,1\n",
                 encoding="utf-8")
    ratings, missing, invalid = ev.load_ratings(str(p))
    assert ratings == {"S1-E01": 2.0, "S1-E04": 1.0}
    assert missing == ["S1-E02"] and invalid == [("S1-E03", "3")]


def test_consensus_and_agreement():
    a = {"x": 2.0, "y": 0.0, "z": 1.0}
    b = {"x": 2.0, "y": 1.0, "z": 1.0}
    assert ev.consensus([a, b]) == {"x": 2.0, "y": 0.5, "z": 1.0}
    g = ev.agreement(a, b)
    assert g["n"] == 3 and abs(g["exact_agreement"] - 2 / 3) < 1e-9