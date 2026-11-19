"""
Phase D tests, part 2: loading and scoring with a real checkpoint.
Needs PyTorch (skipped automatically if it is not installed). No database.

Run from the backend folder:
    python -m pytest tests/test_phase_d_model.py -q
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

# make sure the backend folder (the parent of tests/) is importable however pytest was started
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

torch = pytest.importorskip("torch")

import bert4rec_model as bm  # noqa: E402
import build_sequences as bs  # noqa: E402
import sequential_recommender as sr  # noqa: E402
import simulate_interactions as sim  # noqa: E402
import train_bert4rec as tb  # noqa: E402

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
GOOD = {"trained_on_simulated": False, "quality_gate_passed": True, "quality_gate_reasons": [], "trained_at": "x"}


def make_vocab(max_len=8):
    specials = {"[PAD]": 0, "[MASK]": 1, "[UNK]": 2}
    return {
        "special_tokens": dict(specials),
        "actions": {**specials, **{a: 3 + i for i, a in enumerate(bs.DEFAULT_ACTIONS)}},
        "categories": {**specials, "technical": 3, "cultural": 4, "sports": 5},
        "organizers": {**specials, "ai club": 3, "music club": 4},
        "max_len": max_len,
    }


def write_model(folder, meta, seed=0):
    """An UNTRAINED tiny model saved exactly like train_bert4rec.py saves one."""
    os.makedirs(folder, exist_ok=True)
    vocab = make_vocab()
    torch.manual_seed(seed)
    cfg = bm.Bert4RecConfig(n_actions=len(vocab["actions"]), n_categories=len(vocab["categories"]),
                            n_organizers=len(vocab["organizers"]), max_len=vocab["max_len"],
                            hidden=16, layers=1, heads=2, ff=32, dropout=0.1)
    bm.save_checkpoint(os.path.join(folder, "model.pt"), bm.Bert4Rec(cfg), meta)
    with open(os.path.join(folder, "vocab.json"), "w", encoding="utf-8") as handle:
        json.dump(vocab, handle)
    return str(folder)


def history(categories, start=0):
    return [{"event_id": f"h{i}", "action": "view", "timestamp": T0 + timedelta(minutes=start + i),
             "category": c, "organizer": "AI Club"} for i, c in enumerate(categories)]


CANDIDATES = [
    {"_id": "a", "category": "Technical", "organizer": "AI Club"},
    {"_id": "b", "category": "Cultural", "organizer": "Music Club"},
    {"_id": "c", "category": "Sports", "organizer": "AI Club"},
]
RECENT = history(["Technical", "Technical", "Cultural", "Technical"])


# ------------------------------------------------------------------ scoring
def test_scores_are_probabilities_for_known_candidates(tmp_path):
    rec = sr.SequentialRecommender(model_dir=write_model(tmp_path, GOOD))
    scores = rec.score_events(RECENT, CANDIDATES)
    assert set(scores) == {"a", "b", "c"}
    assert all(0.0 < s <= 1.0 for s in scores.values())
    assert rec.status()["available"] is True and rec.status()["reason"] == "ok"


def test_a_candidates_score_does_not_depend_on_the_other_candidates(tmp_path):
    rec = sr.SequentialRecommender(model_dir=write_model(tmp_path, GOOD))
    alone = rec.score_events(RECENT, [CANDIDATES[0]])
    together = rec.score_events(RECENT, CANDIDATES)
    assert alone["a"] == together["a"]
    assert rec.score_events(RECENT, CANDIDATES) == together              # deterministic


def test_the_students_history_changes_the_scores(tmp_path):
    rec = sr.SequentialRecommender(model_dir=write_model(tmp_path, GOOD))
    one = rec.score_events(history(["Technical"] * 5), CANDIDATES)
    two = rec.score_events(history(["Sports"] * 5), CANDIDATES)
    assert one != two


def test_category_text_is_normalised_like_in_training(tmp_path):
    rec = sr.SequentialRecommender(model_dir=write_model(tmp_path, GOOD))
    plain = rec.score_events(RECENT, [{"_id": "x", "category": "technical", "organizer": "ai club"}])
    messy = rec.score_events(RECENT, [{"_id": "x", "category": "  TECHNICAL ", "organizer": "AI   Club"}])
    assert plain == messy and plain


def test_unseen_category_or_club_is_scored_through_the_known_part_only(tmp_path):
    rec = sr.SequentialRecommender(model_dir=write_model(tmp_path, GOOD))
    scores = rec.score_events(RECENT, [
        {"_id": "both", "category": "Technical", "organizer": "AI Club"},
        {"_id": "club_only", "category": "Chess", "organizer": "AI Club"},
        {"_id": "club_only_too", "category": "Origami", "organizer": "AI Club"},
        {"_id": "cat_only", "category": "Technical", "organizer": "Chess Club"},
        {"_id": "neither", "category": "Chess", "organizer": "Chess Club"},
    ])
    assert "neither" not in scores                                       # nothing known: left out
    assert scores["club_only"] == scores["club_only_too"]                # depends on the club alone
    assert {"both", "club_only", "cat_only"} <= set(scores)


def test_garbage_candidates_are_skipped_but_valid_ones_still_scored(tmp_path):
    rec = sr.SequentialRecommender(model_dir=write_model(tmp_path, GOOD))
    scores = rec.score_events(RECENT, [None, 7, {"x": 1}, CANDIDATES[0], "text"])
    assert set(scores) == {"a"}


def test_too_little_or_only_simulated_history_gives_nothing(tmp_path):
    rec = sr.SequentialRecommender(model_dir=write_model(tmp_path, GOOD))
    assert rec.score_events(history(["Technical", "Cultural"]), CANDIDATES) == {}
    simulated = [{**r, "simulated": True} for r in RECENT]
    assert rec.score_events(simulated, CANDIDATES) == {}
    assert rec.score_events(RECENT, []) == {}


def test_the_model_is_read_from_disk_once(tmp_path):
    rec = sr.SequentialRecommender(model_dir=write_model(tmp_path, GOOD))
    for _ in range(4):
        rec.score_events(RECENT, CANDIDATES)
    assert rec.load_count == 1


# ------------------------------------------------------------------ the safety rules, with real checkpoints
def test_blocked_models_return_nothing_and_say_why(tmp_path):
    cases = {
        "failed_gate": ({**GOOD, "quality_gate_passed": False, "quality_gate_reasons": ["too weak"]}, "quality gate"),
        "simulated": ({**GOOD, "trained_on_simulated": True}, "simulated"),
        "unknown_origin": ({**GOOD, "trained_on_simulated": None}, "simulated"),
        "old_checkpoint": ({"trained_on_simulated": False}, "no quality-gate record"),
    }
    for name, (meta, expected) in cases.items():
        rec = sr.SequentialRecommender(model_dir=write_model(tmp_path / name, meta))
        assert rec.score_events(RECENT, CANDIDATES) == {}, name
        status = rec.status()
        assert status["available"] is False and expected in status["reason"], name


def test_explicit_overrides_allow_a_blocked_model_and_are_reported(tmp_path):
    meta = {**GOOD, "quality_gate_passed": False, "trained_on_simulated": True}
    folder = write_model(tmp_path, meta)

    only_gate = sr.SequentialRecommender(model_dir=folder, require_gate=False)
    assert only_gate.score_events(RECENT, CANDIDATES) == {}                 # simulated still blocks

    both = sr.SequentialRecommender(model_dir=folder, require_gate=False, allow_simulated=True)
    assert set(both.score_events(RECENT, CANDIDATES)) == {"a", "b", "c"}
    status = both.status()
    assert status["gate_override_active"] is True and status["simulated_override_active"] is True


def test_environment_override_works_without_arguments(tmp_path, monkeypatch):
    folder = write_model(tmp_path, {**GOOD, "trained_on_simulated": True, "quality_gate_passed": False})
    assert sr.SequentialRecommender(model_dir=folder).score_events(RECENT, CANDIDATES) == {}
    monkeypatch.setenv("SEQUENTIAL_ALLOW_UNGATED", "true")
    monkeypatch.setenv("SEQUENTIAL_ALLOW_SIMULATED", "true")
    assert sr.SequentialRecommender(model_dir=folder).score_events(RECENT, CANDIDATES)


def test_disable_switch_beats_a_good_model(tmp_path, monkeypatch):
    folder = write_model(tmp_path, GOOD)
    monkeypatch.setenv("DISABLE_SEQUENTIAL_MODEL", "true")
    rec = sr.SequentialRecommender(model_dir=folder)
    assert rec.score_events(RECENT, CANDIDATES) == {} and "disabled" in rec.status()["reason"]


def test_reload_picks_up_a_model_that_appears_later(tmp_path):
    folder = tmp_path / "later"
    rec = sr.SequentialRecommender(model_dir=str(folder))
    assert rec.score_events(RECENT, CANDIDATES) == {}                       # nothing there yet
    write_model(folder, GOOD)
    assert rec.score_events(RECENT, CANDIDATES) == {}                       # still remembers the failed attempt
    rec.reload()
    assert set(rec.score_events(RECENT, CANDIDATES)) == {"a", "b", "c"}


def test_a_real_checkpoint_from_the_training_script_is_accepted_by_the_loader(tmp_path):
    """The checkpoint written by train_bert4rec.run() loads through the same path (safe loader included)."""
    events = [{"_id": f"ev{i}", "category": c, "organizer": o, "skills": s, "date": "2026-12-01", "title": title}
              for i, (c, o, s, title) in enumerate([
                  ("Technical", "AI Club", ["Python", "ML"], "Machine Learning Bootcamp"),
                  ("Technical", "AI Club", ["Python"], "AI Hackathon"),
                  ("Technical", "Cyber Club", ["Security"], "Cybersecurity Challenge"),
                  ("Cultural", "Music Club", ["Singing"], "Music Night"),
                  ("Cultural", "Drama Club", ["Acting"], "Drama Fest"),
                  ("Sports", "Sports Club", ["Fitness"], "Football Cup"),
                  ("Career", "Placement Cell", ["Resume"], "Resume Workshop"),
                  ("Technical", "Web Club", ["JavaScript"], "Web Development Workshop"),
              ])]
    profiles = [{"interests": ["machine learning", "cybersecurity"], "skills": ["Python", "Security"],
                 "preferred_domains": ["Technical"], "career_goal": "AI engineer"},
                {"interests": ["music", "drama"], "skills": ["Singing"], "preferred_domains": ["Cultural"],
                 "career_goal": ""}]
    rows = sim.simulate(profiles, events, copies=25, seed=8, now=datetime(2026, 11, 1, tzinfo=timezone.utc))
    data_dir, out_dir = tmp_path / "data", tmp_path / "out"
    bs.write_outputs(str(data_dir), bs.build_datasets(rows), "simulated")
    assert tb.run(str(data_dir), str(out_dir), cfg_overrides={"hidden": 16, "layers": 1, "heads": 2},
                  min_train=5, verbose=False, epochs=2, patience=2, batch_size=16, seed=1) == 0

    rec = sr.SequentialRecommender(model_dir=str(out_dir))                  # default rules: must be blocked
    assert rec.score_events(RECENT, CANDIDATES) == {}
    assert "simulated" in rec.status()["reason"] or "quality gate" in rec.status()["reason"]

    allowed = sr.SequentialRecommender(model_dir=str(out_dir), require_gate=False, allow_simulated=True)
    assert allowed.score_events(RECENT, CANDIDATES)                          # and it really loads and scores