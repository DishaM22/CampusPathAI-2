"""
Phase H tests, part 1: the recommendation engine and "not interested".
No database. Uses the real recommendation_engine.py (without the semantic model).

Run from the backend folder:
    $env:DISABLE_SEMANTIC_MODEL="true"
    python -m pytest tests/test_phase_h_engine.py -q
"""
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DISABLE_SEMANTIC_MODEL", "true")

import recommendation_engine as eng  # noqa: E402

NOW = datetime(2026, 11, 7, 9, 0)

STUDENT = {"student_id": "s1", "skills": ["Python", "Machine Learning"], "interests": ["Artificial Intelligence", "Hackathon"],
           "career_goal": "AI Engineer", "preferred_domains": ["Technical"], "department": "AIML", "year": "4"}


def make_events():
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


def recommend(feedback=None, events=None, student=None):
    return eng.get_recommendations(student=student or STUDENT, events=events or make_events(),
                                   feedback=feedback if feedback is not None else {}, top_k=10, now=NOW)


def ids(recs):
    return [r["id"] for r in recs]


# ------------------------------------------------------------------ the new behaviour
def test_a_hidden_event_is_left_out_for_that_student():
    assert "e1" in ids(recommend())
    hidden = recommend({"not_interested_ids": ["e1"]})
    assert "e1" not in ids(hidden)
    assert {"e2", "e3", "e4", "e5"} <= set(ids(hidden))                 # everything else is still there


def test_several_hidden_events_are_all_left_out():
    hidden = recommend({"not_interested_ids": ["e1", "e4"]})
    assert set(ids(hidden)) == {"e2", "e3", "e5"}


def test_hiding_one_event_does_not_change_the_scores_of_the_others():
    before = {r["id"]: r["matchScore"] for r in recommend()}
    after = {r["id"]: r["matchScore"] for r in recommend({"not_interested_ids": ["e2"]})}
    assert "e2" not in after
    for event_id, score in after.items():
        assert score == before[event_id], event_id


def test_not_interested_is_not_positive_feedback_and_never_switches_the_feedback_signal_on():
    assert eng.has_feedback_history({"not_interested_ids": ["e1"]}) is False
    assert eng.has_feedback_history({"saved_ids": ["e1"]}) is True
    assert eng.has_feedback_history({"registered_ids": ["e1"], "not_interested_ids": ["e2"]}) is True

    plain = {r["id"]: r for r in recommend()}
    with_hidden = {r["id"]: r for r in recommend({"not_interested_ids": ["e1"]})}
    for event_id, result in with_hidden.items():
        assert result["signals_used"] == plain[event_id]["signals_used"]
        assert result["matchScore"] == plain[event_id]["matchScore"]
        assert "feedback" not in result["signals_used"]


def test_ids_are_compared_as_text_and_an_alias_key_works():
    events = make_events()
    events[0]["id"] = 7                                                  # a numeric event id
    assert 7 not in ids(recommend({"not_interested_ids": [7]}, events))
    assert 7 not in ids(recommend({"not_interested_ids": ["7"]}, events))
    assert "e2" not in ids(recommend({"not_interested": ["e2"]}))        # alias key


def test_hiding_everything_gives_an_empty_list_not_an_error():
    assert recommend({"not_interested_ids": ["e1", "e2", "e3", "e4", "e5"]}) == []


def test_a_hidden_event_that_was_also_saved_is_still_left_out():
    out = recommend({"saved_ids": ["e1"], "not_interested_ids": ["e1"]})
    assert "e1" not in ids(out)


def test_other_students_are_unaffected_because_the_list_is_per_request():
    # The engine only knows what it is given: the same events for a student with no hidden list are all shown.
    assert set(ids(recommend({"not_interested_ids": []}))) == {"e1", "e2", "e3", "e4", "e5"}
    assert set(ids(recommend(None))) == {"e1", "e2", "e3", "e4", "e5"}


# ------------------------------------------------------------------ nothing else changed
def test_without_a_hidden_list_the_feedback_and_filters_work_as_before():
    out = recommend({"registered_ids": ["e1"], "attended_ids": ["e2"], "saved_ids": ["e3"]})
    assert "e1" not in ids(out) and "e2" not in ids(out)                 # registered / attended still removed
    assert "e3" in ids(out)                                              # saved is still shown (and boosted)
    assert "feedback" in next(r for r in out if r["id"] == "e3")["signals_used"]


def test_the_id_sets_and_the_eligibility_check_stay_compatible_with_older_callers():
    sets = eng.collect_feedback_ids({"saved_ids": ["a"], "registered_ids": ["b"], "attended_ids": ["c"],
                                     "not_interested_ids": ["d"]})
    assert sets == {"saved": {"a"}, "registered": {"b"}, "attended": {"c"}, "not_interested": {"d"}}

    old_style = {"saved": set(), "registered": set(), "attended": set()}      # no "not_interested" key
    event = make_events()[0]
    assert eng.ineligibility_reason(event, old_style, ([], 0), NOW) is None
    assert eng.ineligibility_reason(event, {**old_style, "not_interested": {"e1"}}, ([], 0), NOW) == "not_interested"


def test_expired_full_and_registered_events_keep_their_own_reasons():
    event = make_events()[0]
    base = {"saved": set(), "registered": set(), "attended": set(), "not_interested": set()}
    assert eng.ineligibility_reason(event, {**base, "attended": {"e1"}}, ([], 0), NOW) == "already_attended"
    assert eng.ineligibility_reason(event, {**base, "registered": {"e1"}}, ([], 0), NOW) == "already_registered"
    old = {**event, "date": "2026-01-01"}
    assert eng.ineligibility_reason(old, base, ([], 0), NOW) == "expired"