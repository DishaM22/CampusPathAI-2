"""
Official departments of CampusPath AI.

ONE place for the list used by HOD registration, event creation and the
HOD approval routing (an event goes only to the HOD of its department).

To add a department:
  1. add its name to OFFICIAL_DEPARTMENTS below, and
  2. add the same name to src/departments.js in the frontend.
Optionally add spellings people used before to _ALIASES.

Put this file next to main.py and reviewer_auth.py.
"""
import re

OFFICIAL_DEPARTMENTS = [
    "CSE",
    "AIML & Robotics",
    "Cyber Security",
    "EEE",
    "Mechanical",
]

# Older accounts / events typed the department as free text. These
# spellings are recognised as the official department they obviously mean.
# Keys are written in the normalised form produced by department_key().
_ALIASES = {
    # CSE
    "cs": "CSE",
    "computer science": "CSE",
    "computer science and engineering": "CSE",
    "computer science engineering": "CSE",
    # AIML & Robotics
    "aiml": "AIML & Robotics",
    "ai and ml": "AIML & Robotics",
    "aiml and robotics": "AIML & Robotics",
    "ai and robotics": "AIML & Robotics",
    "robotics": "AIML & Robotics",
    "artificial intelligence": "AIML & Robotics",
    "artificial intelligence and machine learning": "AIML & Robotics",
    "artificial intelligence and robotics": "AIML & Robotics",
    # Cyber Security
    "cyber security": "Cyber Security",
    "cybersecurity": "Cyber Security",
    # EEE
    "electrical and electronics": "EEE",
    "electrical and electronics engineering": "EEE",
    # Mechanical
    "mech": "Mechanical",
    "mechanical engineering": "Mechanical",
}


def department_key(value):
    """'AIML  &  Robotics' -> 'aiml and robotics' (case / spacing / punctuation free)."""
    text = str(value or "").lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


_BY_KEY = {department_key(name): name for name in OFFICIAL_DEPARTMENTS}
_BY_KEY.update(_ALIASES)


def canonical_department(value):
    """The official department name for `value`, or None when it is not one."""
    return _BY_KEY.get(department_key(value))