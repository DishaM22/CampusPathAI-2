"""
Phase E tests: score normalization. Pure Python, no database, no PyTorch.

Run from the backend folder:
    python -m pytest tests/test_phase_e_normalization.py -q
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import score_normalization as sn  # noqa: E402


def close(a, b, tol=1e-9):
    return abs(a - b) <= tol


def raises(error, function, *args, **kwargs):
    try:
        function(*args, **kwargs)
    except error:
        return True
    return False


# ------------------------------------------------------------------ min-max
def test_minmax_maps_lowest_to_0_and_highest_to_100():
    out = sn.min_max_normalize({"a": 0.1, "b": 0.5, "c": 0.9})
    assert out == {"a": 0.0, "b": 50.0, "c": 100.0}


def test_minmax_keeps_keys_order_and_ranking_and_stays_in_range():
    scores = {"x": 3.2, "y": -1.5, "z": 8.8, "w": 0.0, "v": 2.2}
    out = sn.min_max_normalize(scores)
    assert list(out) == list(scores)
    assert all(0.0 <= v <= 100.0 for v in out.values())
    assert sorted(out, key=out.get) == sorted(scores, key=scores.get)
    assert min(out.values()) == 0.0 and max(out.values()) == 100.0


def test_minmax_ignores_scale_and_shift_and_handles_negative_scores():
    base = {"a": 0.2, "b": 0.5, "c": 0.9, "d": 0.4}
    scaled = {k: v * 1000 + 7 for k, v in base.items()}
    a, b = sn.min_max_normalize(base), sn.min_max_normalize(scaled)
    assert all(close(a[k], b[k], 1e-7) for k in base)
    assert sn.min_max_normalize({"a": -3, "b": -2, "c": -1}) == {"a": 0.0, "b": 50.0, "c": 100.0}   # log-probabilities


def test_minmax_is_idempotent():
    once = sn.min_max_normalize({"a": 0.3, "b": 0.31, "c": 0.9, "d": 0.5})
    twice = sn.min_max_normalize(once)
    assert all(close(once[k], twice[k], 1e-9) for k in once)


def test_fewer_than_three_candidates_all_get_the_neutral_value():
    assert sn.min_max_normalize({"a": 0.1}) == {"a": 50.0}
    assert sn.min_max_normalize({"a": 0.1, "b": 0.9}) == {"a": 50.0, "b": 50.0}
    assert sn.min_max_normalize({"a": 0.1, "b": 0.5, "c": 0.9})["a"] == 0.0         # exactly three is enough


def test_all_equal_scores_give_neutral_not_a_made_up_spread():
    assert sn.min_max_normalize({"a": 0.4, "b": 0.4, "c": 0.4, "d": 0.4}) == {k: 50.0 for k in "abcd"}
    nearly = {"a": 0.4, "b": 0.4 + 1e-15, "c": 0.4 - 1e-15}
    assert set(sn.min_max_normalize(nearly).values()) == {50.0}


def test_empty_input_gives_empty_output():
    assert sn.min_max_normalize({}) == {} and sn.min_max_normalize([]) == []
    assert sn.rank_percentile_normalize({}) == {} and sn.rank_percentile_normalize([]) == []


def test_invalid_scores_are_ignored_and_do_not_influence_the_others():
    messy = {"a": 0.1, "b": 0.5, "c": 0.9, "n": None, "f": float("nan"), "i": float("inf"),
             "m": float("-inf"), "s": "0.7", "t": True, "o": object()}
    out = sn.min_max_normalize(messy)
    assert out == {"a": 0.0, "b": 50.0, "c": 100.0}                                  # junk left out, rest unchanged
    assert sn.min_max_normalize({"a": 0.1, "b": 0.9, "n": None}) == {"a": 50.0, "b": 50.0}   # only 2 usable -> neutral


def test_lists_come_back_aligned_with_none_for_unusable_entries():
    out = sn.min_max_normalize([0.1, None, 0.5, float("nan"), 0.9])
    assert out == [0.0, None, 50.0, None, 100.0]
    assert sn.rank_percentile_normalize([0.1, None, 0.5, 0.9]) == [0.0, None, 50.0, 100.0]


def test_the_input_is_never_modified():
    scores = {"a": 0.1, "b": 0.5, "c": 0.9}
    snapshot = dict(scores)
    sn.min_max_normalize(scores)
    sn.rank_percentile_normalize(scores)
    assert scores == snapshot


def test_custom_range_and_neutral():
    assert sn.min_max_normalize({"a": 1, "b": 2, "c": 3}, low=0, high=1) == {"a": 0.0, "b": 0.5, "c": 1.0}
    assert sn.min_max_normalize({"a": 1, "b": 1, "c": 1}, low=0, high=1) == {"a": 0.5, "b": 0.5, "c": 0.5}
    assert sn.min_max_normalize({"a": 1, "b": 1, "c": 1}, neutral=40) == {"a": 40.0, "b": 40.0, "c": 40.0}
    assert sn.min_max_normalize({"a": 1}, min_candidates=1) == {"a": 50.0}          # one candidate is still "nothing to compare"
    assert sn.min_max_normalize({"a": 1, "b": 3}, min_candidates=2) == {"a": 0.0, "b": 100.0}


def test_bad_settings_are_rejected():
    assert raises(ValueError, sn.min_max_normalize, {"a": 1}, low=5, high=5)
    assert raises(ValueError, sn.min_max_normalize, {"a": 1}, low=10, high=0)
    assert raises(ValueError, sn.min_max_normalize, {"a": 1}, low=float("nan"), high=1)
    assert raises(ValueError, sn.min_max_normalize, {"a": 1}, min_candidates=0)
    assert raises(ValueError, sn.rank_percentile_normalize, {"a": 1}, low=1, high=1)


# ------------------------------------------------------------------ rank percentile
def test_rank_spreads_evenly_regardless_of_the_gaps():
    out = sn.rank_percentile_normalize({"a": 1, "b": 2, "c": 3, "d": 4, "e": 1000})
    assert out == {"a": 0.0, "b": 25.0, "c": 50.0, "d": 75.0, "e": 100.0}


def test_minmax_is_squeezed_by_an_outlier_but_rank_is_not():
    scores = {"a": 1, "b": 2, "c": 3, "d": 4, "e": 1000}
    mm = sn.min_max_normalize(scores)
    assert mm["d"] < 1.0 and mm["e"] == 100.0
    assert sn.rank_percentile_normalize(scores)["d"] == 75.0


def test_rank_ties_share_the_average_position():
    out = sn.rank_percentile_normalize({"a": 1, "b": 1, "c": 2, "d": 3})
    assert close(out["a"], out["b"]) and close(out["a"], 0.5 / 3 * 100)
    assert close(out["c"], 2 / 3 * 100) and out["d"] == 100.0
    top_tie = sn.rank_percentile_normalize({"a": 1, "b": 5, "c": 5})
    assert close(top_tie["b"], top_tie["c"]) and close(top_tie["b"], 75.0)          # average of 50 and 100
    assert top_tie["a"] == 0.0


def test_rank_neutral_rules_match_minmax():
    assert sn.rank_percentile_normalize({"a": 1, "b": 2}) == {"a": 50.0, "b": 50.0}
    assert sn.rank_percentile_normalize({"a": 3, "b": 3, "c": 3}) == {"a": 50.0, "b": 50.0, "c": 50.0}
    assert sn.rank_percentile_normalize({"a": 1, "b": 2, "c": 3, "x": None}) == {"a": 0.0, "b": 50.0, "c": 100.0}


def test_rank_is_deterministic_and_order_independent():
    scores = {"a": 0.3, "b": 0.1, "c": 0.7, "d": 0.3, "e": 0.9}
    shuffled = {k: scores[k] for k in ["e", "c", "a", "b", "d"]}
    one, two = sn.rank_percentile_normalize(scores), sn.rank_percentile_normalize(shuffled)
    assert all(close(one[k], two[k]) for k in scores)
    assert sn.rank_percentile_normalize(scores) == one


def test_rank_values_stay_in_range_for_many_candidates():
    scores = {i: math.sin(i) for i in range(500)}
    out = sn.rank_percentile_normalize(scores)
    assert min(out.values()) == 0.0 and max(out.values()) == 100.0
    assert all(0.0 <= v <= 100.0 for v in out.values())


# ------------------------------------------------------------------ dispatcher
def test_normalize_scores_picks_the_method_and_rejects_unknown_ones():
    scores = {"a": 1, "b": 2, "c": 3, "d": 4, "e": 1000}
    assert sn.normalize_scores(scores) == sn.min_max_normalize(scores)
    assert sn.normalize_scores(scores, method="minmax") == sn.min_max_normalize(scores)
    assert sn.normalize_scores(scores, method="rank") == sn.rank_percentile_normalize(scores)
    assert raises(ValueError, sn.normalize_scores, scores, method="zscore")


def test_integer_and_float_inputs_both_work_and_results_are_floats():
    out = sn.normalize_scores({"a": 1, "b": 2, "c": 3})
    assert out == {"a": 0.0, "b": 50.0, "c": 100.0} and all(isinstance(v, float) for v in out.values())


def test_non_string_keys_are_kept_as_they_are():
    out = sn.normalize_scores({1: 0.1, (2, "x"): 0.5, 3.5: 0.9})
    assert set(out) == {1, (2, "x"), 3.5}