"""
Phase D tests, part 1: everything that needs NO PyTorch.
(History preparation, candidate lookup, the safety rules, and the "returns
nothing, never raises" paths.)

Run from the backend folder:
    python -m pytest tests/test_phase_d_pure.py -q
"""
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

# make sure the backend folder (the parent of tests/) is importable however pytest was started
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bert4rec_data as bd
import build_sequences as bs
import sequential_recommender as sr

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def make_vocab(max_len=8):
    specials = {"[PAD]": 0, "[MASK]": 1, "[UNK]": 2}
    return {
        "special_tokens": dict(specials),
        "actions": {**specials, **{a: 3 + i for i, a in enumerate(bs.DEFAULT_ACTIONS)}},
        "categories": {**specials, "technical": 3, "cultural": 4, "sports": 5},
        "organizers": {**specials, "ai club": 3, "music club": 4},
        "max_len": max_len,
    }


def row(minutes, action="view", category="Technical", organizer="AI Club", event="e1", **extra):
    return {"event_id": event, "action": action, "timestamp": T0 + timedelta(minutes=minutes),
            "category": category, "organizer": organizer, **extra}


class env:
    """Set environment variables for a block, then restore them."""
    def __init__(self, **values):
        self.values, self.saved = values, {}

    def __enter__(self):
        for key, value in self.values.items():
            self.saved[key] = os.environ.get(key)
            os.environ[key] = value

    def __exit__(self, *exc):
        for key, old in self.saved.items():
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old


# ------------------------------------------------------------------ history -> model input
def test_history_is_sorted_normalised_and_ends_with_the_question_slot():
    vocab = make_vocab()
    rows = [row(30, "register", "Sports", "AI Club", "c"), row(10, "view", "  TECHNICAL ", "ai club", "a"),
            row(20, "save", "Cultural", "Music Club", "b")]
    ctx, why = sr.history_context(rows, vocab)
    assert why == "ok"
    assert ctx["cat"][-4:] == [3, 4, 5, bd.MASK_ID]                     # technical, cultural, sports, ?
    assert ctx["org"][-4:] == [3, 4, 3, bd.MASK_ID]
    assert ctx["act"][-4:] == [vocab["actions"]["view"], vocab["actions"]["save"],
                               vocab["actions"]["register"], vocab["actions"]["register"]]   # last = asked action
    assert len(ctx["cat"]) == 8 and ctx["cat"][0] == 0                   # left-padded


def test_next_action_can_be_chosen():
    vocab = make_vocab()
    ctx, _ = sr.history_context([row(i) for i in range(3)], vocab, next_action="attend")
    assert ctx["act"][-1] == vocab["actions"]["attend"]


def test_too_little_history_gives_nothing_with_a_reason():
    vocab = make_vocab()
    ctx, why = sr.history_context([row(1), row(2)], vocab)
    assert ctx is None and "only 2" in why
    assert sr.history_context([], vocab)[0] is None
    assert sr.history_context(None, vocab)[0] is None


def test_simulated_rows_and_unwanted_actions_do_not_count():
    vocab = make_vocab()
    rows = [row(1, simulated=True), row(2, simulated=True), row(3, "impression"), row(4), row(5)]
    ctx, why = sr.history_context(rows, vocab)
    assert ctx is None and "only 2" in why                               # just the two real, wanted rows
    assert sr.history_context(rows + [row(6)], vocab)[0] is not None


def test_only_the_newest_interactions_that_fit_are_kept():
    vocab = make_vocab(max_len=5)
    rows = [row(i, category=["Technical", "Cultural", "Sports"][i % 3]) for i in range(12)]
    ctx, _ = sr.history_context(rows, vocab)
    assert len(ctx["cat"]) == 5 and ctx["cat"][-1] == bd.MASK_ID
    expected = [vocab["categories"][["technical", "cultural", "sports"][i % 3]] for i in range(8, 12)]
    assert ctx["cat"][:4] == expected                                    # the newest four


def test_unseen_text_becomes_unk_and_garbage_rows_are_ignored():
    vocab = make_vocab()
    rows = [row(1, category="Chess"), row(2), row(3), None, 5, "x", {"action": "view"}]
    ctx, _ = sr.history_context(rows, vocab)
    assert ctx is not None and bd.UNK_ID in ctx["cat"]


# ------------------------------------------------------------------ candidates
def test_candidate_lookup_and_normalisation():
    vocab = make_vocab()
    assert sr.candidate_key({"_id": 7, "category": " Technical ", "organizer": "AI CLUB"}, vocab) == ("7", 3, 3)
    assert sr.candidate_key({"id": "x", "category": ["Cultural", "Sports"], "organizer": "Music Club"}, vocab) == ("x", 4, 4)
    assert sr.candidate_key({"id": "y", "category": "Chess", "organizer": "Chess Club"}, vocab) == ("y", 2, 2)
    for bad in (None, 5, "text", {"category": "Technical"}):
        assert sr.candidate_key(bad, vocab) is None


# ------------------------------------------------------------------ safety rules
GOOD = {"trained_on_simulated": False, "quality_gate_passed": True, "quality_gate_reasons": []}


def test_a_model_is_allowed_only_when_the_gate_passed_and_it_was_not_trained_on_simulated_data():
    assert sr.check_model_allowed(GOOD) == (True, "ok")

    ok, why = sr.check_model_allowed({**GOOD, "quality_gate_passed": False, "quality_gate_reasons": ["too weak"]})
    assert not ok and "failed the quality gate" in why and "too weak" in why

    ok, why = sr.check_model_allowed({"trained_on_simulated": False})
    assert not ok and "no quality-gate record" in why

    for flag in (True, None):
        ok, why = sr.check_model_allowed({**GOOD, "trained_on_simulated": flag})
        assert not ok and "simulated" in why


def test_overrides_are_explicit_and_independent():
    failed = {**GOOD, "quality_gate_passed": False}
    assert sr.check_model_allowed(failed, require_gate=False)[0] is True
    simulated = {**GOOD, "trained_on_simulated": True}
    assert sr.check_model_allowed(simulated, allow_simulated=True)[0] is True
    both = {"trained_on_simulated": True, "quality_gate_passed": False}
    assert sr.check_model_allowed(both, require_gate=False)[0] is False         # simulated still blocked
    assert sr.check_model_allowed(both, allow_simulated=True)[0] is False       # gate still blocks
    assert sr.check_model_allowed(both, require_gate=False, allow_simulated=True)[0] is True


# ------------------------------------------------------------------ the recommender never breaks anything
def test_missing_model_means_no_scores_and_a_clear_reason():
    with tempfile.TemporaryDirectory() as folder:
        rec = sr.SequentialRecommender(model_dir=os.path.join(folder, "nope"))
        assert rec.score_events([row(i) for i in range(5)], [{"_id": "e", "category": "Technical"}]) == {}
        status = rec.status()
        assert status["available"] is False and "not found" in status["reason"]
        assert rec.available is False


def test_it_loads_at_most_once_until_asked_to_reload():
    with tempfile.TemporaryDirectory() as folder:
        rec = sr.SequentialRecommender(model_dir=folder)
        for _ in range(5):
            rec.score_events([row(i) for i in range(5)], [{"_id": "e"}])
            rec.status()
        assert rec.load_count == 1
        rec.reload()
        rec.status()
        assert rec.load_count == 2


def test_the_disable_switch_wins_even_when_files_exist():
    with tempfile.TemporaryDirectory() as folder:
        for name in ("model.pt", "vocab.json"):
            open(os.path.join(folder, name), "w").close()
        with env(DISABLE_SEQUENTIAL_MODEL="true"):
            rec = sr.SequentialRecommender(model_dir=folder)
            assert rec.score_events([row(i) for i in range(5)], [{"_id": "e"}]) == {}
            assert "disabled" in rec.status()["reason"]


def test_unreadable_model_files_are_handled_not_raised():
    with tempfile.TemporaryDirectory() as folder:
        for name in ("model.pt", "vocab.json"):
            with open(os.path.join(folder, name), "w") as handle:
                handle.write("not a real file")
        rec = sr.SequentialRecommender(model_dir=folder)
        assert rec.score_events([row(i) for i in range(5)], [{"_id": "e"}]) == {}
        reason = rec.status()["reason"]
        assert "PyTorch could not be imported" in reason or "could not load the model" in reason


def test_garbage_input_never_raises():
    with tempfile.TemporaryDirectory() as folder:
        rec = sr.SequentialRecommender(model_dir=folder)
        for history, candidates in ((None, None), ([None, 5, "x"], [None, 7, {"x": 1}]), ([], []), ("abc", 3)):
            assert rec.score_events(history, candidates) == {}


def test_environment_switches_are_read_at_creation():
    with tempfile.TemporaryDirectory() as folder:
        with env(SEQUENTIAL_ALLOW_UNGATED="1", SEQUENTIAL_ALLOW_SIMULATED="yes", SEQUENTIAL_MODEL_DIR=folder):
            rec = sr.SequentialRecommender()
            assert rec.require_gate is False and rec.allow_simulated is True and rec.model_dir == folder
        rec = sr.SequentialRecommender()
        assert rec.require_gate is True and rec.allow_simulated is False


def test_the_shared_instance_is_created_once():
    assert sr.get_recommender() is sr.get_recommender()
    assert isinstance(sr.status(), dict) and isinstance(sr.score_events([], []), dict)