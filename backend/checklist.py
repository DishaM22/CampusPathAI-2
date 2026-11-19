"""
Event Checklist: the 17 fixed activities, their status options and the
pure helpers used by the checklist endpoints (no database, no FastAPI).
"""

# (key, label) - exactly the 17 activities, in this order.
ACTIVITIES = [
    ("event_creative", "Event Creative (Poster/Brochure)"),
    ("event_webpage", "Event Webpage"),
    ("event_itinerary", "Event Itinerary"),
    ("venue_set_up", "Venue Set up"),
    ("audience_invite", "Audience Invite"),
    ("stage_dias_set_up", "Stage/Dias Set up"),
    ("photography", "Photography"),
    ("transport", "Transport"),
    ("certificates", "Certificates"),
    ("banner", "Banner"),
    ("communications", "Communications (Mailers/Invite)"),
    ("indent", "Indent"),
    ("mementoes", "Mementoes"),
    ("name_plate", "Name plate"),
    ("online_link", "Zoom/MS Teams Link (If Online)"),
    ("post_event_report", "Post Event Report"),
    ("website_updates_status", "Website Updates Status (Live or Yet to)"),
]

STANDARD_OPTIONS = ["Not Started", "In Progress", "Ready", "Not Applicable"]
# The last activity is tracked as Live / Yet to, as requested.
SPECIAL_OPTIONS = {"website_updates_status": ["Yet to", "Live"]}

CHALLENGES_MAX = 500
KEYS = [key for key, _ in ACTIVITIES]


def options_for(key):
    return list(SPECIAL_OPTIONS.get(key, STANDARD_OPTIONS))


def default_status(key):
    return options_for(key)[0]


class ChecklistError(ValueError):
    pass


def clean_update(key, status, challenges):
    """Validated (status, challenges) for one activity, or ChecklistError."""
    if key not in KEYS:
        raise ChecklistError("Unknown checklist activity.")
    status = str(status or "").strip()
    if status not in options_for(key):
        raise ChecklistError(
            "Status must be one of: " + ", ".join(options_for(key)) + "."
        )
    text = " ".join(str(challenges or "").split())
    if len(text) > CHALLENGES_MAX:
        raise ChecklistError(
            f"Challenges must be {CHALLENGES_MAX} characters or fewer."
        )
    return status, text


def build_view(doc):
    """All 17 activities (stored values or defaults) plus recent history."""
    stored = (doc or {}).get("items") or {}
    activities = []
    for key, label in ACTIVITIES:
        item = stored.get(key) or {}
        status = item.get("status")
        if status not in options_for(key):
            status = default_status(key)
        activities.append({
            "key": key,
            "label": label,
            "options": options_for(key),
            "status": status,
            "challenges": item.get("challenges", ""),
            "updated_at": item.get("updated_at"),
            "updated_by_name": item.get("updated_by_name", ""),
        })
    history = list((doc or {}).get("history") or [])[-50:]
    history.reverse()
    return activities, history