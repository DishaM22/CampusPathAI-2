"""
demo_check.py   READ-ONLY: no database, nothing written.

Asks the trained model what category a student would register for next, for
three made-up students with different recent activity, and shows how much the
answers differ. If the three answers are almost identical, the model is mostly
repeating overall popularity and a "recommendations change with my activity"
demo would not show anything real.

    python demo_check.py --model-dir models\\bert4rec_v2

This loads the model with the developer overrides ON (gate and simulated-data
blocks lifted) FOR THIS SCRIPT ONLY. It does not change what the app does.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import sequential_recommender as sr

PERSONAS = {
    "tech (workshop/hackathon, AI Club + Coding Club)": [
        ("Workshop", "AI Club"), ("Hackathon", "Coding Club"), ("Technical", "AI Club")],
    "arts and sports (Cultural Club, Sports Club)": [
        ("Cultural", "Cultural Club"), ("Sports", "Sports Club"), ("Cultural", "Music Club")],
    "career-minded (Career, Industry Visit, Placement Cell)": [
        ("Career", "Placement Cell"), ("Industry Visit", "Placement Cell"), ("Career", "Entrepreneurship Cell")],
}


def persona_rows(steps):
    """view -> save -> register for each event, oldest first."""
    t0 = datetime(2026, 11, 1, 10, 0, tzinfo=timezone.utc)
    rows = []
    for i, (category, organizer) in enumerate(steps):
        for j, action in enumerate(("view", "save", "register")):
            rows.append({
                "student_id": "demo", "event_id": f"demo-e{i}", "action": action,
                "timestamp": t0 + timedelta(days=i * 3, minutes=j * 10),
                "category": category, "organizer": organizer,
            })
    return rows


def category_distribution(rec, rows, categories):
    """P(category) for each category, using candidates with an unknown club so only the category counts."""
    candidates = [{"_id": f"c{i}", "category": c, "organizer": "zz unknown club"}
                  for i, c in enumerate(categories)]
    scores = rec.score_events(rows, candidates)
    return {c: scores.get(f"c{i}", 0.0) for i, c in enumerate(categories)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-dir", default=os.path.join("models", "bert4rec_v2"))
    a = ap.parse_args()

    rec = sr.SequentialRecommender(model_dir=a.model_dir, require_gate=False, allow_simulated=True)
    st = rec.status()
    print("model status:", json.dumps({k: st[k] for k in ("available", "reason", "trained_on_simulated", "quality_gate_passed")}))
    if not st["available"]:
        print("The model could not be loaded, so there is nothing to check.")
        return 1

    categories = [c for c in rec._vocab["categories"] if c not in rec._vocab["special_tokens"]]
    dists = {}
    for name, steps in PERSONAS.items():
        dists[name] = category_distribution(rec, persona_rows(steps), categories)
        total = sum(dists[name].values()) or 1.0
        top = sorted(dists[name].items(), key=lambda kv: -kv[1])[:4]
        print(f"\n{name}")
        print("  top categories:", ", ".join(f"{c} {v / total:.2f}" for c, v in top))

    names = list(dists)
    print("\nHow different are the answers? (0 = identical, 1 = completely different)")
    worst = 1.0
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a_tot = sum(dists[names[i]].values()) or 1.0
            b_tot = sum(dists[names[j]].values()) or 1.0
            tv = 0.5 * sum(abs(dists[names[i]][c] / a_tot - dists[names[j]][c] / b_tot) for c in categories)
            worst = min(worst, tv)
            print(f"  {names[i].split(' (')[0]} vs {names[j].split(' (')[0]}: {tv:.2f}")
    print("\nRough guide (not a statistical test): below about 0.10 the model barely reacts to history;"
          " above about 0.25 a live demo should show a visible change.")
    return 0


if __name__ == "__main__":
    sys.exit(main())