"""
phase_i_export.py  (Phase I, step 1)   READ-ONLY: never writes to the database.

Run from the backend folder:
    python phase_i_export.py

Writes into  evaluation/ :
  relevance_sheet_template.csv   what raters fill in (anonymised, shuffled, NO engine scores)
  engine_scores_PRIVATE.csv      the engine's scores per pair; keep it away from raters

Every real student profile x every published event = one pair. The rater sheet
holds no names, e-mails or student ids (students are called S1, S2, ...).
Refuses to overwrite existing files unless --force is given, so a sheet that
has already been filled in cannot be lost by accident.
"""
import argparse
import csv
import os
import random
import sys

PUBLIC_EVENT_FILTER = {      # same rule main.py uses for what students may see
    "$or": [
        {"approval_status": "published"},
        {"approval_status": {"$exists": False}},
    ]
}

SHEET_COLUMNS = [
    "pair_id", "student", "year", "department", "interests", "skills",
    "career_goal", "preferred_domains", "event_title", "category",
    "event_mode", "date", "time", "organizer", "event_skills",
    "description", "relevance",
]
SCORE_COLUMNS = [
    "pair_id", "student_id", "event_id", "event_title", "category",
    "popularity", "profile_match", "keyword_only", "semantic_only",
    "hybrid_full", "signals_used",
]


def as_list(value):
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(v).strip() for v in value if str(v).strip()]
    return [p.strip() for p in str(value).split(",") if p.strip()]


def usable_profile(profile):
    return bool(
        as_list(profile.get("interests"))
        or as_list(profile.get("skills"))
        or as_list(profile.get("preferred_domains"))
        or str(profile.get("career_goal") or "").strip()
    )


def collect(db, seed=0):
    """Return (students, events, registered_counts). Read-only."""
    profiles = [p for p in db.profiles.find({}) if usable_profile(p)]
    profiles.sort(key=lambda p: str(p.get("student_id")))
    random.Random(seed).shuffle(profiles)          # labels do not follow signup order

    students = []
    for i, p in enumerate(profiles, 1):
        sid = str(p.get("student_id"))
        department = str(p.get("department") or "")
        if not department:                          # fall back to the student record, as main.py does
            try:
                from bson import ObjectId
                doc = db.students.find_one({"_id": ObjectId(sid)})
                department = str((doc or {}).get("department") or "")
            except Exception:
                department = ""
        students.append({
            "label": f"S{i}",
            "student_id": sid,
            "engine_student": {
                "student_id": sid,
                "department": department,
                "year": p.get("year", ""),
                "interests": p.get("interests", []),
                "skills": p.get("skills", []),
                "career_goal": p.get("career_goal", ""),
                "preferred_domains": p.get("preferred_domains", []),
            },
        })

    events = list(db.events.find(PUBLIC_EVENT_FILTER))
    events.sort(key=lambda e: str(e.get("_id")))

    counts = {}
    for row in db.event_registrations.aggregate([
        {"$match": {"status": {"$ne": "cancelled"}}},
        {"$group": {"_id": "$event_id", "count": {"$sum": 1}}},
    ]):
        if row.get("_id") is not None:
            counts[str(row["_id"])] = int(row.get("count", 0))
    return students, events, counts


def score_pairs(students, events, counts):
    """Engine scores per pair, with NO feedback, timetable or learned signals."""
    from recommendation_engine import score_event       # imported late: slow to load

    engine_events = []
    for e in events:
        d = dict(e)
        d["registered_count"] = counts.get(str(e.get("_id")), 0)
        engine_events.append(d)

    table = {}
    for st in students:
        for ev in engine_events:
            r = score_event(
                st["engine_student"], ev,
                feedback=None, timetable=None, mlp_model=None,
                sequential_score=None,
            )
            used = list(r.get("signals_used") or [])
            table[(st["label"], str(ev.get("_id")))] = {
                "popularity": counts.get(str(ev.get("_id")), 0),
                "profile_match": r.get("content_score"),
                "keyword_only": r.get("lexical_score"),
                "semantic_only": r.get("semantic_score") if "semantic" in used else "",
                "hybrid_full": r.get("matchScore"),
                "signals_used": "|".join(used),
            }
    return table


def join(values):
    return "; ".join(as_list(values))


def write_files(students, events, table, outdir, seed=0, force=False):
    os.makedirs(outdir, exist_ok=True)
    sheet_path = os.path.join(outdir, "relevance_sheet_template.csv")
    score_path = os.path.join(outdir, "engine_scores_PRIVATE.csv")
    for path in (sheet_path, score_path):
        if os.path.exists(path) and not force:
            raise SystemExit(f"{path} already exists. Use --force to replace it "
                             "(this deletes anything already filled in).")

    rng = random.Random(seed + 1)
    sheet_rows, score_rows = [], []
    for st in students:
        order = list(range(len(events)))
        rng.shuffle(order)                          # event numbers are arbitrary per student
        es = st["engine_student"]
        for num, idx in enumerate(order, 1):
            ev = events[idx]
            pair_id = f"{st['label']}-E{num:02d}"
            sheet_rows.append({
                "pair_id": pair_id, "student": st["label"],
                "year": es["year"], "department": es["department"],
                "interests": join(es["interests"]), "skills": join(es["skills"]),
                "career_goal": es["career_goal"],
                "preferred_domains": join(es["preferred_domains"]),
                "event_title": ev.get("title", ""), "category": ev.get("category", ""),
                "event_mode": ev.get("event_mode", ""), "date": ev.get("date", ""),
                "time": ev.get("time", ""), "organizer": ev.get("organizer", ""),
                "event_skills": join(ev.get("skills")),
                "description": str(ev.get("description", ""))[:800],
                "relevance": "",
            })
            sc = table[(st["label"], str(ev.get("_id")))]
            score_rows.append({
                "pair_id": pair_id, "student_id": st["student_id"],
                "event_id": str(ev.get("_id")), "event_title": ev.get("title", ""),
                "category": ev.get("category", ""), **sc,
            })

    for path, cols, rows in ((sheet_path, SHEET_COLUMNS, sheet_rows),
                             (score_path, SCORE_COLUMNS, score_rows)):
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            w.writerows(rows)
    return sheet_path, score_path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--outdir", default="evaluation")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()

    from database import db                               # your existing connection
    students, events, counts = collect(db, a.seed)
    print(f"usable student profiles: {len(students)}")
    print(f"published events:        {len(events)}")
    print(f"pairs to judge:          {len(students) * len(events)}")
    if not students or not events:
        raise SystemExit("Nothing to export.")

    table = score_pairs(students, events, counts)
    if all(v["semantic_only"] == "" for v in table.values()):
        print("WARNING: no semantic scores. Unset DISABLE_SEMANTIC_MODEL and run again "
              "if you want the semantic-only method in the comparison.")
    sheet, scores = write_files(students, events, table, a.outdir, a.seed, a.force)
    print(f"wrote {sheet}\nwrote {scores}  (keep this one private)")


if __name__ == "__main__":
    sys.exit(main())