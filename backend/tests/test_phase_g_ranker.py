"""
Phase G tests: the learned ranker (trained_ranker.py), its training script
(train_ranker.py) and its use inside the engine. No real database.

Run from the backend folder:
    $env:DISABLE_SEMANTIC_MODEL="true"
    python -m pytest tests/test_phase_g_ranker.py -q
Needs: scikit-learn, joblib (already installed with your project).
"""
import json
import math
import os
import random
import sys
import tempfile
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DISABLE_SEMANTIC_MODEL", "true")

import recommendation_engine as eng  # noqa: E402
import train_ranker as trn  # noqa: E402
import trained_ranker as tr  # noqa: E402

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
NOW = datetime(2026, 11, 7, 9, 0)


def raises(error, function, *args, **kwargs):
    try:
        function(*args, **kwargs)
    except error:
        return True
    return False


# ------------------------------------------------------------------ synthetic data (clearly NOT real)
def make_pairs(n=1500, predictive=True, seed=1, students=40):
    """
    Pairs whose label depends on the text match (which the hybrid score here barely contains),
    so a ranker CAN help. With predictive=False the labels are pure noise.
    """
    rng = random.Random(seed)
    pairs = []
    for i in range(n):
        content, text = rng.uniform(0, 100), rng.uniform(0, 100)
        hybrid = 0.3 * content + 0.7 * rng.uniform(0, 100)
        p = 1 / (1 + math.exp(-(text - 50) / 12)) if predictive else 0.5
        pairs.append({"student_id": f"s{i % students}", "event_id": f"e{i}", "label": 1 if rng.random() < p else 0,
                      "ts": T0 + timedelta(minutes=i), "features": {"content": content, "text_match": text},
                      "hybrid": hybrid, "text_signal": "lexical"})
    return pairs


# ------------------------------------------------------------------ the inputs: no feedback, no availability
def test_the_ranker_input_has_no_feedback_and_no_availability_feature():
    assert tr.FEATURE_NAMES == ("content", "text_match")
    assert not any(("feedback" in n or "availab" in n or "saved" in n or "regist" in n) for n in tr.FEATURE_NAMES)
    assert tr.feature_row(50, 25) == [0.5, 0.25]
    assert tr.feature_row(150, -20) == [1.0, 0.0]                                   # clamped
    assert raises(TypeError, tr.feature_row, 1, 2, 3, True)                         # the old 4-argument form is gone


def test_text_signal_prefers_semantic_then_keyword():
    assert tr.text_signal_for(True, True) == "semantic"
    assert tr.text_signal_for(False, True) == "lexical"
    assert tr.text_signal_for(False, False) is None


# ------------------------------------------------------------------ the ranker object
def trained_ranker(signal="lexical", seed=1):
    out = tr.train_and_evaluate(make_pairs(seed=seed), signal, seed=7)
    assert out["status"] == "trained"
    return out["ranker"]


def test_a_trained_ranker_scores_0_to_100_and_prefers_the_better_match():
    r = trained_ranker()
    high = r.score_event_features(content=70, semantic=0, lexical=95, semantic_available=False, lexical_available=True)
    low = r.score_event_features(content=70, semantic=0, lexical=5, semantic_available=False, lexical_available=True)
    assert 0 <= low < high <= 100


def test_the_ranker_ignores_availability_and_refuses_a_different_text_signal():
    r = trained_ranker()
    args = dict(content=60, semantic=0, lexical=70, semantic_available=False, lexical_available=True)
    assert r.score_event_features(**args, availability=0, availability_known=False) == \
           r.score_event_features(**args, availability=100, availability_known=True)
    # trained on the keyword signal, asked about an event scored by the semantic model -> no opinion
    assert r.score_event_features(content=60, semantic=80, lexical=70, semantic_available=True, lexical_available=True) is None
    assert r.score_event_features(content=60, semantic=0, lexical=0, semantic_available=False, lexical_available=False) is None


def test_the_ranker_never_raises():
    class Broken:
        def predict_proba(self, rows):
            raise RuntimeError("boom")

    ranker = tr.Ranker(Broken(), "lexical")
    assert ranker.score_event_features(content=1, semantic=0, lexical=1, semantic_available=False, lexical_available=True) is None
    good = trained_ranker()
    assert good.score_event_features(content="x", semantic=None, lexical=None, semantic_available=False, lexical_available=True) is None


# ------------------------------------------------------------------ splitting, refusing, the gate
def test_the_split_is_by_time_with_no_overlap():
    pairs = make_pairs(600)
    train, val, test = tr.temporal_split(pairs)
    assert len(train) + len(val) + len(test) == 600 and train and val and test
    assert max(p["ts"] for p in train) < min(p["ts"] for p in val)
    assert max(p["ts"] for p in val) < min(p["ts"] for p in test)
    assert tr.temporal_split([]) == ([], [], [])
    assert raises(ValueError, tr.temporal_split, pairs, 0.9, 0.2)


def test_too_little_data_is_refused_with_the_reasons_and_no_model():
    out = tr.train_and_evaluate(make_pairs(60, students=5), "lexical")
    assert out["status"] == "refused" and out["ranker"] is None
    text = " ".join(out["report"]["reasons"])
    assert "labelled pairs" in text and "students" in text


def test_one_class_only_is_refused():
    pairs = [{**p, "label": 1} for p in make_pairs(400)]
    out = tr.train_and_evaluate(pairs, "lexical")
    assert out["status"] == "refused" and out["ranker"] is None


def test_the_gate_passes_when_the_ranker_really_helps_and_records_the_evidence():
    out = tr.train_and_evaluate(make_pairs(), "lexical", seed=7)
    gate = out["report"]["quality_gate"]
    assert gate["passed"] is True and gate["difference_ci95"][0] > 0
    assert gate["auc_blend"] > gate["auc_hybrid"]
    assert gate["n_test_positive"] >= tr.TEST_MIN_EACH_CLASS and gate["n_test_negative"] >= tr.TEST_MIN_EACH_CLASS
    assert out["ranker"].meta["gate_passed"] is True and out["ranker"].meta["data_origin"] == "real"


def test_the_gate_fails_on_noise():
    out = tr.train_and_evaluate(make_pairs(predictive=False, seed=3), "lexical", seed=7)
    assert out["status"] == "trained" and out["report"]["quality_gate"]["passed"] is False
    assert out["ranker"].meta["gate_passed"] is False


def test_the_gate_fails_with_too_few_test_cases():
    pairs = make_pairs(240, students=20)                      # enough to train, too few held-out cases per class
    out = tr.train_and_evaluate(pairs, "lexical", seed=7)
    assert out["status"] == "trained"
    gate = out["report"]["quality_gate"]
    assert gate["passed"] is False and "too few test cases" in gate["reasons"][0]
    assert gate["n_test_negative"] < tr.TEST_MIN_EACH_CLASS or gate["n_test_positive"] < tr.TEST_MIN_EACH_CLASS
    assert out["ranker"].meta["gate_passed"] is False          # so the engine would never use it


def test_training_is_deterministic():
    a = tr.train_and_evaluate(make_pairs(), "lexical", seed=7)
    b = tr.train_and_evaluate(make_pairs(), "lexical", seed=7)
    assert a["report"]["quality_gate"] == b["report"]["quality_gate"]
    args = dict(content=40, semantic=0, lexical=60, semantic_available=False, lexical_available=True)
    assert a["ranker"].score_event_features(**args) == b["ranker"].score_event_features(**args)


# ------------------------------------------------------------------ saving, loading, the safety rules
def test_save_and_load_round_trip_gives_identical_scores():
    out = tr.train_and_evaluate(make_pairs(), "lexical", seed=7)
    with tempfile.TemporaryDirectory() as folder:
        tr.save_ranker(folder, out["ranker"], out["report"])
        assert os.path.isfile(os.path.join(folder, tr.MODEL_FILE)) and os.path.isfile(os.path.join(folder, tr.REPORT_FILE))
        loaded, why = tr.load_ranker(folder)
        assert why == "ok" and loaded is not None
        args = dict(content=33, semantic=0, lexical=81, semantic_available=False, lexical_available=True)
        assert loaded.score_event_features(**args) == out["ranker"].score_event_features(**args)


def test_a_failed_gate_or_synthetic_origin_blocks_the_ranker():
    failed = tr.train_and_evaluate(make_pairs(predictive=False, seed=3), "lexical", seed=7)
    with tempfile.TemporaryDirectory() as folder:
        tr.save_ranker(folder, failed["ranker"], failed["report"])
        ranker, why = tr.load_ranker(folder)
        assert ranker is None and "quality gate" in why
        assert tr.load_ranker(folder, require_gate=False)[0] is not None                  # explicit override works

    synthetic = tr.train_and_evaluate(make_pairs(), "lexical", seed=7, data_origin="synthetic")
    with tempfile.TemporaryDirectory() as folder:
        tr.save_ranker(folder, synthetic["ranker"], synthetic["report"])
        ranker, why = tr.load_ranker(folder)
        assert ranker is None and "real data" in why
        assert tr.load_ranker(folder, allow_synthetic=True)[0] is not None


def test_a_blocked_ranker_is_rejected_from_the_json_report_without_reading_the_model_file():
    failed = tr.train_and_evaluate(make_pairs(predictive=False, seed=3), "lexical", seed=7)
    with tempfile.TemporaryDirectory() as folder:
        tr.save_ranker(folder, failed["ranker"], failed["report"])
        with open(os.path.join(folder, tr.MODEL_FILE), "wb") as handle:
            handle.write(b"this is not a model")                                         # unreadable on purpose
        ranker, why = tr.load_ranker(folder)
        assert ranker is None and "quality gate" in why                                  # decided before any unpickling


def test_missing_or_corrupt_files_are_reported_not_raised():
    with tempfile.TemporaryDirectory() as folder:
        assert tr.load_ranker(os.path.join(folder, "nope")) == (None, f"no ranker found in '{os.path.join(folder, 'nope')}'")
        with open(os.path.join(folder, tr.MODEL_FILE), "wb") as handle:
            handle.write(b"garbage")
        ranker, why = tr.load_ranker(folder)
        assert ranker is None and "could not load" in why


def test_the_shared_instance_loads_once_and_obeys_the_switches():
    out = tr.train_and_evaluate(make_pairs(), "lexical", seed=7)
    with tempfile.TemporaryDirectory() as folder:
        tr.save_ranker(folder, out["ranker"], out["report"])
        saved = {k: os.environ.get(k) for k in ("RANKER_DIR", "DISABLE_RANKER")}
        try:
            os.environ["RANKER_DIR"] = folder
            os.environ.pop("DISABLE_RANKER", None)
            tr.reload_ranker()
            before = tr._holder.load_count
            assert tr.get_active_ranker() is not None
            for _ in range(4):
                tr.get_active_ranker()
            assert tr._holder.load_count == before + 1            # read from disk exactly once
            assert tr.ranker_status()["available"] is True

            os.environ["DISABLE_RANKER"] = "true"
            tr.reload_ranker()
            assert tr.get_active_ranker() is None and "disabled" in tr.ranker_status()["reason"]
        finally:
            for key, value in saved.items():
                os.environ.pop(key, None) if value is None else os.environ.__setitem__(key, value)
            tr.reload_ranker()


# ------------------------------------------------------------------ inside the engine
STUDENT = {"student_id": "s1", "skills": ["Python", "Machine Learning"], "interests": ["Artificial Intelligence"],
           "career_goal": "AI Engineer", "preferred_domains": ["Technical"], "department": "AIML", "year": "4"}


def engine_events():
    rows = [("e1", "Machine Learning Bootcamp", "Workshop", ["Python", "Machine Learning"], "Hands-on ML projects"),
            ("e2", "CyberShield Ethical Hacking Challenge", "Competition", ["Cybersecurity"], "Capture the flag security contest"),
            ("e3", "Startup Pitch Night", "Entrepreneurship", ["Pitching"], "Pitch your startup idea"),
            ("e4", "Inter-College Hackathon", "Hackathon", ["Python", "Web Development"], "24 hour hackathon")]
    return [{"id": i, "title": t, "category": c, "skills": s, "description": d, "date": f"2026-12-{10 + n}",
             "time": "10:00 AM – 1:00 PM", "capacity": 100, "registered_count": 5} for n, (i, t, c, s, d) in enumerate(rows)]


def recs(mlp=None, feedback=None):
    return eng.get_recommendations(student=STUDENT, events=engine_events(), feedback=feedback or {}, top_k=10, now=NOW, mlp_model=mlp)


class FixedRanker:
    """Stands in for a trained ranker: always answers the same number."""
    def __init__(self, value):
        self.value = value

    def score_event_features(self, **kwargs):
        return self.value


def dump(x):
    return json.dumps(x, sort_keys=True, default=str)


def test_the_engine_blends_80_20_and_names_the_method():
    base = {r["id"]: r for r in recs()}
    with_ranker = {r["id"]: r for r in recs(FixedRanker(100.0))}
    for event_id, r in with_ranker.items():
        assert r["mlp_score"] == 100.0 and r["recommendation_model"] == "Hybrid + Learned Ranker"
        assert math.isclose(r["recommendation_score"], 0.8 * base[event_id]["hybrid_score"] + 0.2 * 100.0, abs_tol=0.05)


def test_if_the_ranker_has_no_opinion_or_fails_the_engine_uses_the_hybrid_score_alone():
    base = dump(recs())
    assert dump(recs(FixedRanker(None))) == base

    class Exploding:
        def score_event_features(self, **kwargs):
            raise RuntimeError("boom")

    assert dump(recs(Exploding())) == base
    assert dump(recs(object())) == base                                                  # not a model at all: ignored as before


def test_a_ranker_trained_on_the_other_text_signal_is_ignored():
    wrong = trained_ranker(signal="semantic")                 # the engine here runs without the semantic model
    assert dump(recs(wrong)) == dump(recs())


def test_the_rankers_score_does_not_depend_on_the_students_feedback():
    ranker = trained_ranker(signal="lexical")
    plain = {r["id"]: r["mlp_score"] for r in recs(ranker)}
    saved = {r["id"]: r["mlp_score"] for r in recs(ranker, feedback={"saved_ids": ["e1"], "registered_ids": ["e4"]})
             if r["id"] in plain}
    assert plain and all(v is not None for v in plain.values())
    for event_id, value in saved.items():
        assert value == plain[event_id]                                               # no leakage path from feedback


def test_a_real_trained_ranker_works_end_to_end_in_the_engine():
    out = recs(trained_ranker(signal="lexical"))
    assert out and all(r["recommendation_model"] == "Hybrid + Learned Ranker" and 0 <= r["mlp_score"] <= 100 for r in out)


# ------------------------------------------------------------------ the training script: labels
def reg(student, event, minutes, status="registered"):
    return {"student_id": student, "event_id": event, "status": status, "registered_at": T0 + timedelta(minutes=minutes)}


def imp(student, event, minutes, source="dashboard_recommended", action="impression", **extra):
    return {"student_id": student, "event_id": event, "action": action, "source": source,
            "timestamp": T0 + timedelta(minutes=minutes), **extra}


def test_labels_registered_and_attended_are_positive_cancelled_is_not():
    pairs, stats = trn.build_label_pairs(
        [reg("a", "e1", 5), reg("a", "e2", 6, "cancelled"), reg("b", "e1", 7)],
        [{"student_id": "c", "event_id": "e3", "marked_at": T0 + timedelta(minutes=9)}], [])
    got = {(p["student_id"], p["event_id"]): p["label"] for p in pairs}
    assert got == {("a", "e1"): 1, ("b", "e1"): 1, ("c", "e3"): 1}
    assert stats["cancelled_registration_ignored"] == 1


def test_labels_negatives_come_from_recommended_impressions_and_not_interested_only():
    interactions = [imp("a", "n1", 1), imp("a", "n2", 2, source="dashboard_upcoming"), imp("a", "n3", 3, action="view"),
                    imp("a", "n4", 4, action="not_interested", source="other")]
    pairs, _ = trn.build_label_pairs([], [], interactions)
    assert {(p["event_id"], p["kind"]) for p in pairs} == {("n1", "impression"), ("n4", "not_interested")}
    more, _ = trn.build_label_pairs([], [], interactions, impression_sources=("dashboard_recommended", "dashboard_upcoming"))
    assert {p["event_id"] for p in more} == {"n1", "n2", "n4"}


def test_labels_ambiguous_cases_are_dropped_not_guessed():
    interactions = [imp("a", "x", 1), imp("a", "y", 2, action="not_interested"), imp("a", "z", 3, action="not_interested")]
    pairs, stats = trn.build_label_pairs([reg("a", "x", 10)], [], interactions, undone_pairs={("a", "z")})
    got = {(p["event_id"]): p["label"] for p in pairs}
    assert got == {"x": 1, "y": 0}                          # x was shown but then registered -> positive only; z was un-hidden -> dropped
    assert stats["negative_dropped_because_also_positive"] == 1 and stats["negative_dropped_because_undone"] == 1


def test_labels_simulated_rows_never_count_and_the_earliest_time_is_kept():
    interactions = [imp("a", "n1", 50, simulated=True), imp("a", "n2", 30), imp("a", "n2", 10), imp("a", "n2", 20, action="not_interested")]
    pairs, stats = trn.build_label_pairs([], [], interactions)
    assert [p["event_id"] for p in pairs] == ["n2"] and pairs[0]["ts"] == T0 + timedelta(minutes=10)
    assert pairs[0]["kind"] == "not_interested" and stats["simulated_ignored"] == 1


def test_labels_positives_without_a_time_cannot_be_placed_and_are_counted():
    pairs, stats = trn.build_label_pairs([{"student_id": "a", "event_id": "e", "status": "registered"}], [], [])
    assert pairs == [] and stats["positive_without_time"] == 1


# ------------------------------------------------------------------ the training script: features and the whole run
def test_features_come_from_the_engine_result_with_semantic_preferred():
    result = {"signals_used": ["content", "semantic", "lexical"], "content_score": 40, "semantic_score": 70, "lexical_score": 20, "hybrid_score": 55}
    assert trn.features_from_result(result) == ("semantic", {"content": 40.0, "text_match": 70.0}, 55.0)
    result["signals_used"] = ["content", "lexical"]
    assert trn.features_from_result(result)[0] == "lexical"
    assert trn.features_from_result({"signals_used": ["content"], "content_score": 1, "hybrid_score": 1}) is None


def test_the_hybrid_baseline_is_computed_without_feedback():
    seen = {}

    class SpyEngine:
        def score_event(self, student, event, feedback, timetable):
            seen["feedback"], seen["timetable"] = feedback, timetable
            return {"signals_used": ["content", "lexical"], "content_score": 1, "lexical_score": 2, "hybrid_score": 3}

    pairs, stats = trn.attach_features([{"student_id": "s", "event_id": "e", "label": 1, "ts": T0}],
                                       {"s": {"student_id": "s"}}, {"e": {"_id": "e"}}, SpyEngine())
    assert len(pairs) == 1 and seen == {"feedback": {}, "timetable": None}


def test_pairs_with_missing_profiles_events_or_text_signal_are_skipped_and_counted():
    class Engine:
        def score_event(self, student, event, feedback, timetable):
            if event.get("boom"):
                raise RuntimeError("x")
            used = [] if event.get("notext") else ["content", "lexical"]
            return {"signals_used": used, "content_score": 1, "lexical_score": 2, "hybrid_score": 3}

    base = {"label": 1, "ts": T0}
    pairs = [{"student_id": s, "event_id": e, **base} for s, e in
             [("s", "ok"), ("nobody", "ok"), ("s", "gone"), ("s", "boom"), ("s", "notext")]]
    out, stats = trn.attach_features(pairs, {"s": {}}, {"ok": {}, "boom": {"boom": 1}, "notext": {"notext": 1}}, Engine())
    assert len(out) == 1
    assert stats == {"skipped_no_profile": 1, "skipped_event_missing": 1, "skipped_engine_error": 1, "skipped_no_text_signal": 1}


def test_only_one_kind_of_text_signal_is_used_for_training():
    pairs = [{"text_signal": "lexical"}] * 3 + [{"text_signal": "semantic"}] * 1
    signal, kept, dropped = trn.majority_signal(pairs)
    assert signal == "lexical" and len(kept) == 3 and dropped == 1
    assert trn.majority_signal([]) == (None, [], 0)


class FakeCollection:
    def __init__(self, docs):
        self.docs = docs

    def find(self, query=None, projection=None):
        fields = [k for k, v in (projection or {}).items() if v]
        return [{k: d[k] for k in (fields or d) if k in d} if fields else dict(d) for d in self.docs]


class FakeDB:
    def __init__(self, **collections):
        self.collections = {k: FakeCollection(v) for k, v in collections.items()}

    def __getitem__(self, name):
        return self.collections.setdefault(name, FakeCollection([]))

    def __getattr__(self, name):
        return self[name]


class FakeEngine:
    """Reads the numbers hidden in the fake events, so the test controls what the 'engine' sees."""
    def score_event(self, student, event, feedback, timetable):
        assert feedback == {}                                   # the baseline must never see feedback
        return {"signals_used": ["content", "lexical"], "content_score": event["_c"], "lexical_score": event["_t"],
                "hybrid_score": 0.3 * event["_c"] + 0.7 * event["_n"]}


def fake_world(n_pairs, predictive=True, seed=5):
    rng = random.Random(seed)
    students = [{"student_id": f"s{i}", "interests": ["x"], "skills": ["y"]} for i in range(25)]
    events, regs, impressions = [], [], []
    for i in range(n_pairs):
        event_id = f"e{i}"
        c, t, noise = rng.uniform(0, 100), rng.uniform(0, 100), rng.uniform(0, 100)
        events.append({"_id": event_id, "_c": c, "_t": t, "_n": noise})
        student = f"s{i % 25}"
        p = 1 / (1 + math.exp(-(t - 50) / 12)) if predictive else 0.5
        if rng.random() < p:
            regs.append({"student_id": student, "event_id": event_id, "status": "registered", "registered_at": T0 + timedelta(minutes=i)})
        else:
            impressions.append({"student_id": student, "event_id": event_id, "action": "impression",
                                "source": "dashboard_recommended", "timestamp": T0 + timedelta(minutes=i)})
    return FakeDB(profiles=students, events=events, event_registrations=regs, attendance=[], interactions=impressions,
                  not_interested_events=[])


def test_the_whole_script_trains_judges_and_saves_when_there_is_enough_data(capsys):
    with tempfile.TemporaryDirectory() as folder:
        code = trn.run(fake_world(1500), FakeEngine(), folder, seed=7, verbose=False)
        assert code == 0
        report = json.load(open(os.path.join(folder, tr.REPORT_FILE), encoding="utf-8"))
        assert report["quality_gate"]["passed"] is True and report["data_origin"] == "real"
        assert report["known_limits"] and report["label_statistics"]["positives"] > 0
        assert "QUALITY GATE: PASSED" in capsys.readouterr().out
        assert tr.load_ranker(folder)[0] is not None


def test_the_whole_script_refuses_small_data_and_writes_nothing(capsys):
    with tempfile.TemporaryDirectory() as folder:
        target = os.path.join(folder, "ranker")
        assert trn.run(fake_world(60), FakeEngine(), target, verbose=False) == 1
        assert not os.path.exists(target)
        assert "Not training" in capsys.readouterr().out


def test_the_script_never_writes_to_the_database():
    class ReadOnlyCollection(FakeCollection):
        def insert_one(self, *a, **k): raise AssertionError("write attempted")
        insert_many = update_one = update_many = delete_one = delete_many = replace_one = insert_one

    world = fake_world(60)
    world.collections = {k: ReadOnlyCollection(v.docs) for k, v in world.collections.items()}
    with tempfile.TemporaryDirectory() as folder:
        trn.run(world, FakeEngine(), os.path.join(folder, "r"), verbose=False)       # must not raise