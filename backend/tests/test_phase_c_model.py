"""
Phase C tests, part 2: the model, Cloze masking, training and reproducibility.
Needs PyTorch (skipped automatically if it is not installed). No database.

Run from the backend folder:
    python -m pytest tests/test_phase_c_model.py -q
"""
import json
import math
import random
from datetime import datetime, timezone

import pytest

torch = pytest.importorskip("torch")

import build_sequences as bs  # noqa: E402
import bert4rec_data as bd  # noqa: E402
import bert4rec_model as bm  # noqa: E402
import simulate_interactions as sim  # noqa: E402
import train_bert4rec as tb  # noqa: E402

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


def small_cfg(max_len=10, dropout=0.1):
    return bm.Bert4RecConfig(n_actions=10, n_categories=9, n_organizers=8, max_len=max_len,
                             hidden=16, layers=2, heads=2, ff=32, dropout=dropout)


def make_batch(valid_lengths, max_len=10, seed=0):
    """Left-padded batch of ids: real ids are >= 3, padding is 0."""
    rng = random.Random(seed)
    cat, org, act = [], [], []
    for n in valid_lengths:
        pad = [0] * (max_len - n)
        cat.append(pad + [rng.randint(3, 8) for _ in range(n)])
        org.append(pad + [rng.randint(3, 7) for _ in range(n)])
        act.append(pad + [rng.randint(3, 9) for _ in range(n)])
    return (torch.tensor(cat), torch.tensor(org), torch.tensor(act))


def make_groups(valid_lengths, max_len=10, seed=0):
    """Event-group ids per position (runs of 1-4 interactions per event); 0 under padding."""
    rng = random.Random(seed)
    rows = []
    for n in valid_lengths:
        ids, group = [], 0
        while len(ids) < n:
            group += 1
            ids += [group] * min(rng.randint(1, 4), n - len(ids))
        rows.append([0] * (max_len - n) + ids)
    return torch.tensor(rows)


def sim_files(tmp_path, copies=25, seed=8):
    rows = sim.simulate(PROFILES, EVENTS, copies=copies, seed=seed, now=NOW)
    data = bs.build_datasets(rows)
    bs.write_outputs(str(tmp_path), data, "simulated")
    return data


def sim_examples(copies=25, seed=8):
    rows = sim.simulate(PROFILES, EVENTS, copies=copies, seed=seed, now=NOW)
    data = bs.build_datasets(rows)
    vocab = data["vocab"]
    return (bd.build_train_examples(data["train"], vocab, 30),
            bd.build_eval_examples(data["val"], vocab, 30),
            bd.build_eval_examples(data["test"], vocab, 30), vocab)


# ------------------------------------------------------------------ model shape / behaviour
def test_forward_shapes():
    cfg = small_cfg()
    model = bm.Bert4Rec(cfg)
    cat, org, act = make_batch([10, 6, 3])
    cat_logits, org_logits = model(cat, org, act)
    assert cat_logits.shape == (3, 10, cfg.n_categories)
    assert org_logits.shape == (3, 10, cfg.n_organizers)
    assert torch.isfinite(cat_logits).all() and torch.isfinite(org_logits).all()


def test_shorter_sequences_than_max_len_are_accepted_but_longer_are_refused():
    model = bm.Bert4Rec(small_cfg(max_len=10))
    cat, org, act = make_batch([4, 4], max_len=6)
    assert model(cat, org, act)[0].shape[1] == 6
    cat, org, act = make_batch([4], max_len=12)
    with pytest.raises(ValueError):
        model(cat, org, act)


def test_bad_head_count_is_rejected():
    with pytest.raises(ValueError):
        bm.Bert4Rec(bm.Bert4RecConfig(n_actions=5, n_categories=5, n_organizers=5, hidden=10, heads=3))


def test_padding_never_changes_the_output_at_real_positions():
    model = bm.Bert4Rec(small_cfg()).eval()
    cat, org, act = make_batch([10, 6, 3])
    cat2, org2 = cat.clone(), org.clone()
    pad = act == 0
    cat2[pad] = 7                                  # garbage under the padding
    org2[pad] = 5
    with torch.no_grad():
        a_cat, a_org = model(cat, org, act)
        b_cat, b_org = model(cat2, org2, act)
    real = ~pad
    assert torch.allclose(a_cat[real], b_cat[real], atol=1e-5)
    assert torch.allclose(a_org[real], b_org[real], atol=1e-5)


def test_the_model_is_bidirectional():
    """A later token influences earlier positions (no causal mask)."""
    model = bm.Bert4Rec(small_cfg()).eval()
    cat, org, act = make_batch([10])
    changed = cat.clone()
    changed[0, -1] = 3 if int(cat[0, -1]) != 3 else 4
    with torch.no_grad():
        before = model(cat, org, act)[0][0, 0]
        after = model(changed, org, act)[0][0, 0]
    assert not torch.allclose(before, after, atol=1e-7)


def test_special_tokens_can_never_be_predicted():
    model = bm.Bert4Rec(small_cfg())
    cat, org, act = make_batch([5, 8])
    cat[:, -1] = bm.MASK_ID
    org[:, -1] = bm.MASK_ID
    cat_lp, org_lp = bm.predict_next_log_probs(model, cat, org, act)
    for lp in (cat_lp, org_lp):
        probs = lp.exp()
        assert probs[:, :3].max().item() < 1e-6
        assert torch.allclose(probs.sum(dim=-1), torch.ones(2), atol=1e-4)


def test_predict_does_not_leave_the_model_in_eval_mode():
    model = bm.Bert4Rec(small_cfg())
    model.train()
    cat, org, act = make_batch([5])
    bm.predict_next_log_probs(model, cat, org, act)
    assert model.training is True


# ------------------------------------------------------------------ Cloze masking
def test_masking_rules():
    cat, org, act = make_batch([10, 7, 4, 2, 10, 5], seed=3)
    g = torch.Generator().manual_seed(1)
    in_cat, in_org, lab_cat, lab_org = bm.apply_cloze_mask(cat, org, act, 0.2, 0.25, g)

    valid = act != 0
    masked = lab_cat != bm.IGNORE_INDEX
    assert torch.equal(masked, lab_org != bm.IGNORE_INDEX)          # category and club are masked together
    assert not (masked & ~valid).any()                              # padding is never masked
    assert masked.any(dim=1).all()                                  # every row has at least one
    assert (in_cat[masked] == bm.MASK_ID).all() and (in_org[masked] == bm.MASK_ID).all()
    assert torch.equal(in_cat[~masked], cat[~masked])               # everything else untouched
    assert torch.equal(in_org[~masked], org[~masked])
    assert torch.equal(lab_cat[masked], cat[masked])                # label = the hidden original
    assert torch.equal(lab_org[masked], org[masked])


def test_masking_is_reproducible_with_a_seed_and_differs_between_seeds():
    cat, org, act = make_batch([10] * 20, seed=4)

    def run(seed):
        return bm.apply_cloze_mask(cat, org, act, 0.3, 0.25, torch.Generator().manual_seed(seed))[2]

    assert torch.equal(run(5), run(5))
    assert not torch.equal(run(5), run(6))


def test_mask_rate_is_close_to_the_requested_rate():
    cat, org, act = make_batch([20] * 400, max_len=20, seed=5)
    _, _, labels, _ = bm.apply_cloze_mask(cat, org, act, 0.2, 0.0, torch.Generator().manual_seed(0))
    rate = (labels != bm.IGNORE_INDEX).float().mean().item()
    assert 0.17 < rate < 0.23


def test_mask_last_prob_one_always_masks_the_newest_item():
    cat, org, act = make_batch([10, 6, 3, 8], seed=6)
    _, _, labels, _ = bm.apply_cloze_mask(cat, org, act, 0.0, 1.0, torch.Generator().manual_seed(0))
    assert (labels[:, -1] != bm.IGNORE_INDEX).all()
    assert (labels != bm.IGNORE_INDEX).sum().item() == 4            # nothing else was masked


def test_event_level_masking_hides_whole_events_together():
    lengths = [10, 7, 4, 2, 10, 5]
    cat, org, act = make_batch(lengths, seed=3)
    groups = make_groups(lengths, seed=3)
    for seed in range(6):
        in_cat, in_org, lab_cat, lab_org = bm.apply_cloze_mask(
            cat, org, act, 0.3, 0.25, torch.Generator().manual_seed(seed), groups)
        masked = lab_cat != bm.IGNORE_INDEX
        assert torch.equal(masked, lab_org != bm.IGNORE_INDEX)
        assert not (masked & (act == 0)).any()                       # padding never masked
        assert masked.any(dim=1).all()                               # at least one per row
        assert (in_cat[masked] == bm.MASK_ID).all() and (in_org[masked] == bm.MASK_ID).all()
        assert torch.equal(in_cat[~masked], cat[~masked])
        for row in range(len(lengths)):
            for group in groups[row].unique().tolist():
                if group == 0:
                    continue
                members = masked[row][groups[row] == group]
                assert bool(members.all()) or not bool(members.any()), "an event was only partly masked"


def test_event_level_masking_is_reproducible_and_close_to_the_requested_rate():
    lengths = [20] * 400
    cat, org, act = make_batch(lengths, max_len=20, seed=5)
    groups = make_groups(lengths, max_len=20, seed=5)

    def labels(seed):
        return bm.apply_cloze_mask(cat, org, act, 0.2, 0.0, torch.Generator().manual_seed(seed), groups)[2]

    assert torch.equal(labels(1), labels(1)) and not torch.equal(labels(1), labels(2))
    rate = (labels(1) != bm.IGNORE_INDEX).float().mean().item()
    assert 0.14 < rate < 0.26


def test_event_level_mask_last_prob_one_hides_the_whole_newest_event():
    lengths = [10, 6, 8]
    cat, org, act = make_batch(lengths, seed=6)
    groups = make_groups(lengths, seed=6)
    _, _, labels, _ = bm.apply_cloze_mask(cat, org, act, 0.0, 1.0, torch.Generator().manual_seed(0), groups)
    masked = labels != bm.IGNORE_INDEX
    for row in range(len(lengths)):
        newest = groups[row, -1]
        assert torch.equal(masked[row], groups[row] == newest)       # exactly the newest event


def test_actions_stay_visible_at_masked_positions():
    cat, org, act = make_batch([10, 10], seed=7)
    original = act.clone()
    bm.apply_cloze_mask(cat, org, act, 0.5, 0.5, torch.Generator().manual_seed(0))
    assert torch.equal(act, original)


def test_cloze_loss_is_finite_and_has_a_gradient():
    model = bm.Bert4Rec(small_cfg())
    cat, org, act = make_batch([10, 7, 4])
    in_cat, in_org, lab_cat, lab_org = bm.apply_cloze_mask(cat, org, act, 0.3, 0.3, torch.Generator().manual_seed(0))
    loss = bm.cloze_loss(*model(in_cat, in_org, act), lab_cat, lab_org)
    assert math.isfinite(loss.item())
    loss.backward()
    assert any(p.grad is not None and p.grad.abs().sum() > 0 for p in model.parameters())


# ------------------------------------------------------------------ checkpoints
def test_checkpoint_round_trip_gives_identical_predictions(tmp_path):
    model = bm.Bert4Rec(small_cfg()).eval()
    path = str(tmp_path / "model.pt")
    bm.save_checkpoint(path, model, {"trained_on_simulated": True, "note": "x"})
    loaded, meta = bm.load_checkpoint(path)
    assert meta["trained_on_simulated"] is True
    cat, org, act = make_batch([6, 9])
    with torch.no_grad():
        a = model(cat, org, act)
        b = loaded(cat, org, act)
    assert torch.allclose(a[0], b[0], atol=1e-6) and torch.allclose(a[1], b[1], atol=1e-6)


# ------------------------------------------------------------------ metrics and baselines
def test_score_logits_top_k_and_unknown_labels():
    logits = torch.full((4, 6), -1e9)
    logits[:, 3:] = torch.tensor([[3.0, 2.0, 1.0],      # ranks: 3, 4, 5
                                  [1.0, 3.0, 2.0],      # ranks: 4, 5, 3
                                  [1.0, 2.0, 3.0],      # ranks: 5, 4, 3
                                  [3.0, 2.0, 1.0]])
    labels = torch.tensor([3, 5, 5, 2])                  # rows 0 and 2 are top-1 hits, row 1 is rank 2, row 3 is unknown (UNK)
    known = torch.tensor([True, True, True, False])
    out = tb.score_logits(logits, logits, labels, labels, known, known, ks=(1, 3))
    assert out["n_cat"] == 3
    assert out["cat_acc@1"] == round(2 / 3, 4)           # rows 0 and 2
    assert out["cat_acc@3"] == 1.0


def test_chance_level_is_exact():
    known = torch.tensor([True, True, True])
    out = tb.chance_scores(known, known, n_categories=6, n_organizers=5, ks=(1, 3))
    assert out["cat_acc@1"] == round(1 / 3, 4) and out["cat_acc@3"] == 1.0
    assert out["org_acc@1"] == 0.5 and out["org_acc@3"] == 1.0


def test_repeat_last_baseline_beats_popularity_when_the_pattern_is_repetition():
    cat = [0, 0, 0, 3, 4, 4, 4, 1]                       # previous = 4, question slot last
    example = {"cat": cat, "org": cat, "act": [0, 0, 0, 4, 4, 4, 4, 4], "label_cat": 4, "label_org": 4,
               "cat_known": True, "org_known": True, "positive": True}
    train = [{"cat": [0, 0, 3, 3, 3, 3, 3, 3], "org": [0, 0, 3, 3, 3, 3, 3, 3], "act": [0, 0, 4, 4, 4, 4, 4, 4]}]
    base = tb.baseline_scores([example], train, n_categories=6, n_organizers=6, ks=(1, 3))
    assert base["repeat_last"]["cat_acc@1"] == 1.0       # guesses the previous category (4)
    assert base["popularity"]["cat_acc@1"] == 0.0        # guesses 3, the most frequent in training


def _question(prev_cat, label_cat, prev_org=3, label_org=3):
    cat = [0, 0, 0, 0, 0, 0, prev_cat, bd.MASK_ID]
    org = [0, 0, 0, 0, 0, 0, prev_org, bd.MASK_ID]
    return {"cat": cat, "org": org, "act": [0] * 6 + [4, 5], "label_cat": label_cat, "label_org": label_org,
            "cat_known": True, "org_known": True, "positive": True}


def test_markov_baseline_learns_a_transition_that_popularity_misses():
    # in training, after category 3 students always engage with 4, after 5 always with 3 (but 3 is most common overall)
    train_questions = [_question(3, 4)] * 12 + [_question(5, 3)] * 6
    train_examples = [{"cat": [0, 0, 3, 3, 3, 3, 3, 3], "org": [0, 0, 3, 3, 3, 3, 3, 3],
                       "act": [0, 0, 4, 4, 4, 4, 4, 4]}]
    asked = [_question(3, 4), _question(5, 3)]
    markov = tb.markov_scores(asked, train_questions, train_examples, 6, 5)
    popularity = tb.baseline_scores(asked, train_examples, 6, 5)["popularity"]
    assert markov["cat_acc@1"] == 1.0
    assert popularity["cat_acc@1"] == 0.5                  # always guesses 3


def test_markov_baseline_falls_back_to_popularity_for_an_unseen_previous_category():
    train_examples = [{"cat": [0, 0, 3, 3, 3, 3, 3, 3], "org": [0, 0, 3, 3, 3, 3, 3, 3],
                       "act": [0, 0, 4, 4, 4, 4, 4, 4]}]
    asked = [_question(5, 3)]                              # category 5 never seen before a question in training
    markov = tb.markov_scores(asked, [_question(3, 4)], train_examples, 6, 5)
    assert markov["cat_acc@1"] == 1.0                      # popularity says 3, which is right


def _m(n, cat, org, margin=0.05):
    return {"n": n, "cat_acc@1": cat, "cat_acc@1_margin95": margin, "org_acc@1": org, "org_acc@1_margin95": margin}


def _block(model, popularity, markov):
    return {"model": model, "popularity": popularity, "markov": markov}


def test_gate_passes_only_on_a_clear_win_over_both_baselines():
    block = _block(_m(200, 0.60, 0.60), _m(200, 0.40, 0.58), _m(200, 0.41, 0.57))
    gate = tb.evaluate_gate(block)
    assert gate["passed"] is True and gate["n_questions"] == 200
    assert any("category" in r for r in gate["reasons"])


def test_gate_fails_when_the_gap_is_inside_the_error_margins():
    block = _block(_m(200, 0.45, 0.58), _m(200, 0.40, 0.58), _m(200, 0.40, 0.58))     # +0.05 < 0.05 + 0.05
    assert tb.evaluate_gate(block)["passed"] is False


def test_gate_fails_when_the_model_only_beats_one_baseline():
    block = _block(_m(200, 0.60, 0.60), _m(200, 0.40, 0.58), _m(200, 0.58, 0.58))
    assert tb.evaluate_gate(block)["passed"] is False


def test_gate_fails_if_the_model_is_clearly_worse_somewhere_even_with_a_win_elsewhere():
    block = _block(_m(200, 0.60, 0.30), _m(200, 0.40, 0.58), _m(200, 0.41, 0.57))     # club far below baselines
    gate = tb.evaluate_gate(block)
    assert gate["passed"] is False and any("clearly worse" in r for r in gate["reasons"])


def test_gate_needs_enough_questions_and_both_baselines():
    small = _block(_m(40, 0.9, 0.9), _m(40, 0.1, 0.1), _m(40, 0.1, 0.1))
    assert tb.evaluate_gate(small)["passed"] is False
    missing = {"model": _m(200, 0.9, 0.9), "popularity": _m(200, 0.1, 0.1)}
    assert tb.evaluate_gate(missing)["passed"] is False
    assert tb.evaluate_gate({"model": {"n": 0}})["passed"] is False


# ------------------------------------------------------------------ training
def vocab_cfg(vocab, layers=1):
    return bm.Bert4RecConfig(
        n_actions=len(vocab["actions"]), n_categories=len(vocab["categories"]),
        n_organizers=len(vocab["organizers"]), max_len=30, hidden=16, layers=layers, heads=2, ff=32, dropout=0.1,
    )


def tiny_train(seed, train, val, vocab, epochs=3):
    return tb.train_model(train, val, vocab_cfg(vocab), epochs=epochs, patience=epochs, batch_size=16, seed=seed)


def test_training_is_reproducible_for_a_seed_and_differs_for_another():
    train, val, _, vocab = sim_examples()
    a, _, _ = tiny_train(1, train, val, vocab)
    b, _, _ = tiny_train(1, train, val, vocab)
    c, _, _ = tiny_train(2, train, val, vocab)
    same = all(torch.allclose(x, y, atol=1e-6) for x, y in zip(a.state_dict().values(), b.state_dict().values()))
    other = any(not torch.allclose(x, y, atol=1e-6) for x, y in zip(a.state_dict().values(), c.state_dict().values()))
    assert same and other


def test_training_also_runs_with_single_interaction_masking():
    train, val, _, vocab = sim_examples()
    model, history, _ = tb.train_model(train, val, vocab_cfg(vocab), epochs=2, patience=2,
                                       batch_size=16, seed=1, mask_by_event=False)
    assert len(history) >= 1 and math.isfinite(history[-1]["val_loss"])


def test_training_needs_validation_data():
    train, val, _, _ = sim_examples()
    with pytest.raises(ValueError):
        tb.train_model(train, [], small_cfg(max_len=30), epochs=1)
    with pytest.raises(ValueError):
        tb.train_model([], val, small_cfg(max_len=30), epochs=1)


def test_early_stopping_and_best_weights_are_restored():
    train, val, _, vocab = sim_examples()
    model, history, info = tb.train_model(train, val, vocab_cfg(vocab), epochs=40, patience=3,
                                          batch_size=16, seed=3, lr=5e-3)
    assert info["epochs_run"] == len(history) <= 40
    assert 1 <= info["best_epoch"] <= info["epochs_run"]
    lowest = min(r["val_loss"] for r in history)
    assert info["best_val_loss"] == pytest.approx(lowest, abs=2e-3)
    assert tb.evaluate(model, val)["loss"] == pytest.approx(info["best_val_loss"], abs=1e-3)


def test_the_model_can_learn_a_simple_alternating_pattern():
    """Sanity check: it must be able to learn something. Alternating A,B,A,B... categories."""
    max_len, A, B = 8, 3, 4
    rng = random.Random(0)
    other = {A: B, B: A}

    def sequence(start):
        seq, cur = [], start
        for _ in range(max_len):
            seq.append(cur)
            cur = other[cur]
        return seq

    train = []
    for _ in range(64):
        seq = sequence(rng.choice([A, B]))
        train.append({"cat": seq, "org": [3] * max_len, "act": [4] * max_len})

    val = []
    for _ in range(60):
        history_len = rng.randint(3, 7)
        start = rng.choice([A, B])
        full = sequence(start) + [other[sequence(start)[-1]]]
        history = full[:history_len]
        label = full[history_len]
        ctx = bd.build_context({"actions": [4] * history_len, "categories": history, "organizers": [3] * history_len},
                               4, max_len)
        val.append({**ctx, "label_cat": label, "label_org": 3, "cat_known": True, "org_known": True,
                    "positive": True})

    cfg = bm.Bert4RecConfig(n_actions=5, n_categories=5, n_organizers=4, max_len=max_len,
                            hidden=32, layers=2, heads=2, ff=64, dropout=0.1)
    model, _, _ = tb.train_model(train, val, cfg, epochs=250, patience=250, batch_size=16, lr=3e-3,
                                 mask_prob=0.3, mask_last_prob=0.5, seed=0)
    assert tb.evaluate(model, val)["cat_acc@1"] >= 0.8              # chance is 0.5


# ------------------------------------------------------------------ the whole script
def test_script_end_to_end_writes_everything_and_flags_simulated_data(tmp_path):
    data_dir, out_dir = tmp_path / "data", tmp_path / "out"
    sim_files(data_dir)
    code = tb.run(str(data_dir), str(out_dir), cfg_overrides={"hidden": 16, "layers": 1, "heads": 2},
                  min_train=5, verbose=False, epochs=3, patience=2, batch_size=16, seed=1)
    assert code == 0
    for name in ("model.pt", "vocab.json", "train_report.json", "train_report.md"):
        assert (out_dir / name).exists(), name

    model, meta = bm.load_checkpoint(str(out_dir / "model.pt"))
    assert meta["trained_on_simulated"] is True and meta["seed"] == 1
    assert type(meta["torch_version"]) is str and meta["mask_by_event"] is True
    report = json.loads((out_dir / "train_report.json").read_text(encoding="utf-8"))
    assert report["trained_on_simulated"] is True
    assert set(report["results"]["test"]["all"]) == {"model", "popularity", "repeat_last", "chance"}
    assert set(report["results"]["test"]) == {"all", "positive", "recommendation"}
    assert "markov" in report["results"]["test"]["recommendation"]
    assert isinstance(meta["quality_gate_passed"], bool) and isinstance(meta["quality_gate_reasons"], list)
    assert report["quality_gate"]["passed"] == meta["quality_gate_passed"]
    assert "## Quality gate" in (out_dir / "train_report.md").read_text(encoding="utf-8")
    assert "markov" not in report["results"]["test"]["all"]
    assert "TRAINED ON SIMULATED DATA" in (out_dir / "train_report.md").read_text(encoding="utf-8")


def test_script_refuses_to_train_on_too_little_data(tmp_path, capsys):
    rows = [{"student_id": "s", "event_id": "e", "action": "view", "timestamp": datetime(2026, 1, 1, tzinfo=timezone.utc),
             "category": "Technical", "organizer": "AI Club"}] * 4
    data = bs.build_datasets(rows)
    bs.write_outputs(str(tmp_path / "data"), data, "real")
    assert tb.run(str(tmp_path / "data"), str(tmp_path / "out"), verbose=False) == 1
    assert "Not enough training sequences" in capsys.readouterr().out
    assert not (tmp_path / "out" / "model.pt").exists()