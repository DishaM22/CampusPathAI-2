"""
score_normalization.py
======================
PHASE E: put the scores of ONE recommendation request on a common 0-100 scale,
so signals that come from different models (content, semantic, sequential ...)
can be blended fairly.

    normalize_scores({"ev1": 0.12, "ev2": 0.40, "ev3": 0.07})            # min-max (default)
    normalize_scores(scores, method="rank")                              # rank percentile

Normalization is PER REQUEST: it looks only at the candidates of the request
in hand, never at past requests or other students.

Two methods
-----------
min-max   (x - min) / (max - min) * 100.  Keeps the SHAPE of the scores (a
          clear winner stays clearly ahead), but one extreme score squeezes
          everyone else toward 0.
rank      Each candidate gets its percentile position: lowest = 0, highest = 100,
          equal scores share the average position. Ignores how far apart
          scores are, so outliers and very skewed scores (such as small
          probabilities) cannot distort it.

What is never invented
----------------------
* Fewer than 3 usable candidates  -> everyone gets the neutral value (50).
* All usable scores are equal     -> everyone gets the neutral value (50).
  A spread is never fabricated from nothing.
* Missing / invalid scores (None, NaN, infinity, text, booleans) are ignored:
  they are left out of the result and do not influence the others.

Pure Python: no database, no PyTorch, no numpy.
"""
from __future__ import annotations

import math
import numbers
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, Union

LOW = 0.0
HIGH = 100.0
MIN_CANDIDATES = 3
EQUAL_TOLERANCE = 1e-12          # spreads smaller than this count as "all equal"

Scores = Union[Mapping[Any, Any], Sequence[Any]]


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------
def _is_valid(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        return False
    return math.isfinite(float(value))


def _check_settings(low: float, high: float, min_candidates: int) -> None:
    if not (_is_valid(low) and _is_valid(high)) or high <= low:
        raise ValueError("need low < high (both finite numbers)")
    if int(min_candidates) < 1:
        raise ValueError("min_candidates must be at least 1")


def _valid_items(scores: Scores) -> List[Tuple[Any, float]]:
    """[(key, float)] for the usable entries, in input order. Lists use their index as key."""
    pairs = scores.items() if isinstance(scores, Mapping) else enumerate(scores)
    return [(key, float(value)) for key, value in pairs if _is_valid(value)]


def _package(scores: Scores, normalized: Dict[Any, float]) -> Union[Dict[Any, float], List[Optional[float]]]:
    """Mappings come back as a dict of the usable keys; lists come back aligned, with None for unusable entries."""
    if isinstance(scores, Mapping):
        return normalized
    return [normalized.get(index) for index in range(len(scores))]


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


# ------------------------------------------------------------------
# Min-max
# ------------------------------------------------------------------
def min_max_normalize(
    scores: Scores,
    low: float = LOW,
    high: float = HIGH,
    neutral: Optional[float] = None,
    min_candidates: int = MIN_CANDIDATES,
):
    """
    Min-max scaling of one request's scores onto [low, high] (default 0-100).

    neutral: the value used when there is too little to compare (default: the
    middle of the range, 50 for 0-100).
    """
    _check_settings(low, high, min_candidates)
    neutral = (low + high) / 2.0 if neutral is None else float(neutral)

    items = _valid_items(scores)
    if not items:
        return _package(scores, {})

    values = [value for _, value in items]
    lowest, highest = min(values), max(values)

    if len(items) < min_candidates or (highest - lowest) <= EQUAL_TOLERANCE:
        return _package(scores, {key: neutral for key, _ in items})

    span = highest - lowest
    return _package(scores, {
        key: _clamp(low + (value - lowest) / span * (high - low), low, high)
        for key, value in items
    })


# ------------------------------------------------------------------
# Rank percentile
# ------------------------------------------------------------------
def rank_percentile_normalize(
    scores: Scores,
    low: float = LOW,
    high: float = HIGH,
    neutral: Optional[float] = None,
    min_candidates: int = MIN_CANDIDATES,
):
    """
    Percentile position of each score within the request: the lowest gets
    `low`, the highest `high`; equal scores share the average of their positions.
    """
    _check_settings(low, high, min_candidates)
    neutral = (low + high) / 2.0 if neutral is None else float(neutral)

    items = _valid_items(scores)
    if not items:
        return _package(scores, {})

    n = len(items)
    values = [value for _, value in items]
    if n < min_candidates or (max(values) - min(values)) <= EQUAL_TOLERANCE:
        return _package(scores, {key: neutral for key, _ in items})

    ordered = sorted(range(n), key=lambda index: values[index])        # stable for ties
    percentile = [0.0] * n
    start = 0
    while start < n:
        end = start
        while end + 1 < n and values[ordered[end + 1]] == values[ordered[start]]:
            end += 1
        average_position = (start + end) / 2.0
        for position in range(start, end + 1):
            percentile[ordered[position]] = low + average_position / (n - 1) * (high - low)
        start = end + 1

    return _package(scores, {
        items[index][0]: _clamp(percentile[index], low, high) for index in range(n)
    })


# ------------------------------------------------------------------
# One entry point
# ------------------------------------------------------------------
METHODS = {"minmax": min_max_normalize, "rank": rank_percentile_normalize}


def normalize_scores(
    scores: Scores,
    method: str = "minmax",
    low: float = LOW,
    high: float = HIGH,
    neutral: Optional[float] = None,
    min_candidates: int = MIN_CANDIDATES,
):
    """method: "minmax" (default) or "rank". See the module docstring."""
    try:
        function = METHODS[method]
    except KeyError:
        raise ValueError(f"unknown method '{method}' (use one of {sorted(METHODS)})") from None
    return function(scores, low=low, high=high, neutral=neutral, min_candidates=min_candidates)