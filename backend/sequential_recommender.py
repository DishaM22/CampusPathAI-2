"""
sequential_recommender.py
=========================
PHASE D: the module the live recommendation engine calls to get a
"sequential" score for candidate events.

    scores = score_events(student_interaction_rows, candidate_event_documents)
    # -> {event_id: score between 0 and 1}   (an EMPTY dict = "signal not available")

Contract with the engine
------------------------
* An empty dict means "this signal is unavailable". The engine then ignores it
  and renormalises its other weights, exactly as it does for any missing
  signal today. Nothing else changes.
* It NEVER raises. Any problem (no model, torch cannot load, bad input) is
  logged and returns {}.
* The model is loaded ONCE, lazily, on the first request, not at import time.
  Importing this module does not import PyTorch.

When it returns nothing (on purpose)
------------------------------------
1. DISABLE_SEQUENTIAL_MODEL=true.
2. The model folder / files are missing or cannot be loaded.
3. The checkpoint has no quality-gate record, or the gate FAILED
   (train_bert4rec.py decides this on held-out data; see its report).
4. The model was trained on SIMULATED (or unknown) data. A model that only
   knows the simulator's rules must never influence real students, even if
   it passes the gate (it would pass by construction).
5. The student has fewer than 3 usable interactions.

Developer switches (never for production)
-----------------------------------------
    SEQUENTIAL_ALLOW_UNGATED=true     use a model that failed / lacks the gate
    SEQUENTIAL_ALLOW_SIMULATED=true   use a model trained on simulated data
    SEQUENTIAL_MODEL_DIR=<folder>     default: models/bert4rec
status() always reports when either override is active.

How a candidate event is scored
-------------------------------
The model looks at the student's recent interactions and answers "what
category and club would this student register for next?" as two probability
distributions. An event is scored through its category and club:

    both known  -> geometric mean of P(category) and P(club)
    one known   -> that one probability
    neither     -> no score (event left out)

"Known" means the category / club text was seen when the model was trained;
a brand-new club or category is not scored through that part. Scores are raw
probabilities; Phase E turns them into a 0-100 scale per request.
"""
from __future__ import annotations

import json
import logging
import math
import os
import threading
from typing import Any, Dict, List, Optional, Sequence, Tuple

import bert4rec_data as bd
from build_sequences import SPECIAL_TOKENS, norm_text, pack, prepare_items

logger = logging.getLogger(__name__)

DEFAULT_MODEL_DIR = os.path.join("models", "bert4rec")
MIN_HISTORY = 3
DEFAULT_NEXT_ACTION = "register"


# ------------------------------------------------------------------
# Small pure helpers (no PyTorch)
# ------------------------------------------------------------------
def _flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in ("1", "true", "yes", "on")


def vocab_actions(vocab: Dict[str, Any]) -> List[str]:
    return [a for a in vocab["actions"] if a not in SPECIAL_TOKENS]


def history_context(
    rows: Optional[Sequence[Dict[str, Any]]],
    vocab: Dict[str, Any],
    *,
    min_history: int = MIN_HISTORY,
    next_action: str = DEFAULT_NEXT_ACTION,
) -> Tuple[Optional[Dict[str, List[int]]], str]:
    """
    One student's interaction rows -> the padded model input (or None and the reason).
    Simulated rows are ignored. Rows are sorted by time and normalised exactly
    like the training data (build_sequences.make_item), so text matches the vocabulary.
    """
    clean = []
    for row in rows or []:
        if isinstance(row, dict) and not row.get("simulated"):
            clean.append({**row, "student_id": "student"})        # all rows belong to ONE student

    by_student, _ = prepare_items(clean, vocab_actions(vocab))
    items = by_student.get("student", [])
    if len(items) < min_history:
        return None, f"only {len(items)} usable interactions (need {min_history})"

    max_len = int(vocab["max_len"])
    encoded = bd.encode_packed(pack(items[-(max_len - 1):]), vocab)
    return bd.build_context(encoded, bd.action_id(vocab, next_action), max_len), "ok"


def candidate_key(event: Any, vocab: Dict[str, Any]) -> Optional[Tuple[str, int, int]]:
    """event document -> (event_id, category_id, club_id); unseen text becomes [UNK]."""
    if not isinstance(event, dict):
        return None
    event_id = event.get("_id", event.get("id"))
    if event_id is None:
        return None
    category = event.get("category")
    if isinstance(category, (list, tuple)):
        category = category[0] if category else ""
    unk = bd.UNK_ID
    return (
        str(event_id),
        vocab["categories"].get(norm_text(category), unk),
        vocab["organizers"].get(norm_text(event.get("organizer")), unk),
    )


def check_model_allowed(meta: Dict[str, Any], *, require_gate: bool = True, allow_simulated: bool = False) -> Tuple[bool, str]:
    """May this checkpoint influence live recommendations?"""
    if require_gate:
        if "quality_gate_passed" not in meta:
            return False, "checkpoint has no quality-gate record (retrain with the current train_bert4rec.py)"
        if meta["quality_gate_passed"] is not True:
            reasons = "; ".join(str(r) for r in meta.get("quality_gate_reasons", [])) or "no reason recorded"
            return False, f"model failed the quality gate ({reasons})"
    if meta.get("trained_on_simulated") is not False and not allow_simulated:
        return False, "model was trained on simulated (or unknown) data and is never used for real recommendations"
    return True, "ok"


def read_report_meta(model_dir: str) -> Optional[Dict[str, Any]]:
    """
    The quality gate and data origin as written to train_report.json, read WITHOUT PyTorch.
    Lets a model that is blocked anyway be rejected before PyTorch is imported. Only ever used
    to BLOCK: a model is allowed only after its checkpoint's own record says so.
    """
    path = os.path.join(model_dir, "train_report.json")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            info = json.load(handle)
        meta: Dict[str, Any] = {
            "trained_on_simulated": info.get("trained_on_simulated"),
            "trained_at": info.get("trained_at"),
        }
        gate = info.get("quality_gate")
        if isinstance(gate, dict) and "passed" in gate:
            meta["quality_gate_passed"] = gate["passed"]
            meta["quality_gate_reasons"] = gate.get("reasons", [])
        return meta
    except Exception:
        return None


# ------------------------------------------------------------------
# The recommender
# ------------------------------------------------------------------
class SequentialRecommender:
    def __init__(
        self,
        model_dir: Optional[str] = None,
        *,
        require_gate: Optional[bool] = None,
        allow_simulated: Optional[bool] = None,
        min_history: int = MIN_HISTORY,
        next_action: str = DEFAULT_NEXT_ACTION,
    ):
        self.model_dir = model_dir or os.getenv("SEQUENTIAL_MODEL_DIR", DEFAULT_MODEL_DIR)
        self.require_gate = (not _flag("SEQUENTIAL_ALLOW_UNGATED")) if require_gate is None else require_gate
        self.allow_simulated = _flag("SEQUENTIAL_ALLOW_SIMULATED") if allow_simulated is None else allow_simulated
        self.min_history = min_history
        self.next_action = next_action

        self._lock = threading.Lock()
        self._attempted = False
        self._model = None
        self._torch = None
        self._predict = None
        self._vocab: Optional[Dict[str, Any]] = None
        self._meta: Dict[str, Any] = {}
        self._reason = "not loaded yet"
        self.load_count = 0                      # how many times the files were read (should stay at 1)

    # ---------------------------------------------------------- loading
    def _load(self) -> None:
        self.load_count += 1
        self._model = None
        self._reason = "ok"

        if _flag("DISABLE_SEQUENTIAL_MODEL"):
            self._reason = "disabled by DISABLE_SEQUENTIAL_MODEL"
            return

        model_path = os.path.join(self.model_dir, "model.pt")
        vocab_path = os.path.join(self.model_dir, "vocab.json")
        if not (os.path.isfile(model_path) and os.path.isfile(vocab_path)):
            self._reason = f"model files not found in '{self.model_dir}'"
            return

        # A model that is blocked anyway is rejected here, before PyTorch is imported.
        report_meta = read_report_meta(self.model_dir)
        if report_meta is not None:
            allowed, why = check_model_allowed(
                report_meta, require_gate=self.require_gate, allow_simulated=self.allow_simulated,
            )
            if not allowed:
                self._meta = report_meta
                self._reason = why
                return

        try:
            import torch
            import bert4rec_model as bm
        except Exception as error:               # e.g. blocked PyTorch DLLs
            self._reason = f"PyTorch could not be imported ({error})"
            return

        try:
            with open(vocab_path, "r", encoding="utf-8") as handle:
                vocab = json.load(handle)
            model, meta = bm.load_checkpoint(model_path)
        except Exception as error:
            self._reason = f"could not load the model ({error})"
            return

        self._meta = dict(meta or {})
        allowed, why = check_model_allowed(
            self._meta, require_gate=self.require_gate, allow_simulated=self.allow_simulated,
        )
        if not allowed:
            self._reason = why
            return

        self._torch, self._predict, self._vocab, self._model = torch, bm.predict_next_log_probs, vocab, model
        self._reason = "ok"

    def _ensure_loaded(self) -> None:
        if self._attempted:
            return
        with self._lock:
            if not self._attempted:
                try:
                    self._load()
                except Exception as error:       # never let loading break a request
                    self._model = None
                    self._reason = f"unexpected error while loading ({error})"
                    logger.warning("sequential model load failed: %s", error)
                self._attempted = True

    def reload(self) -> None:
        """Forget what was loaded (use after retraining). The next request loads again."""
        with self._lock:
            self._attempted = False
            self._model = None

    # ---------------------------------------------------------- public API
    @property
    def available(self) -> bool:
        self._ensure_loaded()
        return self._model is not None

    def status(self) -> Dict[str, Any]:
        self._ensure_loaded()
        meta = self._meta
        return {
            "available": self._model is not None,
            "reason": self._reason,
            "model_dir": self.model_dir,
            "trained_on_simulated": meta.get("trained_on_simulated"),
            "quality_gate_passed": meta.get("quality_gate_passed"),
            "trained_at": meta.get("trained_at"),
            "gate_override_active": (not self.require_gate) and meta.get("quality_gate_passed") is not True,
            "simulated_override_active": self.allow_simulated and meta.get("trained_on_simulated") is not False,
        }

    def score_events(
        self,
        history_rows: Optional[Sequence[Dict[str, Any]]],
        candidate_events: Optional[Sequence[Dict[str, Any]]],
    ) -> Dict[str, float]:
        """{event_id: score in (0, 1]}. Empty dict = signal unavailable. Never raises."""
        try:
            self._ensure_loaded()
            if self._model is None or not candidate_events:
                return {}

            context, _why = history_context(
                history_rows, self._vocab, min_history=self.min_history, next_action=self.next_action,
            )
            if context is None:
                return {}

            torch = self._torch
            cat = torch.tensor([context["cat"]], dtype=torch.long)
            org = torch.tensor([context["org"]], dtype=torch.long)
            act = torch.tensor([context["act"]], dtype=torch.long)
            cat_log_probs, org_log_probs = self._predict(self._model, cat, org, act)
            cat_probs = cat_log_probs[0].exp().tolist()
            org_probs = org_log_probs[0].exp().tolist()

            scores: Dict[str, float] = {}
            for event in candidate_events:
                key = candidate_key(event, self._vocab)
                if key is None:
                    continue
                event_id, category_id, club_id = key
                known = []
                if category_id != bd.UNK_ID and category_id < len(cat_probs):
                    known.append(cat_probs[category_id])
                if club_id != bd.UNK_ID and club_id < len(org_probs):
                    known.append(org_probs[club_id])
                if known:
                    scores[event_id] = float(math.prod(known) ** (1.0 / len(known)))
            return scores
        except Exception as error:
            logger.warning("sequential scoring failed: %s", error)
            return {}


# ------------------------------------------------------------------
# Shared instance for the app
# ------------------------------------------------------------------
_default: Optional[SequentialRecommender] = None
_default_lock = threading.Lock()


def get_recommender() -> SequentialRecommender:
    global _default
    with _default_lock:
        if _default is None:
            _default = SequentialRecommender()
        return _default


def score_events(history_rows, candidate_events) -> Dict[str, float]:
    return get_recommender().score_events(history_rows, candidate_events)


def status() -> Dict[str, Any]:
    return get_recommender().status()