"""
bert4rec_model.py
=================
PHASE C: a small, CONTENT-AWARE BERT4Rec-style model (PyTorch).

Why "content-aware"
-------------------
Events happen once and expire, so a classic BERT4Rec over event ids could
never score an event it has not seen. This model's tokens are stable event
PROPERTIES instead:

    input at each position  =  category embedding
                             + club (organizer) embedding
                             + action-type embedding      (view, save, register ...)
                             + position embedding

A brand-new event can therefore be scored through its category and club.

How it is trained (Cloze task, like BERT)
-----------------------------------------
Random positions (about 20%) have their category and club replaced by
[MASK]; their ACTION stays visible. The model must recover the hidden
category and club from the rest of the sequence (both directions, i.e. a
bidirectional transformer). Loss = cross-entropy over the category
vocabulary + cross-entropy over the club vocabulary.

How it is used
--------------
Append one more slot whose category and club are [MASK] and whose action is
"register" (or save / attend). The model then returns, for that slot,
a probability distribution over categories and over clubs: "given this
student's recent activity, what would they register for next?". Phase D
turns those two distributions into a score for each candidate event.

Honest limits
-------------
* With tiny data the model overfits easily; dropout is high and training
  stops early on a validation set.
* It predicts categories and clubs, not individual events.
* If it was trained on simulated data (see the checkpoint meta), it only
  knows the simulator's rules.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional, Tuple

import torch
from torch import nn
import torch.nn.functional as F

from bert4rec_data import MASK_ID, PAD_ID
from build_sequences import FIRST_FREE_ID

IGNORE_INDEX = -100
SPECIAL_LOGIT = -1e9      # special tokens can never be predicted


# ------------------------------------------------------------------
# Configuration
# ------------------------------------------------------------------
@dataclass
class Bert4RecConfig:
    n_actions: int
    n_categories: int
    n_organizers: int
    max_len: int = 30
    hidden: int = 64
    layers: int = 2
    heads: int = 2
    ff: int = 256
    dropout: float = 0.3

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Bert4RecConfig":
        return cls(**data)


# ------------------------------------------------------------------
# Model
# ------------------------------------------------------------------
class Bert4Rec(nn.Module):
    def __init__(self, cfg: Bert4RecConfig):
        super().__init__()
        if cfg.hidden % cfg.heads != 0:
            raise ValueError("hidden size must be divisible by the number of heads")
        self.cfg = cfg

        self.cat_emb = nn.Embedding(cfg.n_categories, cfg.hidden, padding_idx=PAD_ID)
        self.org_emb = nn.Embedding(cfg.n_organizers, cfg.hidden, padding_idx=PAD_ID)
        self.act_emb = nn.Embedding(cfg.n_actions, cfg.hidden, padding_idx=PAD_ID)
        self.pos_emb = nn.Embedding(cfg.max_len, cfg.hidden)
        self.embed_norm = nn.LayerNorm(cfg.hidden)
        self.embed_drop = nn.Dropout(cfg.dropout)

        layer = nn.TransformerEncoderLayer(
            d_model=cfg.hidden,
            nhead=cfg.heads,
            dim_feedforward=cfg.ff,
            dropout=cfg.dropout,
            activation="gelu",
            batch_first=True,
            norm_first=True,
        )
        # Bidirectional: no causal mask is ever passed.
        self.encoder = nn.TransformerEncoder(layer, num_layers=cfg.layers, enable_nested_tensor=False)
        self.out_norm = nn.LayerNorm(cfg.hidden)

        self.cat_head = nn.Linear(cfg.hidden, cfg.n_categories)
        self.org_head = nn.Linear(cfg.hidden, cfg.n_organizers)

        self._init_weights()

    def _init_weights(self) -> None:
        for module in self.modules():
            if isinstance(module, nn.Embedding):
                nn.init.normal_(module.weight, mean=0.0, std=0.02)
                if module.padding_idx is not None:
                    with torch.no_grad():
                        module.weight[module.padding_idx].zero_()

    def forward(self, cat: torch.Tensor, org: torch.Tensor, act: torch.Tensor) -> Tuple[torch.Tensor, torch.Tensor]:
        """
        cat, org, act: LongTensor [batch, length]  (length <= max_len).
        Returns category logits [batch, length, n_categories] and
        club logits [batch, length, n_organizers].
        Padding is recognised by act == [PAD] and is ignored by attention.
        """
        _, length = act.shape
        if length > self.cfg.max_len:
            raise ValueError(f"sequence length {length} exceeds max_len {self.cfg.max_len}")

        positions = torch.arange(length, device=act.device).unsqueeze(0)
        x = self.cat_emb(cat) + self.org_emb(org) + self.act_emb(act) + self.pos_emb(positions)
        x = self.embed_drop(self.embed_norm(x))

        padding = act == PAD_ID
        hidden = self.encoder(x, src_key_padding_mask=padding)
        hidden = self.out_norm(hidden)
        return self.cat_head(hidden), self.org_head(hidden)


# ------------------------------------------------------------------
# Special tokens can never be predicted
# ------------------------------------------------------------------
def mask_special_logits(logits: torch.Tensor) -> torch.Tensor:
    bias = torch.zeros(logits.shape[-1], dtype=logits.dtype, device=logits.device)
    bias[:FIRST_FREE_ID] = SPECIAL_LOGIT
    return logits + bias


# ------------------------------------------------------------------
# Cloze masking
# ------------------------------------------------------------------
def apply_cloze_mask(
    cat: torch.Tensor,
    org: torch.Tensor,
    act: torch.Tensor,
    mask_prob: float = 0.2,
    mask_last_prob: float = 0.25,
    generator: Optional[torch.Generator] = None,
    groups: Optional[torch.Tensor] = None,
) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor]:
    """
    Hide the category and club of random positions.

    groups (optional, LongTensor like `act`, 0 = padding): which interactions
    belong to the same EVENT. When given, whole events are hidden together
    (a student's view, save, register and attend of one event are all masked
    at once). This matters: if only the "register" were hidden, the model could
    simply copy the category and club from the "view" next to it, a shortcut
    that does not exist when recommending a new event.

    * Only real positions (action != [PAD]) can be masked.
    * Every row gets at least one masked position.
    * With probability `mask_last_prob` the NEWEST position is masked too,
      which imitates what happens at prediction time (the last slot is
      always the question).
    * The ACTION of a masked position stays visible.

    Returns (input_cat, input_org, label_cat, label_org). Labels are -100
    (ignored by the loss) everywhere that was not masked.
    """
    valid = act != PAD_ID
    batch = act.shape[0]

    if groups is None:
        noise = torch.rand(act.shape, generator=generator)
    else:
        # one random number per EVENT, shared by all of its interactions
        group_noise = torch.rand((batch, int(groups.max().item()) + 1), generator=generator)
        noise = group_noise.gather(1, groups)
    masked = (noise < mask_prob) & valid

    if mask_last_prob > 0:
        newest = (torch.rand(batch, generator=generator) < mask_last_prob) & valid[:, -1]
        if groups is None:
            masked[:, -1] = masked[:, -1] | newest
        else:
            last_group = groups[:, -1:]
            masked = masked | ((groups == last_group) & (last_group != 0) & newest.unsqueeze(1))

    empty = ~masked.any(dim=1)
    if bool(empty.any()):
        scores = torch.where(valid, noise, torch.full_like(noise, 2.0))
        choice = scores.argmin(dim=1)
        rows = torch.nonzero(empty).squeeze(1)
        if groups is None:
            masked[rows, choice[rows]] = True
        else:
            chosen = groups[rows, choice[rows]].unsqueeze(1)
            masked[rows] = masked[rows] | ((groups[rows] == chosen) & valid[rows])

    ignore = torch.full_like(cat, IGNORE_INDEX)
    mask_token = torch.full_like(cat, MASK_ID)
    label_cat = torch.where(masked, cat, ignore)
    label_org = torch.where(masked, org, ignore)
    input_cat = torch.where(masked, mask_token, cat)
    input_org = torch.where(masked, mask_token, org)
    return input_cat, input_org, label_cat, label_org


def cloze_loss(
    cat_logits: torch.Tensor,
    org_logits: torch.Tensor,
    label_cat: torch.Tensor,
    label_org: torch.Tensor,
) -> torch.Tensor:
    cat_loss = F.cross_entropy(
        mask_special_logits(cat_logits).reshape(-1, cat_logits.shape[-1]),
        label_cat.reshape(-1),
        ignore_index=IGNORE_INDEX,
    )
    org_loss = F.cross_entropy(
        mask_special_logits(org_logits).reshape(-1, org_logits.shape[-1]),
        label_org.reshape(-1),
        ignore_index=IGNORE_INDEX,
    )
    return cat_loss + org_loss


# ------------------------------------------------------------------
# Prediction
# ------------------------------------------------------------------
@torch.no_grad()
def predict_next_log_probs(
    model: Bert4Rec,
    cat: torch.Tensor,
    org: torch.Tensor,
    act: torch.Tensor,
) -> Tuple[torch.Tensor, torch.Tensor]:
    """
    For contexts whose LAST slot is the question ([MASK] category and club,
    the action being asked about), return log-probabilities over categories
    and clubs: shapes [batch, n_categories] and [batch, n_organizers].
    Special tokens get probability ~0.
    """
    was_training = model.training
    model.eval()
    try:
        cat_logits, org_logits = model(cat, org, act)
        cat_lp = F.log_softmax(mask_special_logits(cat_logits[:, -1, :]), dim=-1)
        org_lp = F.log_softmax(mask_special_logits(org_logits[:, -1, :]), dim=-1)
    finally:
        model.train(was_training)
    return cat_lp, org_lp


# ------------------------------------------------------------------
# Checkpoints (weights + config + plain-data meta; loads with weights_only=True)
# ------------------------------------------------------------------
def save_checkpoint(path: str, model: Bert4Rec, meta: Dict[str, Any]) -> None:
    torch.save(
        {"config": model.cfg.to_dict(), "state_dict": model.state_dict(), "meta": meta},
        path,
    )


def load_checkpoint(path: str) -> Tuple[Bert4Rec, Dict[str, Any]]:
    checkpoint = torch.load(path, map_location="cpu", weights_only=True)
    model = Bert4Rec(Bert4RecConfig.from_dict(checkpoint["config"]))
    model.load_state_dict(checkpoint["state_dict"])
    model.eval()
    return model, checkpoint.get("meta", {})