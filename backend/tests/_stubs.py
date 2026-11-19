"""Shared stand-ins for bson / pymongo / fastapi so the tests run anywhere (installed once)."""
import itertools, sys, types

class ObjectId(str):
    def __new__(cls, value=None):
        v = str(value) if value is not None else ("%024x" % next(_COUNTER))
        if len(v) != 24 or any(c not in "0123456789abcdef" for c in v.lower()):
            raise ValueError("bad id")
        return str.__new__(cls, v)
_COUNTER = itertools.count(100000)

class DuplicateKeyError(Exception): pass

class HTTPException(Exception):
    def __init__(self, status_code, detail=""):
        self.status_code, self.detail = status_code, detail

def install():
    sys.modules["bson"] = types.SimpleNamespace(ObjectId=ObjectId)
    pm, pe = types.ModuleType("pymongo"), types.ModuleType("pymongo.errors")
    pe.DuplicateKeyError = DuplicateKeyError
    sys.modules["pymongo"], sys.modules["pymongo.errors"] = pm, pe
    sys.modules["fastapi"] = types.SimpleNamespace(HTTPException=HTTPException)

if not getattr(sys.modules.get("bson"), "_shared", False):
    install()
    sys.modules["bson"]._shared = True
