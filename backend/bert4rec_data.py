"""
bert4rec_data.py
================
PHASE C helpers that need NO PyTorch: they turn the files written by
build_sequences.py into padded id lists the model can read.

Keeping this separate means the data handling can be tested anywhere, and
Phase D (the live recommender) can reuse exactly the same encoding and
padding as training, so the two can never drift apart.

Conventions
-----------
* Special ids (fixed by build_sequences.py): [PAD]=0, [MASK]=1, [UNK]=2.
* Every sequence is LEFT-padded to max_len, so the newest interaction is
  always the last position. At prediction time the last position is the
  slot being asked about: its category and club are [MASK], and its
  action is the action we are asking about ("what would this student
  REGISTER for next?").
* An interaction has three parts: action, category, organizer (club).
  Event ids are never given to the model.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Sequence

from build_sequences import (
    FIRST_FREE_ID,
    MASK,
    PAD,
    SPECIAL_TOKENS,
    UNK,
    encode_sequence,
)

PAD_ID = SPECIAL_TOKENS[PAD]
MASK_ID = SPECIAL_TOKENS[MASK]
UNK_ID = SPECIAL_TOKENS[UNK]

# Actions that mean the student wants the event. Used to report how well
# the model predicts the interactions that matter for recommendation.
POSITIVE_ACTIONS = ("save", "register", "attend")


# ------------------------------------------------------------------
# Files
# ------------------------------------------------------------------
def load_jsonl(path: str) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def load_vocab(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


# ------------------------------------------------------------------
# Encoding and padding
# ------------------------------------------------------------------
def encode_packed(packed: Dict[str, List[Any]], vocab: Dict[str, Any]) -> Dict[str, List[int]]:
    """Strings -> ids (anything unseen in training becomes [UNK])."""
    return encode_sequence(packed, vocab)


def action_id(vocab: Dict[str, Any], name: str) -> int:
    return vocab["actions"].get(str(name), UNK_ID)


def left_pad(ids: Sequence[int], length: int, pad: int = PAD_ID) -> List[int]:
    """Keep the NEWEST `length` items and pad on the left."""
    ids = list(ids)[-length:] if length > 0 else []
    return [pad] * (length - len(ids)) + ids


def build_context(history: Dict[str, List[int]], next_action: int, max_len: int) -> Dict[str, List[int]]:
    """
    history: encoded {"actions", "categories", "organizers"} (oldest first).
    Returns three lists of length max_len. The last slot is the question:
    category and club are [MASK], action is `next_action`.
    Only the newest max_len - 1 history items fit.
    """
    keep = max_len - 1
    cats = list(history["categories"])[-keep:] if keep > 0 else []
    orgs = list(history["organizers"])[-keep:] if keep > 0 else []
    acts = list(history["actions"])[-keep:] if keep > 0 else []
    return {
        "cat": left_pad(cats + [MASK_ID], max_len),
        "org": left_pad(orgs + [MASK_ID], max_len),
        "act": left_pad(acts + [next_action], max_len),
    }


# ------------------------------------------------------------------
# Training examples
# ------------------------------------------------------------------
def _group_ids(event_ids: Sequence[Any]) -> List[int]:
    """['a','a','b','a'] -> [1,1,2,1]  (numbered by first appearance, per sequence)."""
    seen: Dict[Any, int] = {}
    out: List[int] = []
    for event_id in event_ids:
        if event_id not in seen:
            seen[event_id] = len(seen) + 1
        out.append(seen[event_id])
    return out


def build_train_examples(records: Sequence[Dict[str, Any]], vocab: Dict[str, Any], max_len: int) -> List[Dict[str, Any]]:
    """One example per training window. The trainer masks tokens itself."""
    examples = []
    for record in records:
        encoded = encode_packed(record["sequence"], vocab)
        if len(encoded["actions"]) < 2:
            continue
        examples.append({
            "student_id": record.get("student_id", ""),
            "cat": left_pad(encoded["categories"], max_len),
            "org": left_pad(encoded["organizers"], max_len),
            "act": left_pad(encoded["actions"], max_len),
            # which interactions belong to the SAME event (1, 2, 3 ... per window; 0 = padding).
            # Lets the trainer hide a whole event at once (see apply_cloze_mask).
            "evt": left_pad(_group_ids(record["sequence"].get("event_ids", [])), max_len),
        })
    return examples


# ------------------------------------------------------------------
# Validation / test examples
# ------------------------------------------------------------------
def sequences_to_questions(records: Sequence[Dict[str, Any]], vocab: Dict[str, Any], max_len: int) -> List[Dict[str, Any]]:
    """
    Turns TRAINING sequences into recommendation questions (the same kind the
    evaluation asks): for every save / register / attend, the history before it
    with that event's own earlier steps removed. Used only to count transitions
    for a simple baseline; the model itself never sees these.
    """
    pseudo = []
    for record in records:
        sequence = record["sequence"]
        for j in range(1, len(sequence["actions"])):
            if sequence["actions"][j] in POSITIVE_ACTIONS:
                pseudo.append({
                    "student_id": record.get("student_id", ""),
                    "history": {key: values[:j] for key, values in sequence.items()},
                    "targets": {key: values[j:j + 1] for key, values in sequence.items()},
                })
    return build_eval_examples(pseudo, vocab, max_len, exclude_same_event=True)


def build_eval_examples(
    records: Sequence[Dict[str, Any]],
    vocab: Dict[str, Any],
    max_len: int,
    exclude_same_event: bool = False,
) -> List[Dict[str, Any]]:
    """
    For every held-out interaction (a "target"), build the question the
    live recommender will face: given the history, what category and club
    come next, when the next action is <that interaction's action>?

    The history grows with the TRUE earlier targets (teacher forcing), which
    is legitimate because they really happened before the one being asked.
    Targets whose category or club was never seen in training keep their
    UNK label and are flagged, so metrics can leave them out and report
    how many there were.

    exclude_same_event=True answers the REAL recommendation question: the
    student's earlier steps on the SAME event (for example the "view" that
    came just before a "register") are removed from the history, because at
    recommendation time the student has not seen that event yet. Without this,
    "copy the previous interaction" looks almost perfect, since a student's
    view, save, register and attend of one event all share its category and club.
    Questions left with no history at all are skipped.
    """
    examples = []
    for record in records:
        history = encode_packed(record["history"], vocab)
        targets = encode_packed(record["targets"], vocab)
        target_actions = record["targets"]["actions"]
        history_events = list(record["history"]["event_ids"])
        target_events = list(record["targets"]["event_ids"])

        for j in range(len(targets["actions"])):
            grown = {key: history[key] + targets[key][:j] for key in history}
            if exclude_same_event:
                this_event = target_events[j]
                keep = [i for i, event_id in enumerate(history_events + target_events[:j]) if event_id != this_event]
                grown = {key: [values[i] for i in keep] for key, values in grown.items()}
                if not grown["actions"]:
                    continue
            context = build_context(grown, targets["actions"][j], max_len)
            label_cat = targets["categories"][j]
            label_org = targets["organizers"][j]
            examples.append({
                **context,
                "label_cat": label_cat,
                "label_org": label_org,
                "cat_known": label_cat != UNK_ID,
                "org_known": label_org != UNK_ID,
                "action": target_actions[j],
                "positive": target_actions[j] in POSITIVE_ACTIONS,
                "student_id": record.get("student_id", ""),
                "event_id": record["targets"]["event_ids"][j],
                "same_event_hidden": exclude_same_event,
            })
    return examples