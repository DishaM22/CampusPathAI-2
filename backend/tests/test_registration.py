"""Registration Requirement rules (no database / FastAPI needed)."""
import ast, pathlib, sys, types
import pytest

HERE = pathlib.Path(__file__).resolve().parent.parent


class HTTPException(Exception):
    def __init__(self, status_code, detail=None):
        self.status_code, self.detail = status_code, detail


def _load():
    tree = ast.parse((HERE / "main.py").read_text(encoding="utf-8"))
    want = {"event_requires_registration", "with_registration_flag", "resolve_registration_required"}
    sys.path.insert(0, str(HERE))
    from registration_deadline import registration_is_closed
    ns = {"HTTPException": HTTPException, "registration_is_closed": registration_is_closed}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in want:
            exec(compile(ast.Module([node], []), "main.py", "exec"), ns)
    assert want <= set(ns)
    return types.SimpleNamespace(**ns)


M = _load()


class Ev:
    def __init__(self, v=None):
        self.registration_required = v


def test_old_event_defaults_to_required():
    assert M.event_requires_registration({"title": "old"}) is True
    assert M.with_registration_flag({"title": "old"})["registration_required"] is True


def test_explicit_values():
    assert M.event_requires_registration({"registration_required": True}) is True
    assert M.event_requires_registration({"registration_required": False}) is False
    assert M.with_registration_flag({"registration_required": False})["registration_required"] is False


@pytest.mark.parametrize("role", ["club", "faculty", "admin"])
def test_create_defaults_to_required(role):
    assert M.resolve_registration_required(Ev(None), role, creating=True) == {"registration_required": True}


@pytest.mark.parametrize("role", ["club", "faculty"])
@pytest.mark.parametrize("value", [True, False])
def test_club_and_faculty_can_set(role, value):
    assert M.resolve_registration_required(Ev(value), role, True) == {"registration_required": value}
    assert M.resolve_registration_required(Ev(value), role, False) == {"registration_required": value}


def test_update_without_value_keeps_saved():
    assert M.resolve_registration_required(Ev(None), "club", creating=False) == {}


@pytest.mark.parametrize("role", ["admin", "hod", "dean", "student"])
@pytest.mark.parametrize("creating", [True, False])
def test_other_roles_cannot_set(role, creating):
    with pytest.raises(HTTPException) as e:
        M.resolve_registration_required(Ev(False), role, creating)
    assert e.value.status_code == 403