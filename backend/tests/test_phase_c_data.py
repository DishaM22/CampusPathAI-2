"""
Phase C tests, part 1: data helpers. No PyTorch, no database.

Run from the backend folder:
    python -m pytest tests/test_phase_c_data.py -q
"""
from datetime import datetime, timezone

import build_sequences as bs
import bert4rec_data as bd
import simulate_interactions as sim

NOW = datetime(2026, 11, 1, tzinfo=timezone.utc)

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
    {"interests": ["machine learning", "cybersecurity"], "skills": ["Python", "Security"],
     "preferred_domains": ["Technical"], "career_goal": "AI engineer"},
    {"interests": ["music", "drama"], "skills": ["Singing"], "preferred_domains": ["Cultural"], "career_goal": ""},
]


def sim_datasets(copies=25, seed=8, max_len=30):
    rows = sim.simulate(PROFILES, EVENTS, copies=copies, seed=seed, now=NOW)
    return bs.build_datasets(rows, max_len=max_len)


# ---------------------------------------------------------------- padding / context
def test_special_ids_match_phase_b():
    assert (bd.PAD_ID, bd.MASK_ID, bd.UNK_ID) == (0, 1, 2)
    assert bd.POSITIVE_ACTIONS == ("save", "register", "attend")


def test_left_pad_keeps_the_newest_items():
    assert bd.left_pad([5, 6, 7], 5) == [0, 0, 5, 6, 7]
    assert bd.left_pad([1, 2, 3, 4, 5, 6], 4) == [3, 4, 5, 6]
    assert bd.left_pad([], 3) == [0, 0, 0]


def test_context_ends_with_the_question_slot():
    history = {"actions": [3, 4, 5], "categories": [10, 11, 12], "organizers": [20, 21, 22]}
    ctx = bd.build_context(history, next_action=7, max_len=6)
    assert ctx["cat"] == [0, 0, 10, 11, 12, bd.MASK_ID]
    assert ctx["org"] == [0, 0, 20, 21, 22, bd.MASK_ID]
    assert ctx["act"] == [0, 0, 3, 4, 5, 7]


def test_context_keeps_only_the_newest_history_that_fits():
    history = {"actions": list(range(3, 13)), "categories": list(range(30, 40)), "organizers": list(range(50, 60))}
    ctx = bd.build_context(history, next_action=4, max_len=5)
    assert len(ctx["cat"]) == len(ctx["org"]) == len(ctx["act"]) == 5
    assert ctx["cat"] == [36, 37, 38, 39, bd.MASK_ID]          # newest 4 + the question
    assert ctx["act"][-1] == 4


def test_action_id_lookup_and_unknown_action():
    vocab = {"actions": {"[PAD]": 0, "[MASK]": 1, "[UNK]": 2, "view": 3, "register": 4}}
    assert bd.action_id(vocab, "register") == 4
    assert bd.action_id(vocab, "teleport") == bd.UNK_ID


# ---------------------------------------------------------------- training examples
def test_training_examples_are_padded_and_have_no_mask_tokens():
    data = sim_datasets()
    examples = bd.build_train_examples(data["train"], data["vocab"], 30)
    assert len(examples) == len(data["train"]) > 0
    for e in examples:
        assert len(e["cat"]) == len(e["org"]) == len(e["act"]) == 30
        assert e["act"][-1] != bd.PAD_ID                          # newest item is real
        real = [a for a in e["act"] if a != bd.PAD_ID]
        assert len(real) >= 2
        assert bd.MASK_ID not in e["cat"] and bd.MASK_ID not in e["org"]
        assert bd.UNK_ID not in e["cat"]                          # vocabulary came from this data
        first_real = len(e["act"]) - len(real)
        assert all(a == bd.PAD_ID for a in e["act"][:first_real])   # padding only on the left


def test_group_ids_number_events_by_first_appearance():
    assert bd._group_ids(["a", "a", "b", "a", "c"]) == [1, 1, 2, 1, 3]
    assert bd._group_ids([]) == []


def test_training_examples_carry_event_groups_aligned_with_padding():
    data = sim_datasets()
    for e in bd.build_train_examples(data["train"], data["vocab"], 30):
        assert len(e["evt"]) == 30
        for group, action in zip(e["evt"], e["act"]):
            assert (group == 0) == (action == bd.PAD_ID)          # 0 only under padding
        real = [g for g in e["evt"] if g]
        assert min(real) == 1 and max(real) <= len(real)


def test_ids_stay_inside_the_vocabulary():
    data = sim_datasets()
    vocab = data["vocab"]
    for e in bd.build_train_examples(data["train"], vocab, 30):
        assert max(e["cat"]) < len(vocab["categories"])
        assert max(e["org"]) < len(vocab["organizers"])
        assert max(e["act"]) < len(vocab["actions"])


# ---------------------------------------------------------------- evaluation questions
def test_one_question_per_held_out_interaction_with_teacher_forcing():
    data = sim_datasets()
    vocab = data["vocab"]
    record = data["val"][0]
    examples = bd.build_eval_examples([record], vocab, 30)
    n_targets = len(record["targets"]["actions"])
    assert len(examples) == n_targets >= 1

    enc_hist = bd.encode_packed(record["history"], vocab)
    enc_tgt = bd.encode_packed(record["targets"], vocab)
    for j, example in enumerate(examples):
        assert example["cat"][-1] == example["org"][-1] == bd.MASK_ID       # the question slot
        assert example["act"][-1] == enc_tgt["actions"][j]                   # asks about THIS action
        assert example["label_cat"] == enc_tgt["categories"][j]
        assert example["label_org"] == enc_tgt["organizers"][j]
        assert example["event_id"] == record["targets"]["event_ids"][j]
        if j >= 1:                                                           # earlier true targets are visible
            assert example["cat"][-2] == enc_tgt["categories"][j - 1]
        else:
            assert example["cat"][-2] == enc_hist["categories"][-1]


def _small_vocab():
    cats = {"[PAD]": 0, "[MASK]": 1, "[UNK]": 2, "technical": 3, "cultural": 4, "sports": 5, "career": 6}
    return {
        "special_tokens": {"[PAD]": 0, "[MASK]": 1, "[UNK]": 2},
        "actions": {"[PAD]": 0, "[MASK]": 1, "[UNK]": 2, "view": 3, "save": 4, "register": 5},
        "categories": cats,
        "organizers": {"[PAD]": 0, "[MASK]": 1, "[UNK]": 2, "ai club": 3, "music club": 4},
        "max_len": 8,
    }


def test_excluding_the_same_event_removes_its_earlier_steps_from_the_history():
    vocab = _small_vocab()
    record = {
        "student_id": "s",
        "history": {"actions": ["view", "view", "view"],
                    "categories": ["technical", "cultural", "sports"],
                    "organizers": ["ai club", "music club", "ai club"],
                    "event_ids": ["a", "b", "c"]},
        # first target: the student saves event c (already viewed); second: registers for a NEW event d
        "targets": {"actions": ["save", "register"], "categories": ["sports", "career"],
                    "organizers": ["ai club", "ai club"], "event_ids": ["c", "d"]},
    }
    plain = bd.build_eval_examples([record], vocab, 8)
    hidden = bd.build_eval_examples([record], vocab, 8, exclude_same_event=True)
    assert len(plain) == len(hidden) == 2
    assert all(e["same_event_hidden"] for e in hidden) and not any(e["same_event_hidden"] for e in plain)

    # question 0: with the event hidden, its own "view" (sports) is gone from the history
    sports = vocab["categories"]["sports"]
    assert plain[0]["cat"][-2] == sports                       # previous item is the same event: copying works
    assert hidden[0]["cat"][-2] == vocab["categories"]["cultural"]
    assert sports not in hidden[0]["cat"]
    assert hidden[0]["label_cat"] == sports                    # the label is unchanged

    # question 1: the event is genuinely new, so nothing is removed
    assert plain[1]["cat"] == hidden[1]["cat"]


def test_training_sequences_become_recommendation_questions():
    vocab = _small_vocab()
    record = {
        "student_id": "s",
        "simulated": True,
        "sequence": {
            "actions": ["view", "register", "view", "save", "register"],
            "categories": ["technical", "technical", "cultural", "cultural", "sports"],
            "organizers": ["ai club", "ai club", "music club", "music club", "ai club"],
            "event_ids": ["a", "a", "b", "b", "c"],
            "timestamps": ["t"] * 5, "tokens": ["x"] * 5,
        },
    }
    questions = bd.sequences_to_questions([record], vocab, 8)
    # register of "a" is skipped (nothing left once event a is hidden); save of "b" and register of "c" remain
    assert [q["action"] for q in questions] == ["save", "register"]
    assert all(q["same_event_hidden"] and q["positive"] for q in questions)

    technical, cultural, sports = (vocab["categories"][c] for c in ("technical", "cultural", "sports"))
    first, second = questions
    assert first["label_cat"] == cultural and first["cat"][-2] == technical     # b asked after a (b's own view removed)
    assert second["label_cat"] == sports and second["cat"][-2] == cultural      # c asked after b
    assert bd.MASK_ID == first["cat"][-1] == second["cat"][-1]


def test_questions_with_no_history_left_are_skipped():
    vocab = _small_vocab()
    record = {
        "student_id": "s",
        "history": {"actions": ["view", "view", "view"], "categories": ["technical"] * 3,
                    "organizers": ["ai club"] * 3, "event_ids": ["x", "x", "x"]},
        "targets": {"actions": ["register"], "categories": ["technical"], "organizers": ["ai club"],
                    "event_ids": ["x"]},
    }
    assert len(bd.build_eval_examples([record], vocab, 8)) == 1
    assert bd.build_eval_examples([record], vocab, 8, exclude_same_event=True) == []


def test_positive_flag_and_known_flags():
    data = sim_datasets()
    examples = bd.build_eval_examples(data["val"] + data["test"], data["vocab"], 30)
    assert examples
    for e in examples:
        assert e["positive"] == (e["action"] in bd.POSITIVE_ACTIONS)
        assert e["cat_known"] == (e["label_cat"] != bd.UNK_ID)
    assert any(e["positive"] for e in examples) and any(not e["positive"] for e in examples)


def test_categories_never_seen_in_training_become_unk_and_are_flagged():
    vocab = {
        "special_tokens": {"[PAD]": 0, "[MASK]": 1, "[UNK]": 2},
        "actions": {"[PAD]": 0, "[MASK]": 1, "[UNK]": 2, "view": 3, "register": 4},
        "categories": {"[PAD]": 0, "[MASK]": 1, "[UNK]": 2, "technical": 3},
        "organizers": {"[PAD]": 0, "[MASK]": 1, "[UNK]": 2, "ai club": 3},
        "max_len": 6,
    }
    record = {
        "student_id": "s",
        "history": {"actions": ["view", "view", "view"], "categories": ["technical"] * 3,
                    "organizers": ["ai club"] * 3, "event_ids": ["a", "b", "c"]},
        "targets": {"actions": ["register"], "categories": ["chess"], "organizers": ["chess club"],
                    "event_ids": ["d"]},
    }
    (example,) = bd.build_eval_examples([record], vocab, 6)
    assert example["label_cat"] == example["label_org"] == bd.UNK_ID
    assert example["cat_known"] is False and example["org_known"] is False
    assert example["positive"] is True


def test_the_whole_phase_b_to_c_handoff_through_files(tmp_path):
    data = sim_datasets()
    bs.write_outputs(str(tmp_path), data, "simulated")
    vocab = bd.load_vocab(str(tmp_path / "vocab.json"))
    train = bd.load_jsonl(str(tmp_path / "train.jsonl"))
    val = bd.load_jsonl(str(tmp_path / "val.jsonl"))
    test = bd.load_jsonl(str(tmp_path / "test.jsonl"))
    assert (len(train), len(val), len(test)) == (len(data["train"]), len(data["val"]), len(data["test"]))
    assert vocab["max_len"] == 30
    assert all(r["simulated"] is True for r in train)
    assert bd.build_eval_examples(val, vocab, vocab["max_len"])


def test_empty_files_load_as_empty_lists(tmp_path):
    path = tmp_path / "empty.jsonl"
    path.write_text("", encoding="utf-8")
    assert bd.load_jsonl(str(path)) == []