"""
Creates the indexes for the `interactions` collection.
Safe to run any number of times (existing indexes are left alone).

    python create_interaction_indexes.py
"""
from database import db

db.interactions.create_index([("student_id", 1), ("timestamp", -1)], name="by_student_time")
db.interactions.create_index(
    [("student_id", 1), ("action", 1), ("event_id", 1), ("timestamp", -1)],
    name="dedupe_lookup",
)
db.interactions.create_index([("event_id", 1)], name="by_event")
print("interaction indexes ready:", sorted(db.interactions.index_information()))