"""
trained_ranker.py
=================
PHASE G: a small LEARNED RANKER that can adjust the engine's hybrid score
(final = 0.80 * hybrid + 0.20 * ranker), plus the rules that decide whether
it may be used at all.

What was wrong with the old MLP
-------------------------------
The old training code derived its labels from the "feedback" score and ALSO
used that same feedback score as an input. The model could simply read the
answer from its input (label leakage), so it looked perfect in training and
could not be trusted. It was also never trained or passed to the engine.

What is different here
----------------------
* REAL labels. Positive = the student registered for / attended the event.
  Negative = the event was recommended on the dashboard and the student did
  nothing with it, or the student pressed "not interested".
* NO feedback input. The ranker only sees two numbers: the content match and
  the text match (semantic OR keyword, recorded in the model). It never sees
  saved / registered / attended.
* NO seat availability. The database only holds TODAY's seat counts, which
  already include the registrations the ranker is supposed to predict, so
  using them for past events would leak the answer.
* A simple model: logistic regression (small, regularised, class-balanced).
  With little data a bigger model only memorises.
* TEMPORAL split: trained on the earliest part of the data, validated on the
  next, TESTED once on the latest part.
* A QUALITY GATE: the ranker may only be used if blending it in improves AUC
  over the hybrid score alone, with a 95% bootstrap margin that excludes zero,
  on enough held-out cases. Otherwise it stays off.
* Never used when trained on synthetic data (only real data counts).

If it cannot score an event (different text signal than in training, missing
data, any error) it returns None and the engine uses the hybrid score alone.

scikit-learn and joblib are imported only when needed.
"""
from __future__ import annotations

import json
import logging
import math
import os
import random
import threading
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

FEATURE_NAMES: Tuple[str, ...] = ("content", "text_match")

# A model is only trained when there is enough data to say anything.
MIN_PAIRS = 200
MIN_POSITIVES = 40
MIN_NEGATIVES = 40
MIN_STUDENTS = 15
TEST_MIN_EACH_CLASS = 20          # held-out cases needed per class for the gate
BOOTSTRAPS = 400
BLEND_RANKER_WEIGHT = 0.20        # the engine blends 0.80 hybrid + 0.20 ranker
C_GRID = (0.05, 0.5, 5.0)

DEFAULT_DIR = os.path.join("models", "ranker")
MODEL_FILE = "ranker.joblib"
REPORT_FILE = "ranker_report.json"


# ------------------------------------------------------------------
# Features
# ------------------------------------------------------------------
def _clamp01(value: float) -> float:
    return 0.0 if value < 0 else 1.0 if value > 1 else value


def feature_row(content: float, text_match: float) -> List[float]:
    """The ranker's input: two numbers, each 0-1. Deliberately has NO feedback and NO availability feature."""
    return [
        _clamp01(float(content) / 100.0),
        _clamp01(float(text_match) / 100.0),
    ]


def text_signal_for(semantic_available: bool, lexical_available: bool) -> Optional[str]:
    """Which text-similarity score the engine uses for this event (semantic first, keyword fallback)."""
    if semantic_available:
        return "semantic"
    if lexical_available:
        return "lexical"
    return None


# ------------------------------------------------------------------
# The ranker object the engine calls
# ------------------------------------------------------------------
class Ranker:
    """Wraps a fitted scikit-learn classifier. `score_event_features` never raises."""

    def __init__(self, estimator: Any, text_signal: str, meta: Optional[Dict[str, Any]] = None):
        self.estimator = estimator
        self.text_signal = text_signal
        self.meta = dict(meta or {})
        self.feature_names = FEATURE_NAMES

    def score_event_features(
        self,
        *,
        content: float,
        semantic: float,
        lexical: float,
        semantic_available: bool,
        lexical_available: bool,
        availability: float = 0.0,            # accepted so the engine can pass everything it has;
        availability_known: bool = False,     # the ranker deliberately ignores both
    ) -> Optional[float]:
        """0-100 probability-like score, or None if this event cannot be scored the way the model was trained."""
        try:
            signal = text_signal_for(semantic_available, lexical_available)
            if signal is None or signal != self.text_signal:
                return None
            text = semantic if signal == "semantic" else lexical
            row = feature_row(content, text)
            probability = float(self.estimator.predict_proba([row])[0][1])
            if not math.isfinite(probability):
                return None
            return max(0.0, min(100.0, probability * 100.0))
        except Exception:
            return None


# ------------------------------------------------------------------
# Data handling (pure)
# ------------------------------------------------------------------
def temporal_split(pairs: Sequence[Dict[str, Any]], train_frac: float = 0.70, val_frac: float = 0.15):
    """
    Split by TIME (field "ts"): train < t1 <= validation < t2 <= test. Nothing in a
    later part is ever used to fit an earlier decision.
    """
    if not (0 < train_frac < 1) or val_frac < 0 or train_frac + val_frac >= 1:
        raise ValueError("need 0 < train_frac < 1, val_frac >= 0 and train_frac + val_frac < 1")
    ordered = sorted(pairs, key=lambda p: p["ts"])
    if not ordered:
        return [], [], []
    n = len(ordered)
    t1 = ordered[min(n - 1, int(n * train_frac))]["ts"]
    t2 = ordered[min(n - 1, int(n * (train_frac + val_frac)))]["ts"]
    train = [p for p in ordered if p["ts"] < t1]
    val = [p for p in ordered if t1 <= p["ts"] < t2]
    test = [p for p in ordered if p["ts"] >= t2]
    return train, val, test


def class_counts(pairs: Sequence[Dict[str, Any]]) -> Tuple[int, int]:
    positives = sum(1 for p in pairs if p["label"] == 1)
    return positives, len(pairs) - positives


def _matrix(pairs: Sequence[Dict[str, Any]]) -> Tuple[List[List[float]], List[int]]:
    X = [feature_row(**p["features"]) for p in pairs]
    y = [int(p["label"]) for p in pairs]
    return X, y


def _auc(labels: Sequence[int], scores: Sequence[float]) -> Optional[float]:
    if len(set(labels)) < 2:
        return None
    from sklearn.metrics import roc_auc_score
    return float(roc_auc_score(labels, scores))


# ------------------------------------------------------------------
# Training and the quality gate
# ------------------------------------------------------------------
def _fit(train: Sequence[Dict[str, Any]], c_value: float, seed: int):
    from sklearn.linear_model import LogisticRegression
    X, y = _matrix(train)
    model = LogisticRegression(C=c_value, class_weight="balanced", max_iter=2000, random_state=seed)
    model.fit(X, y)
    return model


def gate_on_test(model: Any, test: Sequence[Dict[str, Any]], seed: int = 42) -> Dict[str, Any]:
    """
    Does blending the ranker in (0.8 hybrid + 0.2 ranker) beat the hybrid score alone on the
    held-out TEST cases? Measured with AUC and a bootstrap 95% interval of the difference.
    """
    base = {
        "evaluated_on": "held-out test cases (latest part of the data)",
        "rule": (
            f"AUC of 0.8*hybrid + 0.2*ranker must beat the hybrid alone, with the 95% bootstrap "
            f"interval of the difference entirely above 0, using at least {TEST_MIN_EACH_CLASS} positive "
            f"and {TEST_MIN_EACH_CLASS} negative test cases."
        ),
    }
    positives, negatives = class_counts(test)
    base.update({"n_test": len(test), "n_test_positive": positives, "n_test_negative": negatives})
    if positives < TEST_MIN_EACH_CLASS or negatives < TEST_MIN_EACH_CLASS:
        return {**base, "passed": False,
                "reasons": [f"too few test cases ({positives} positive, {negatives} negative; need {TEST_MIN_EACH_CLASS} of each)"]}

    X, y = _matrix(test)
    ranker_scores = [float(p) * 100.0 for p in model.predict_proba(X)[:, 1]]
    hybrid_scores = [float(p["hybrid"]) for p in test]
    blend_scores = [(1 - BLEND_RANKER_WEIGHT) * h + BLEND_RANKER_WEIGHT * r for h, r in zip(hybrid_scores, ranker_scores)]

    auc_hybrid, auc_blend, auc_ranker = _auc(y, hybrid_scores), _auc(y, blend_scores), _auc(y, ranker_scores)

    rng = random.Random(seed)
    n = len(y)
    differences: List[float] = []
    for _ in range(BOOTSTRAPS):
        index = [rng.randrange(n) for _ in range(n)]
        labels = [y[i] for i in index]
        a = _auc(labels, [blend_scores[i] for i in index])
        b = _auc(labels, [hybrid_scores[i] for i in index])
        if a is not None and b is not None:
            differences.append(a - b)

    if len(differences) < BOOTSTRAPS // 2:
        return {**base, "passed": False, "reasons": ["could not estimate the uncertainty (too few usable resamples)"]}

    differences.sort()
    low = differences[int(0.025 * len(differences))]
    high = differences[min(len(differences) - 1, int(0.975 * len(differences)))]
    passed = low > 0
    reasons = [
        f"AUC hybrid {auc_hybrid:.3f}, blend {auc_blend:.3f}, ranker alone {auc_ranker:.3f}; "
        f"difference (blend - hybrid) 95% interval [{low:+.3f}, {high:+.3f}]"
    ]
    reasons.append("the interval is above 0: the ranker helps" if passed else "the interval includes 0 or below: no demonstrated benefit")
    return {**base, "passed": passed, "reasons": reasons, "auc_hybrid": round(auc_hybrid, 4),
            "auc_blend": round(auc_blend, 4), "auc_ranker_alone": round(auc_ranker, 4),
            "difference_ci95": [round(low, 4), round(high, 4)]}


def train_and_evaluate(
    pairs: Sequence[Dict[str, Any]],
    text_signal: str,
    seed: int = 42,
    data_origin: str = "real",
) -> Dict[str, Any]:
    """
    pairs: [{"student_id", "event_id", "label" (1/0), "ts", "features": {content, text_match},
            "hybrid": hybrid score WITHOUT feedback (0-100)}]
    Returns {"status": "refused"|"trained", "ranker": Ranker|None, "report": {...}}.
    """
    pairs = list(pairs)
    positives, negatives = class_counts(pairs)
    students = len({p["student_id"] for p in pairs})
    counts = {"pairs": len(pairs), "positives": positives, "negatives": negatives, "students": students}

    problems = []
    if len(pairs) < MIN_PAIRS:
        problems.append(f"only {len(pairs)} labelled pairs (need {MIN_PAIRS})")
    if positives < MIN_POSITIVES:
        problems.append(f"only {positives} positive pairs (need {MIN_POSITIVES})")
    if negatives < MIN_NEGATIVES:
        problems.append(f"only {negatives} negative pairs (need {MIN_NEGATIVES})")
    if students < MIN_STUDENTS:
        problems.append(f"only {students} students (need {MIN_STUDENTS})")
    if problems:
        return {"status": "refused", "ranker": None, "report": {"counts": counts, "reasons": problems}}

    train, val, test = temporal_split(pairs)
    for name, part in (("train", train), ("validation", val)):
        pos, neg = class_counts(part)
        if pos == 0 or neg == 0:
            return {"status": "refused", "ranker": None,
                    "report": {"counts": counts, "reasons": [f"the {name} part has only one class; need more varied data"]}}

    # choose the regularisation strength on the VALIDATION part only
    best_c, best_auc = C_GRID[0], -1.0
    for c_value in C_GRID:
        candidate = _fit(train, c_value, seed)
        X, y = _matrix(val)
        score = _auc(y, [float(p) for p in candidate.predict_proba(X)[:, 1]])
        if score is not None and score > best_auc:
            best_c, best_auc = c_value, score

    model = _fit(train, best_c, seed)
    gate = gate_on_test(model, test, seed)
    meta = {
        "data_origin": data_origin, "text_signal": text_signal, "feature_names": list(FEATURE_NAMES),
        "C": best_c, "seed": seed, "gate_passed": bool(gate["passed"]), "gate_reasons": list(gate["reasons"]),
    }
    report = {
        "counts": counts, "splits": {"train": len(train), "validation": len(val), "test": len(test)},
        "chosen_C": best_c, "validation_auc_ranker_alone": round(best_auc, 4) if best_auc >= 0 else None,
        "quality_gate": gate, "data_origin": data_origin, "text_signal": text_signal,
    }
    return {"status": "trained", "ranker": Ranker(model, text_signal, meta), "report": report}


# ------------------------------------------------------------------
# Saving and loading
# ------------------------------------------------------------------
def save_ranker(directory: str, ranker: Ranker, report: Dict[str, Any]) -> None:
    import joblib
    os.makedirs(directory, exist_ok=True)
    joblib.dump({"estimator": ranker.estimator, "text_signal": ranker.text_signal, "meta": ranker.meta},
                os.path.join(directory, MODEL_FILE))
    with open(os.path.join(directory, REPORT_FILE), "w", encoding="utf-8", newline="\n") as handle:
        json.dump(report, handle, indent=2, default=str)


def check_ranker_allowed(meta: Dict[str, Any], require_gate: bool = True, allow_synthetic: bool = False) -> Tuple[bool, str]:
    if require_gate:
        if "gate_passed" not in meta:
            return False, "ranker has no quality-gate record"
        if meta["gate_passed"] is not True:
            return False, "ranker failed its quality gate (" + "; ".join(map(str, meta.get("gate_reasons", []))) + ")"
    if meta.get("data_origin") != "real" and not allow_synthetic:
        return False, "ranker was not trained on real data"
    return True, "ok"


def _report_meta(directory: str) -> Optional[Dict[str, Any]]:
    """Gate and origin read from the small JSON report, so a blocked model never needs scikit-learn or joblib."""
    path = os.path.join(directory, REPORT_FILE)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            info = json.load(handle)
        gate = info.get("quality_gate") or {}
        meta: Dict[str, Any] = {"data_origin": info.get("data_origin")}
        if "passed" in gate:
            meta["gate_passed"] = gate["passed"]
            meta["gate_reasons"] = gate.get("reasons", [])
        return meta
    except Exception:
        return None


def load_ranker(directory: str, require_gate: bool = True, allow_synthetic: bool = False) -> Tuple[Optional[Ranker], str]:
    """(Ranker, "ok") or (None, reason). Never raises. joblib files are pickles: load only from your own folder."""
    try:
        model_path = os.path.join(directory, MODEL_FILE)
        if not os.path.isfile(model_path):
            return None, f"no ranker found in '{directory}'"

        early = _report_meta(directory)
        if early is not None:
            allowed, why = check_ranker_allowed(early, require_gate, allow_synthetic)
            if not allowed:
                return None, why

        import joblib
        data = joblib.load(model_path)
        meta = dict(data.get("meta") or {})
        allowed, why = check_ranker_allowed(meta, require_gate, allow_synthetic)
        if not allowed:
            return None, why
        return Ranker(data["estimator"], data["text_signal"], meta), "ok"
    except Exception as error:
        return None, f"could not load the ranker ({error})"


# ------------------------------------------------------------------
# Shared instance for the app
# ------------------------------------------------------------------
def _flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in ("1", "true", "yes", "on")


class _Holder:
    def __init__(self):
        self.lock = threading.Lock()
        self.attempted = False
        self.ranker: Optional[Ranker] = None
        self.reason = "not loaded yet"
        self.load_count = 0


_holder = _Holder()


def _load_into(holder: _Holder, directory: Optional[str] = None) -> None:
    holder.load_count += 1
    holder.ranker = None
    if _flag("DISABLE_RANKER"):
        holder.reason = "disabled by DISABLE_RANKER"
        return
    folder = directory or os.getenv("RANKER_DIR", DEFAULT_DIR)
    ranker, reason = load_ranker(
        folder, require_gate=not _flag("RANKER_ALLOW_UNGATED"), allow_synthetic=_flag("RANKER_ALLOW_SYNTHETIC"),
    )
    holder.ranker, holder.reason = ranker, reason


def get_active_ranker(directory: Optional[str] = None) -> Optional[Ranker]:
    """The ranker the engine may use, or None. Loaded once; never raises."""
    try:
        if not _holder.attempted:
            with _holder.lock:
                if not _holder.attempted:
                    _load_into(_holder, directory)
                    _holder.attempted = True
        return _holder.ranker
    except Exception as error:
        logger.warning("ranker could not be prepared: %s", error)
        return None


def ranker_status() -> Dict[str, Any]:
    get_active_ranker()
    return {"available": _holder.ranker is not None, "reason": _holder.reason,
            "text_signal": _holder.ranker.text_signal if _holder.ranker else None}


def reload_ranker() -> None:
    """Forget the loaded ranker (use after retraining)."""
    with _holder.lock:
        _holder.attempted = False
        _holder.ranker = None