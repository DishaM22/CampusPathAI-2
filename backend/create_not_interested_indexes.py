"""
Creates the index for the `not_interested_events` collection (Phase H).
Safe to run any number of times (an existing index is left alone).

    python create_not_interested_indexes.py

The unique index guarantees one row per student per event, even if two
requests arrive at the same moment.
"""
from database import db

db.not_interested_events.create_index(
    [("student_id", 1), ("event_id", 1)],
    unique=True,
    name="one_row_per_student_event",
)
db.not_interested_events.create_index(
    [("student_id", 1), ("active", 1)],
    name="by_student_active",
)
print("not-interested indexes ready:", sorted(db.not_interested_events.index_information()))