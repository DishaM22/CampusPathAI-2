"""
Budget Breakup tests. They need no database and no FastAPI install:
budget.py is imported directly, and the budget helpers in main.py are
pulled out with ast and run against a stub HTTPException.
"""
import ast, copy, pathlib, sys, types
import pytest

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
from budget import BudgetError, clean_budget_breakup  # noqa: E402


class HTTPException(Exception):
    def __init__(self, status_code, detail=None):
        self.status_code, self.detail = status_code, detail


def _load_main_helpers():
    src = (HERE / "main.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    want = {"strip_event_budget", "account_can_see_event_budget", "resolve_event_budget",
            "event_belongs_to_club", "user_club_id", "make_club_id", "normalize_text"}
    ns = {"HTTPException": HTTPException, "BudgetError": BudgetError,
          "clean_budget_breakup": clean_budget_breakup, "re": __import__("re")}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in want:
            exec(compile(ast.Module([node], []), "main.py", "exec"), ns)
    assert want <= set(ns), want - set(ns)
    return types.SimpleNamespace(**ns)


M = _load_main_helpers()


def blank():
    from budget import BUDGET_CATEGORIES
    return {c: {i: {"selected": False, "quantity": 0, "amount": 0,
                    **({"description": ""} if i == "others" else {})} for i in items}
            for c, items in BUDGET_CATEGORIES.items()}


def tick(b, cat, item, amount, qty=1, desc=None):
    b[cat][item].update(selected=True, amount=amount, quantity=qty)
    if desc is not None:
        b[cat][item]["description"] = desc
    return b


class Ev:  # stands in for the Pydantic model
    def __init__(self, budget=None, budget_breakup=None):
        self.budget, self.budget_breakup = budget, budget_breakup


# ---------------- budget.py ----------------
def test_total_sums_ticked_rows_only():
    b = tick(tick(blank(), "resource_person", "honorarium", 5000), "refreshments", "lunch", "2500.50")
    b["branding"]["brochures"]["amount"] = 999          # not ticked -> ignored
    clean, total = clean_budget_breakup(b)
    assert clean["total_estimated_budget"] == 7500.5 and str(total) == "7500.50"


def test_float_safe():
    b = tick(tick(blank(), "branding", "brochures", "0.1"), "branding", "print_banners_standees", "0.2")
    assert clean_budget_breakup(b)[0]["total_estimated_budget"] == 0.3


def test_manipulated_total_rejected():
    b = tick(blank(), "participants", "prize", 1000)
    b["total_estimated_budget"] = 1
    with pytest.raises(BudgetError):
        clean_budget_breakup(b)


def test_correct_client_total_accepted():
    b = tick(blank(), "participants", "prize", 1000)
    b["total_estimated_budget"] = 1000
    assert clean_budget_breakup(b)[0]["total_estimated_budget"] == 1000


@pytest.mark.parametrize("bad", [-1, "abc", "1e3", float("nan"), float("inf"), True, "1.234", 10**9])
def test_bad_amounts_rejected(bad):
    b = tick(blank(), "branding", "brochures", 1)
    b["branding"]["brochures"]["amount"] = bad
    with pytest.raises(BudgetError):
        clean_budget_breakup(b)


def test_bad_quantity_rejected():
    b = tick(blank(), "branding", "brochures", 1)
    b["branding"]["brochures"]["quantity"] = -5
    with pytest.raises(BudgetError):
        clean_budget_breakup(b)


def test_others_needs_description_when_ticked():
    with pytest.raises(BudgetError):
        clean_budget_breakup(tick(blank(), "miscellaneous", "others", 100))
    ok, _ = clean_budget_breakup(tick(blank(), "miscellaneous", "others", 100, desc="  Trophy  "))
    assert ok["miscellaneous"]["others"]["description"] == "Trophy"


def test_description_length_limit():
    with pytest.raises(BudgetError):
        clean_budget_breakup(tick(blank(), "miscellaneous", "others", 1, desc="x" * 101))


def test_unknown_keys_rejected_and_not_stored():
    for extra in ({"hack": {}}, ):
        b = {**blank(), **extra}
        with pytest.raises(BudgetError):
            clean_budget_breakup(b)
    b = blank(); b["branding"]["evil"] = {"selected": True, "amount": 1}
    with pytest.raises(BudgetError):
        clean_budget_breakup(b)


def test_total_cap():
    b = tick(tick(blank(), "branding", "brochures", 99999999), "branding", "print_banners_standees", 99999999)
    with pytest.raises(BudgetError):
        clean_budget_breakup(b)


def test_selected_must_be_bool():
    b = blank(); b["branding"]["brochures"]["selected"] = "yes"
    with pytest.raises(BudgetError):
        clean_budget_breakup(b)


def test_empty_breakup_total_zero():
    assert clean_budget_breakup(blank())[0]["total_estimated_budget"] == 0


def test_shape_only_five_categories_in_order():
    assert list(clean_budget_breakup(blank())[0]) == [
        "resource_person", "branding", "participants", "refreshments", "miscellaneous",
        "total_estimated_budget"]


# ---------------- role rules (resolve_event_budget) ----------------
@pytest.mark.parametrize("role", ["club", "faculty"])
def test_club_and_faculty_can_save(role):
    out = M.resolve_event_budget(Ev(2500, {**tick(blank(), "participants", "prize", 2500),
                                          "total_estimated_budget": 2500}), role, creating=True)
    assert out["budget"] == 2500 and out["budget_breakup"]["participants"]["prize"]["amount"] == 2500


def test_legacy_total_must_match():
    with pytest.raises(HTTPException) as e:
        M.resolve_event_budget(Ev(1, tick(blank(), "participants", "prize", 2500)), "club", True)
    assert e.value.status_code == 422


def test_create_requires_breakup():
    with pytest.raises(HTTPException) as e:
        M.resolve_event_budget(Ev(100), "club", creating=True)
    assert e.value.status_code == 422


def test_update_without_breakup_keeps_existing():
    assert M.resolve_event_budget(Ev(), "faculty", creating=False) == {}
    assert M.resolve_event_budget(Ev(999), "faculty", creating=False) == {}   # bare float cannot overwrite


@pytest.mark.parametrize("role", ["admin", "hod", "dean", "student"])
def test_view_only_roles_cannot_write(role):
    with pytest.raises(HTTPException) as e:
        M.resolve_event_budget(Ev(None, blank()), role, creating=True)
    assert e.value.status_code == 403
    with pytest.raises(HTTPException):
        M.resolve_event_budget(Ev(5), role, creating=False)
    assert M.resolve_event_budget(Ev(), role, creating=False) == {}   # no budget -> fine (admin create)


# ---------------- visibility ----------------
EVENT = {"budget": 10, "budget_breakup": {"x": 1}, "title": "t", "club_id": "a::c", "organizer": "A"}
MINE = {"role": "club", "club_name": "A", "club_id": "a::c"}
OTHER = {"role": "faculty", "club_name": "B", "club_id": "b::c"}


def test_strip_removes_both_fields():
    e = M.strip_event_budget(copy.deepcopy(EVENT))
    assert "budget" not in e and "budget_breakup" not in e and e["title"] == "t"


def test_visibility_matrix():
    can = M.account_can_see_event_budget
    assert can(MINE, EVENT)
    assert not can(OTHER, EVENT)                 # other club
    for role in ("hod", "dean", "admin"):
        assert can({"role": role}, EVENT)
    assert not can({"role": "student"}, EVENT)
    assert not can(None, EVENT)


def test_old_event_without_budget_works():
    old = {"title": "old", "club_id": "a::c"}
    assert M.strip_event_budget(dict(old)) == old