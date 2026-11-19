"""Registration deadline rules (no database / FastAPI needed)."""
import ast, pathlib, sys, types
from datetime import datetime, timedelta
import pytest

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import registration_deadline as rd  # noqa: E402


class HTTPException(Exception):
    def __init__(self, status_code, detail=None):
        self.status_code, self.detail = status_code, detail


def _load():
    tree = ast.parse((HERE / "main.py").read_text(encoding="utf-8"))
    want = {"event_requires_registration", "_deadline_error", "resolve_registration_deadline"}
    ns = {"HTTPException": HTTPException, "deadline_problem": rd.deadline_problem,
          "parse_deadline": rd.parse_deadline, "stored_deadline": rd.stored_deadline,
          "to_iso": rd.to_iso}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in want:
            exec(compile(ast.Module([node], []), "main.py", "exec"), ns)
    assert want <= set(ns)
    return types.SimpleNamespace(**ns)


M = _load()
NOW = rd.now_ist()


def fmt(dt):
    return dt.strftime("%Y-%m-%dT%H:%M")


def day(offset, hhmm="10:00"):
    return (NOW + timedelta(days=offset)).strftime("%Y-%m-%d"), f"{hhmm} - 12:00"


class Ev:
    def __init__(self, deadline=None, required=None, date=None, time=None):
        d, t = day(10)
        self.registration_deadline, self.registration_required = deadline, required
        self.date, self.time = date or d, time or t


# ---------- pure helpers ----------
def test_ist_offset_and_parse():
    assert rd.parse_deadline("2026-12-01T17:00").utcoffset() == timedelta(hours=5, minutes=30)
    assert rd.to_iso(rd.parse_deadline("2026-12-01T17:00")) == "2026-12-01T17:00:00+05:30"
    # a UTC time is converted to IST
    assert rd.to_iso(rd.parse_deadline("2026-12-01T11:30:00+00:00")) == "2026-12-01T17:00:00+05:30"


@pytest.mark.parametrize("bad", ["", None, "abc", "2026-13-45T10:00", "2026-12-01T10:00Z"])
def test_parse_rejects_bad(bad):
    with pytest.raises(ValueError):
        rd.parse_deadline(bad)


def test_event_start_parsing():
    assert rd.event_start("2026-12-05", "10:30 - 12:00").hour == 10
    assert rd.event_start("2026-12-05", "02:15 PM").hour == 14
    assert rd.event_start("05/12/2026", "09:00").day == 5
    assert rd.event_start("2026-12-05", "").hour == 23     # no time -> end of day
    assert rd.event_start("nonsense", "10:00") is None


def test_deadline_must_be_before_start_and_future():
    start = rd.event_start("2026-12-05", "10:00 - 12:00")
    ok = start - timedelta(hours=1)
    assert rd.deadline_problem(ok, "2026-12-05", "10:00 - 12:00", now=ok - timedelta(days=1)) == ""
    assert "before the event starts" in rd.deadline_problem(start, "2026-12-05", "10:00 - 12:00", now=ok)
    assert "future" in rd.deadline_problem(ok, "2026-12-05", "10:00 - 12:00", now=ok + timedelta(minutes=1))
    assert rd.deadline_problem(ok, "2026-12-05", "10:00 - 12:00", check_future=False, now=start) == ""


def test_closed_logic():
    past = rd.to_iso(NOW - timedelta(hours=1)); future = rd.to_iso(NOW + timedelta(hours=1))
    assert rd.registration_is_closed({"registration_deadline": past}) is True
    assert rd.registration_is_closed({"registration_deadline": future}) is False
    assert rd.registration_is_closed({"title": "old event"}) is False                      # no deadline
    assert rd.registration_is_closed({"registration_deadline": past, "registration_required": False}) is False
    assert rd.registration_is_closed({"registration_deadline": "garbage"}) is False
    # exactly at the deadline is still open, one second later is closed
    d = rd.parse_deadline(past)
    assert rd.registration_is_closed({"registration_deadline": past}, now=d) is False
    assert rd.registration_is_closed({"registration_deadline": past}, now=d + timedelta(seconds=1)) is True


# ---------- resolve_registration_deadline ----------
def err(fn, *a, **k):
    with pytest.raises(HTTPException) as e:
        fn(*a, **k)
    return e.value


def test_create_requires_deadline_for_club_and_faculty():
    for role in ("club", "faculty"):
        assert err(M.resolve_registration_deadline, Ev(), role, True).status_code == 422
        assert err(M.resolve_registration_deadline, Ev(""), role, True).status_code == 422


def test_create_ok_stores_ist_string():
    out = M.resolve_registration_deadline(Ev(fmt(NOW + timedelta(days=2))), "club", True)
    assert out["registration_deadline"].endswith("+05:30")


def test_create_rejects_past_and_after_start():
    assert err(M.resolve_registration_deadline, Ev(fmt(NOW - timedelta(days=1))), "club", True).status_code == 422
    assert err(M.resolve_registration_deadline, Ev(fmt(NOW + timedelta(days=11))), "club", True).status_code == 422


def test_no_registration_has_no_deadline():
    assert M.resolve_registration_deadline(Ev(None, required=False), "club", True) == {}
    old = {"registration_deadline": rd.to_iso(NOW + timedelta(days=2))}
    assert M.resolve_registration_deadline(Ev(None, required=False), "faculty", False, old) == {"registration_deadline": None}


def test_admin_cannot_set_but_can_create_without():
    assert err(M.resolve_registration_deadline, Ev(fmt(NOW + timedelta(days=2))), "admin", True).status_code == 403
    assert M.resolve_registration_deadline(Ev(None), "admin", True) == {}


def test_legacy_event_without_deadline_edits_fine():
    legacy = {"title": "old"}
    assert M.resolve_registration_deadline(Ev(None), "club", False, legacy) == {}
    assert M.resolve_registration_deadline(Ev(""), "club", False, legacy) == {}


def test_update_keeps_or_validates_existing():
    existing = {"registration_deadline": rd.to_iso(NOW + timedelta(days=2)), "approval_status": "changes_requested"}
    assert M.resolve_registration_deadline(Ev(None), "club", False, existing) == {}
    assert err(M.resolve_registration_deadline, Ev(""), "club", False, existing).status_code == 422   # cannot remove
    # event moved to before the old deadline -> must fix the deadline too
    d, t = day(1)
    assert err(M.resolve_registration_deadline, Ev(None, date=d, time=t), "club", False, existing).status_code == 422


def test_published_event_deadline_needs_admin_request():
    existing = {"registration_deadline": rd.to_iso(NOW + timedelta(days=2)), "approval_status": "published"}
    assert err(M.resolve_registration_deadline, Ev(fmt(NOW + timedelta(days=3))), "club", False, existing).status_code == 409
    # re-sending the same deadline is not a change
    same = rd.parse_deadline(existing["registration_deadline"]).strftime("%Y-%m-%dT%H:%M:%S")
    assert M.resolve_registration_deadline(Ev(same), "club", False, existing)["registration_deadline"] == existing["registration_deadline"]


def test_switching_to_required_needs_deadline():
    existing = {"registration_required": False, "approval_status": "changes_requested"}
    assert err(M.resolve_registration_deadline, Ev(None, required=True), "club", False, existing).status_code == 422