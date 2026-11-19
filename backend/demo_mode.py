"""
demo_mode.py
============
Tells the dashboard when a DEMO-ONLY learned signal is switched on, so the UI
and the response can say so plainly.

A demo is active only while a developer override of sequential_recommender is
lifting a block AND the model really loaded:
    SEQUENTIAL_ALLOW_SIMULATED=true   (model trained on simulated data)
    SEQUENTIAL_ALLOW_UNGATED=true     (model failed or lacks its quality gate)
With neither override set, get_demo_info() returns None and nothing about the
app's behaviour or responses changes.

Never raises. No database. No PyTorch import of its own.
"""
from typing import Any, Dict, Optional

SIMULATED_MESSAGE = "Demo mode: the sequential signal comes from a model trained on synthetic data."
UNGATED_MESSAGE = "Demo mode: the sequential signal comes from a model that did not pass its quality gate."
DETAIL = ("This is a demonstration of how the system works. The model was not trained or "
          "validated on real students, so its suggestions are not evidence of real accuracy.")


def demo_info(status: Any) -> Optional[Dict[str, Any]]:
    """status = sequential_recommender.status(). Returns a small dict, or None when no demo is active."""
    try:
        if not isinstance(status, dict) or status.get("available") is not True:
            return None
        simulated = status.get("simulated_override_active") is True
        ungated = status.get("gate_override_active") is True
        if not (simulated or ungated):
            return None
        return {
            "active": True,
            "trained_on_simulated": status.get("trained_on_simulated") is True,
            "quality_gate_passed": status.get("quality_gate_passed"),
            "message": SIMULATED_MESSAGE if simulated else UNGATED_MESSAGE,
            "detail": DETAIL,
        }
    except Exception:
        return None


def get_demo_info() -> Optional[Dict[str, Any]]:
    """The live answer, or None. Safe to call on every request."""
    try:
        import sequential_recommender as sequential
        return demo_info(sequential.status())
    except Exception:
        return None