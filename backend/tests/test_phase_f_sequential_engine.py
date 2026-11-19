"""
Phase F tests: the sequential signal inside the recommendation engine.
No database. Uses the real recommendation_engine.py (without the semantic model).

Run from the backend folder:
    $env:DISABLE_SEMANTIC_MODEL="true"
    python -m pytest tests/test_phase_f_sequential_engine.py -q
"""
import json
import math
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DISABLE_SEMANTIC_MODEL", "true")

import recommendation_engine as eng  # noqa: E402

NOW = datetime(2026, 11, 7, 9, 0)
STUDENT = {"student_id": "s1", "skills": ["Python", "Machine Learning"], "interests": ["Artificial Intelligence", "Hackathon"],
           "career_goal": "AI Engineer", "preferred_domains": ["Technical"], "department": "AIML", "year": "4"}


def events():
    rows = [
        ("e1", "Machine Learning Bootcamp", "Workshop", ["Python", "Machine Learning"], "Hands-on ML projects"),
        ("e2", "CyberShield Ethical Hacking Challenge", "Competition", ["Cybersecurity"], "Capture the flag security contest"),
        ("e3", "Startup Pitch Night", "Entrepreneurship", ["Pitching"], "Pitch your startup idea"),
        ("e4", "Inter-College Hackathon", "Hackathon", ["Python", "Web Development"], "24 hour hackathon"),
        ("e5", "Classical Music Evening", "Cultural", ["Singing"], "An evening of music"),
    ]
    return [{"id": i, "title": t, "category": c, "skills": s, "description": d, "date": f"2026-12-{10 + n}",
             "time": "10:00 AM – 1:00 PM", "capacity": 100, "registered_count": 5}
            for n, (i, t, c, s, d) in enumerate(rows)]


def twins():
    """Three events that are identical except for their id: every other signal is equal."""
    return [{"id": i, "title": "Python Workshop", "category": "Workshop", "skills": ["Python"], "description": "Learn Python",
             "date": "2026-12-10", "time": "10:00 AM – 1:00 PM", "capacity": 100, "registered_count": 5} for i in ("a", "b", "c")]


def run(sequential=None, feedback=None, evs=None):
    return eng.get_recommendations(student=STUDENT, events=evs or events(), feedback=feedback or {}, top_k=10, now=NOW,
                                   sequential_scores=sequential)


def dump(x):
    return json.dumps(x, sort_keys=True, default=str)


def by_id(recs):
    return {r["id"]: r for r in recs}


GOOD = {"e1": 90, "e2": 20, "e3": 40, "e4": 75, "e5": 10}


# ------------------------------------------------------------------ OFF means exactly as before
def test_without_sequential_scores_nothing_changes_at_all():
    base = dump(run())
    for off in (None, {}, {"e1": 80}, {"e1": 80, "e2": 10}, {"e1": 50, "e2": 50, "e3": 50, "e4": 50},
                {"zzz": 1, "yyy": 2, "xxx": 3}, {"e1": float("nan"), "e2": "x", "e3": None, "e4": True},
                "not a mapping", 12):
        assert dump(run(off)) == base, off


def test_no_sequential_fields_appear_when_the_signal_is_off():
    for r in run():
        assert "sequential_score" not in r and "sequential_scored" not in r
        assert "sequential" not in r["breakdown"] and "sequential" not in r["signals_used"]
        assert "sequential" not in r["signals_unavailable"] and "sequential" not in r["signal_weights"]


def test_prepare_sequential_scores_rules():
    evs = events()
    assert eng.prepare_sequential_scores(GOOD, evs) == {k: float(v) for k, v in GOOD.items()}
    assert eng.prepare_sequential_scores(None, evs) == {} and eng.prepare_sequential_scores({}, evs) == {}
    assert eng.prepare_sequential_scores({"e1": 1, "e2": 2}, evs) == {}                        # fewer than 3
    assert eng.prepare_sequential_scores({"e1": 5, "e2": 5, "e3": 5}, evs) == {}               # no spread
    assert eng.prepare_sequential_scores({"e1": 1, "e2": 2, "nope": 3}, evs) == {}             # unknown id does not count
    assert eng.prepare_sequential_scores({"e1": 150, "e2": -20, "e3": 40}, evs) == {"e1": 100.0, "e2": 0.0, "e3": 40.0}
    junk = {"e1": 10, "e2": 20, "e3": 30, "e4": float("nan"), "e5": "9"}
    assert set(eng.prepare_sequential_scores(junk, evs)) == {"e1", "e2", "e3"}
    assert eng.prepare_sequential_scores(7, evs) == {}


# ------------------------------------------------------------------ ON: weights, ranking, reasons
def test_the_weight_table_matches_the_plan():
    w = eng.SEQUENTIAL_WEIGHTS
    assert (w["content"], w["semantic"], w["lexical"], w["sequential"]) == (0.25, 0.25, 0.25, 0.20)
    assert (w["feedback"], w["availability"], w["context"]) == (0.10, 0.10, 0.05)
    assert ("timetable" in w) == bool(eng.TIMETABLE_AFFECTS_SCORE)
    assert "sequential" not in eng.BASE_WEIGHTS                                                # the old table is untouched


def test_when_on_the_signal_is_used_with_normalised_weights():
    for r in run(GOOD):
        assert "sequential" in r["signals_used"] and r["sequential_scored"] is True
        weights = r["signal_weights"]
        assert math.isclose(sum(weights.values()), 1.0, abs_tol=0.01)
        both = [weights.get("content"), weights.get("sequential")]
        assert math.isclose(both[1] / both[0], 0.20 / 0.25, rel_tol=0.05)                      # 0.20 : 0.25 as planned
        assert r["breakdown"]["sequential"] == r["sequential_score"]


def test_a_higher_sequential_score_ranks_higher_when_everything_else_is_equal():
    out = by_id(run({"a": 90, "b": 50, "c": 10}, evs=twins()))
    assert out["a"]["matchScore"] > out["b"]["matchScore"] > out["c"]["matchScore"]
    assert out["a"]["rank"] < out["b"]["rank"] < out["c"]["rank"]


def test_the_reason_is_shown_only_for_events_the_model_likes():
    out = by_id(run(GOOD))
    assert "Fits your recent activity" in out["e1"]["reasons"] and "Fits your recent activity" in out["e4"]["reasons"]
    for low in ("e2", "e3", "e5"):
        assert "Fits your recent activity" not in out[low]["reasons"]
    assert not any("Fits your recent activity" in r["reasons"] for r in run())                  # never when off


def test_events_the_model_could_not_score_get_a_neutral_stand_in():
    partial = {"e1": 90, "e2": 20, "e3": 70}                                                    # e4, e5 unscored
    out = by_id(run(partial))
    for unscored in ("e4", "e5"):
        r = out[unscored]
        assert r["sequential_scored"] is False and r["sequential_score"] is None
        assert "sequential" not in r["signals_used"] and "sequential" in r["signals_unavailable"]
        assert "Fits your recent activity" not in r["reasons"]
    neutral = by_id(run({**partial, "e4": 50, "e5": 50}))                                      # an explicit 50 is the same thing
    for unscored in ("e4", "e5"):
        assert out[unscored]["matchScore"] == neutral[unscored]["matchScore"]


def test_out_of_range_and_junk_values_are_cleaned_not_trusted():
    clean = by_id(run({"e1": 100, "e2": 0, "e3": 40, "e4": 75, "e5": 10}))
    messy = by_id(run({"e1": 999, "e2": -50, "e3": 40, "e4": 75, "e5": 10, "ghost": 70}))
    for event_id in clean:
        assert clean[event_id]["matchScore"] == messy[event_id]["matchScore"]


def test_it_combines_with_saved_feedback_and_with_hidden_events():
    out = by_id(run(GOOD, feedback={"saved_ids": ["e4"]}))
    assert {"sequential", "feedback"} <= set(out["e4"]["signals_used"])
    hidden = run(GOOD, feedback={"not_interested_ids": ["e1"]})
    assert "e1" not in [r["id"] for r in hidden]                                                # a hidden event stays hidden


def test_hiding_does_not_turn_the_sequential_signal_on_or_off():
    assert all("sequential" in r["signals_used"] for r in run(GOOD, feedback={"not_interested_ids": ["e5"]}))
    assert dump(run(None, feedback={"not_interested_ids": ["e5"]})) == dump(run({}, feedback={"not_interested_ids": ["e5"]}))


def test_the_old_calling_style_still_works():
    assert eng.get_recommendations(STUDENT, events(), {}, top_k=3, now=NOW)                    # positional arguments, no new ones
    assert eng.recommend_events(STUDENT, events(), {}, None, 3, None, NOW)