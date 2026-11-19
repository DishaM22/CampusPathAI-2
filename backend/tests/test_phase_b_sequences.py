"""
Phase B tests, part 1: pure logic. No database, no network, no ML.

Run from the backend folder:
    python -m pytest tests/test_phase_b_sequences.py -q
"""
import random
from collections import Counter
from datetime import datetime, timedelta, timezone

import build_sequences as bs
import simulate_interactions as sim

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
NOW = datetime(2026, 11, 1, tzinfo=timezone.utc)


def row(student, minutes, action="view", category="Technical", organizer="AI Club", event="e1", simulated=False):
    return {
        "student_id": student, "event_id": event, "action": action,
        "timestamp": T0 + timedelta(minutes=minutes), "category": category,
        "organizer": organizer, "simulated": simulated,
    }


def many_rows(students=6, per_student=12, simulated=False):
    rows = []
    for s in range(students):
        for n in range(per_student):
            action = ["view", "save", "register", "attend"][n % 4]
            rows.append(row(f"s{s}", n * 60 + s, action, category=["Technical", "Cultural", "Sports"][n % 3],
                            organizer=["AI Club", "Music Club"][n % 2], event=f"e{n}", simulated=simulated))
    return rows


EVENTS = [
    {"_id": f"ev{i}", "category": cat, "organizer": org, "skills": skills, "date": "2026-12-01", "title": title}
    for i, (cat, org, skills, title) in enumerate([
        ("Technical", "AI Club", ["Python", "ML"], "Machine Learning Bootcamp"),
        ("Technical", "AI Club", ["Python"], "AI Hackathon"),
        ("Technical", "Cyber Club", ["Security"], "Cybersecurity Challenge"),
        ("Cultural", "Music Club", ["Singing"], "Music Night"),
        ("Cultural", "Drama Club", ["Acting"], "Drama Fest"),
        ("Sports", "Sports Club", ["Fitness"], "Football Cup"),
        ("Career", "Placement Cell", ["Resume"], "Resume Workshop"),
        ("Technical", "Web Club", ["JavaScript"], "Web Development Workshop"),
    ])
]
PROFILES = [
    {"interests": ["machine learning", "cybersecurity"], "skills": ["Python", "Security"], "preferred_domains": ["Technical"], "career_goal": "AI engineer"},
    {"interests": ["music", "drama"], "skills": ["Singing"], "preferred_domains": ["Cultural"], "career_goal": ""},
]


# ---------------------------------------------------------------- tokens
def test_token_text_is_normalised():
    assert bs.norm_text("  Technical   EVENTS ") == "technical events"
    assert bs.norm_text(None) == "unknown"
    assert bs.norm_text("") == "unknown"
    assert bs.make_token("attend", "technical", "ai club") == "attend:technical@ai club"


def test_impressions_are_left_out_by_default_and_can_be_included():
    rows = [row("s", 1, "view"), row("s", 2, "impression"), row("s", 3, "save")]
    by_student, stats = bs.prepare_items(rows, bs.DEFAULT_ACTIONS)
    assert [i["action"] for i in by_student["s"]] == ["view", "save"]
    assert stats["filtered"] == 1
    by_student, _ = bs.prepare_items(rows, list(bs.DEFAULT_ACTIONS) + ["impression"])
    assert len(by_student["s"]) == 3


def test_rows_are_time_ordered_and_invalid_rows_skipped():
    rows = [row("s", 30, "save"), row("s", 10, "view"), row("s", 20, "register"),
            {"student_id": "s", "action": "view", "timestamp": None},
            {"student_id": "", "action": "view", "timestamp": T0}]
    by_student, stats = bs.prepare_items(rows, bs.DEFAULT_ACTIONS)
    assert [i["action"] for i in by_student["s"]] == ["view", "register", "save"]
    assert stats["invalid"] == 2


def test_naive_and_text_timestamps_become_utc():
    assert bs.as_utc(datetime(2026, 1, 1)).tzinfo is not None
    assert bs.as_utc("2026-01-01T10:00:00Z") == datetime(2026, 1, 1, 10, tzinfo=timezone.utc)
    assert bs.as_utc("garbage") is None


# ---------------------------------------------------------------- split
def test_temporal_split_has_no_leakage():
    data = bs.build_datasets(many_rows(), max_len=30, min_len=3)
    cut = data["report"]["cutoffs"]
    t1 = datetime.fromisoformat(cut["train_end"])
    t2 = datetime.fromisoformat(cut["val_end"])
    assert data["train"] and data["val"] and data["test"]

    for rec in data["train"]:
        assert all(datetime.fromisoformat(t) < t1 for t in rec["sequence"]["timestamps"])
    for rec in data["val"]:
        assert all(datetime.fromisoformat(t) < t1 for t in rec["history"]["timestamps"])
        assert all(t1 <= datetime.fromisoformat(t) < t2 for t in rec["targets"]["timestamps"])
    for rec in data["test"]:
        assert all(datetime.fromisoformat(t) < t2 for t in rec["history"]["timestamps"])
        assert all(datetime.fromisoformat(t) >= t2 for t in rec["targets"]["timestamps"])
        assert max(rec["history"]["timestamps"]) < min(rec["targets"]["timestamps"])


def test_explicit_cutoffs_override_fractions():
    rows = many_rows(students=2, per_student=10)
    data = bs.build_datasets(rows, train_end=T0 + timedelta(minutes=300), val_end=T0 + timedelta(minutes=450))
    assert data["report"]["cutoffs"]["train_end"] == (T0 + timedelta(minutes=300)).isoformat()


def test_max_len_keeps_the_most_recent_history_and_windows_cover_all_training_items():
    rows = [row("s", n, "view", event=f"e{n}") for n in range(100)]
    data = bs.build_datasets(rows, max_len=10, min_len=3, train_frac=0.8, val_frac=0.1)
    assert all(len(r["sequence"]["actions"]) <= 10 for r in data["train"])
    assert all(len(r["history"]["actions"]) <= 10 for r in data["val"] + data["test"])
    covered = {e for r in data["train"] for e in r["sequence"]["event_ids"]}
    train_part = {f"e{n}" for n in range(80)}
    assert covered == train_part                        # nothing dropped
    # the validation history is the LAST items before the cutoff
    assert data["val"][0]["history"]["event_ids"][-1] == "e79"


def test_window_items_edge_cases():
    items = [{"i": n} for n in range(7)]
    assert bs.window_items(items, 10, 5) == [items]
    windows = bs.window_items(items, 4, 2)
    assert windows[-1][-1]["i"] == 6 and windows[0][0]["i"] == 0
    assert all(len(w) <= 4 for w in windows)
    assert bs.window_items([], 4, 2) == []


def test_students_with_too_few_interactions_are_left_out():
    rows = many_rows(students=3, per_student=12) + [row("tiny", 5), row("tiny", 6)]
    data = bs.build_datasets(rows, min_len=3)
    ids = {r["student_id"] for r in data["train"] + data["val"] + data["test"]}
    assert "tiny" not in ids


def test_bad_settings_are_rejected():
    for kwargs in ({"max_len": 1}, {"min_len": 0}, {"train_frac": 0.9, "val_frac": 0.2}, {"train_frac": 0}):
        try:
            bs.build_datasets([], **kwargs)
        except ValueError:
            continue
        raise AssertionError(f"{kwargs} should have been rejected")


def test_empty_input_gives_empty_datasets_and_an_honest_report():
    data = bs.build_datasets([])
    assert data["train"] == [] and data["val"] == [] and data["test"] == []
    assert data["report"]["students"]["with_interactions"] == 0
    assert data["report"]["contains_simulated_data"] is False


# ---------------------------------------------------------------- vocab
def test_vocab_uses_training_data_only_and_fixed_special_ids():
    rows = []
    for n in range(18):                                 # early: only "technical" / "ai club"
        rows.append(row("s", n, "view", category="Technical", organizer="AI Club"))
    for n in range(18, 30):                             # from the split on: a brand-new category and club
        rows.append(row("s", n, "view", category="Chess", organizer="Chess Club"))
    data = bs.build_datasets(rows, train_frac=0.6, val_frac=0.2)
    vocab = data["vocab"]
    assert vocab["special_tokens"] == {"[PAD]": 0, "[MASK]": 1, "[UNK]": 2}
    assert "technical" in vocab["categories"] and "chess" not in vocab["categories"]
    assert "chess club" not in vocab["organizers"]
    assert data["report"]["unseen_in_evaluation_targets"]["categories_not_in_train_vocab"] == 1.0


def test_action_ids_do_not_depend_on_the_data():
    a = bs.build_datasets([row("s", n, "view") for n in range(20)])["vocab"]["actions"]
    b = bs.build_datasets(many_rows())["vocab"]["actions"]
    assert a == b and list(a)[3:] == list(bs.DEFAULT_ACTIONS)


def test_unseen_values_encode_to_unk():
    rows = [row("s", n, "view", category="Technical", organizer="AI Club") for n in range(20)]
    vocab = bs.build_datasets(rows)["vocab"]
    packed = {"actions": ["view", "dance"], "categories": ["technical", "origami"], "organizers": ["ai club", "x"]}
    enc = bs.encode_sequence(packed, vocab)
    assert enc["actions"][1] == enc["categories"][1] == enc["organizers"][1] == bs.SPECIAL_TOKENS["[UNK]"]
    assert enc["categories"][0] >= bs.FIRST_FREE_ID


# ---------------------------------------------------------------- simulated flag in reports
def test_simulated_sequences_are_flagged_and_reported_loudly():
    data = bs.build_datasets(many_rows(simulated=True))
    assert data["report"]["contains_simulated_data"] is True
    assert all(r["simulated"] for r in data["train"])
    assert "SIMULATED DATA WARNING" in bs.render_report_md(data["report"], "simulated")


def test_real_only_report_has_no_warning():
    data = bs.build_datasets(many_rows(simulated=False))
    assert data["report"]["contains_simulated_data"] is False
    assert "SIMULATED DATA WARNING" not in bs.render_report_md(data["report"], "real")


def test_load_rows_rejects_an_unknown_source():
    try:
        bs.load_rows(object(), "everything", bs.DEFAULT_ACTIONS)
    except ValueError:
        return
    raise AssertionError("unknown source should be rejected")


# ---------------------------------------------------------------- simulator
def test_simulator_is_reproducible_and_seed_sensitive():
    a = sim.simulate(PROFILES, EVENTS, copies=5, seed=1, now=NOW, run_id="r")
    b = sim.simulate(PROFILES, EVENTS, copies=5, seed=1, now=NOW, run_id="r")
    c = sim.simulate(PROFILES, EVENTS, copies=5, seed=2, now=NOW, run_id="r")
    assert a == b
    assert a != c


def test_simulated_rows_are_clearly_flagged_and_anonymous():
    rows = sim.simulate(PROFILES, EVENTS, copies=5, seed=3, now=NOW, run_id="run-x")
    assert rows
    for r in rows:
        assert r["simulated"] is True and r["source"] == "simulated" and r["sim_run_id"] == "run-x"
        assert r["student_id"].startswith("sim-p")
        assert r["action"] in bs.DEFAULT_ACTIONS and r["action"] != "impression"
        assert r["event_id"] in {e["_id"] for e in EVENTS}
        assert r["timestamp"] <= NOW
    assert len({r["student_id"] for r in rows}) == len(PROFILES) * 5


def test_simulated_times_increase_per_student_after_sorting_and_stay_inside_the_window():
    rows = sim.simulate(PROFILES, EVENTS, copies=8, days=60, seed=4, now=NOW)
    earliest = NOW - timedelta(days=60)
    assert all(r["timestamp"] >= earliest for r in rows)
    by_student = {}
    for r in rows:
        by_student.setdefault(r["student_id"], []).append(r["timestamp"])
    assert all(ts == sorted(ts) for ts in by_student.values())


def test_every_simulated_student_has_enough_interactions_for_a_sequence():
    rows = sim.simulate(PROFILES, EVENTS, copies=10, seed=5, now=NOW)
    counts = Counter(r["student_id"] for r in rows)
    assert min(counts.values()) >= sim.MIN_EVENTS


def test_actions_follow_a_sensible_order_per_event():
    rows = sim.simulate(PROFILES, EVENTS, copies=20, seed=6, now=NOW)
    first = {}
    for r in sorted(rows, key=lambda r: r["timestamp"]):
        first.setdefault((r["student_id"], r["event_id"], r["action"]), r["timestamp"])
    for (student, event, action), ts in first.items():
        if action in ("save", "register", "not_interested"):
            assert first[(student, event, "view")] <= ts          # always viewed first
        if action == "attend":
            assert first[(student, event, "register")] <= ts      # never attends without registering


def test_momentum_creates_the_planted_sequential_pattern():
    """Same-category-next rate is higher with momentum than without (a property of the simulator)."""
    def same_category_rate(strength):
        rows = sim.simulate(PROFILES, EVENTS, copies=150, seed=7, now=NOW, momentum=strength)
        by_student = {}
        for r in rows:
            if r["action"] == "view":
                by_student.setdefault(r["student_id"], []).append(r)
        same = total = 0
        for views in by_student.values():
            views.sort(key=lambda r: r["timestamp"])
            for a, b in zip(views, views[1:]):
                total += 1
                same += a["category"] == b["category"]
        return same / total
    assert same_category_rate(0.12) > same_category_rate(0.0)


def test_simulator_needs_enough_input():
    for profiles, events in ((PROFILES, EVENTS[:2]), ([], EVENTS), ([{"interests": []}], EVENTS)):
        try:
            sim.simulate(profiles, events, copies=2, now=NOW)
        except ValueError:
            continue
        raise AssertionError("should have refused")


def test_simulation_can_feed_the_sequence_builder_end_to_end():
    rows = sim.simulate(PROFILES, EVENTS, copies=25, seed=8, now=NOW)
    data = bs.build_datasets(rows)
    assert data["train"] and data["val"] and data["test"]
    assert data["report"]["students"]["real"] == 0
    assert data["report"]["contains_simulated_data"] is True


# ---------------------------------------------------------------- write guard (fake database)
class _FakeCollection:
    def __init__(self):
        self.inserted = []

    def insert_many(self, docs):
        self.inserted.extend(docs)

    def create_index(self, *args, **kwargs):
        return "ok"


class _FakeDB:
    def __init__(self):
        self.collections = {}

    def __getitem__(self, name):
        return self.collections.setdefault(name, _FakeCollection())


def test_simulated_rows_can_only_be_written_to_the_simulated_collection():
    rows = sim.simulate(PROFILES, EVENTS, copies=2, seed=9, now=NOW)
    db = _FakeDB()
    for forbidden in ("interactions", "students", "anything"):
        try:
            sim.write_rows(db, rows, collection=forbidden)
        except ValueError:
            continue
        raise AssertionError(f"writing to {forbidden} should be refused")
    assert "interactions" not in db.collections
    assert sim.write_rows(db, rows) == len(rows)
    assert len(db["interactions_simulated"].inserted) == len(rows)


def test_unflagged_rows_are_refused():
    db = _FakeDB()
    try:
        sim.write_rows(db, [{"student_id": "x", "simulated": False}])
    except ValueError:
        return
    raise AssertionError("unflagged rows must be refused")


def test_real_and_simulated_builds_use_different_default_folders():
    folders = {bs.default_out_dir(s) for s in ("real", "simulated", "both")}
    assert len(folders) == 3