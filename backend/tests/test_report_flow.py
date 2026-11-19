"""Post-event report endpoints against a fake DB and a temp folder."""
import ast, io, pathlib, secrets, sys, types
from datetime import datetime, timezone
import pytest
from pydantic import BaseModel

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tests"))
import report_files as rf  # noqa: E402
from test_checklist import HTTPException, ObjectId, Col, get, match, Cursor, code  # noqa: E402
from test_report_files import PDF, DOCX, PNG, JPG  # noqa: E402


class RCol(Col):
    def update_one(self, q, ch, upsert=False):
        d = self.find_one(q)
        if not d:
            if not upsert:
                return types.SimpleNamespace(matched_count=0)
            d = {k: v for k, v in q.items()}
            for k, v in ch.get("$setOnInsert", {}).items():
                d[k] = v
            self.insert_one(d)
        for k, v in ch.get("$set", {}).items():
            d[k] = v
        for k, v in ch.get("$push", {}).items():
            d.setdefault(k, []).append(v)
        return types.SimpleNamespace(matched_count=1)
    def insert_many(self, rows):
        for r in rows: self.insert_one(r)


class FileResponse:
    def __init__(self, path, media_type=None, headers=None):
        self.path, self.media_type, self.headers = path, media_type, headers


class Up:
    def __init__(self, name, data):
        self.filename, self.file = name, io.BytesIO(data)


class DB:
    def __init__(self):
        for n in ("students", "events", "event_reports", "notifications"):
            setattr(self, n, RCol())


def load(db, who, tmp):
    tree = ast.parse((HERE / "main.py").read_text(encoding="utf-8"))
    want_f = {"_deadline_actor", "_deadline_event", "is_event_public", "event_belongs_to_club",
              "user_club_id", "make_club_id", "normalize_text", "hod_review_block_reason"}
    want_a = {"REPORT_REVIEW_STATUS", "REPORT_NEXT_STATUS", "REPORT_EDITABLE", "_REPORT_FILE_KEYS", "_REPORT_STAGE_RANK"}
    class App:
        def __getattr__(self, n): return lambda *a, **k: (lambda f: f)
    ns = {"HTTPException": HTTPException, "ObjectId": ObjectId, "BaseModel": BaseModel,
          "Header": lambda d=None: d, "File": lambda *a, **k: None, "UploadFile": object, "datetime": datetime,
          "timezone": timezone, "secrets": secrets, "FileResponse": FileResponse, "app": App(),
          "db": db, "POST_REPORT_DIR": tmp, "notify_faculty_of_club": lambda *a, **k: db.notifications.insert_one({"type": "report_review", "to": "faculty", "related_id": a[5]}), "ReportFileError": rf.ReportFileError,
          "report_validate": rf.validate_upload, "report_previewable": rf.PREVIEWABLE,
          "report_limits": {"main": rf.MAX_MAIN_BYTES, "attachment": rf.MAX_ATTACHMENT_BYTES,
                            "total": rf.MAX_TOTAL_BYTES, "count": rf.MAX_ATTACHMENTS},
          "get_student_id_from_token": lambda a: who["id"],
          "canonical_department": lambda v: (str(v).strip().upper() or None) if v else None}
    for node in tree.body:
        name = None
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            name = node.name
        elif isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
        if name is None:
            continue
        ok = (name in want_f or name in want_a or name == "ReportReviewBody"
              or (name.startswith(("_report_", "get_event_report", "upload_report", "remove_report",
                                   "submit_event_report", "review_event_report", "pending_event_reports",
                                   "download_report"))))
        if ok:
            if hasattr(node, "decorator_list"): node.decorator_list = []
            exec(compile(ast.Module([node], []), "main.py", "exec"), ns)
    assert want_f <= set(ns) and want_a <= set(ns)
    return types.SimpleNamespace(**ns)


@pytest.fixture
def world(tmp_path):
    db, who = DB(), {"id": "u-club"}
    mk = lambda i, role, **k: db.students.insert_one({"_id": ObjectId(i), "role": role, "status": "active", "name": i, **k})
    mk("u-club", "club", club_id="c1", club_name="C1")
    mk("u-other", "club", club_id="c2", club_name="C2")
    mk("u-fac", "faculty", club_id="c1", club_name="C1")
    mk("u-hod", "hod", department="CSE"); mk("u-hod2", "hod", department="ECE")
    mk("u-dean", "dean"); mk("u-admin", "admin"); mk("u-stud", "student")
    db.events.insert_one({"_id": ObjectId("e1"), "title": "Hack", "club_id": "c1", "created_by": "u-club",
                          "approval_status": "published", "department": "CSE"})
    db.events.insert_one({"_id": ObjectId("e2"), "title": "Draft", "club_id": "c1", "created_by": "u-club",
                          "approval_status": "pending_hod"})
    return db, who, load(db, who, tmp_path), tmp_path


def as_(who, i): who["id"] = i


def test_unpublished_and_wrong_roles(world):
    db, who, M, _ = world
    assert code(M.get_event_report, "e2", "t") == 409
    as_(who, "u-stud"); assert code(M.get_event_report, "e1", "t") == 403
    as_(who, "u-other"); assert code(M.get_event_report, "e1", "t") == 403
    as_(who, "u-dean"); assert code(M.get_event_report, "e1", "t") == 404  # nothing submitted


def test_full_flow_with_rejection_and_versions(world):
    db, who, M, tmp = world
    v = M.get_event_report("e1", "t")
    assert v["report_status"] == "not_started" and v["can_edit"]
    # submit without main -> 422
    assert code(M.submit_event_report, "e1", "t") == 422
    v = M.upload_report_main("e1", Up("report.pdf", PDF), "t")
    assert v["report_status"] == "draft" and v["main_report"]["name"] == "report.pdf"
    assert "stored_name" not in v["main_report"]
    first = db.event_reports.find_one({"event_id": "e1"})["main_report"]
    assert (tmp / "e1" / first["stored_name"]).is_file()
    # replace main: old bytes removed (never submitted)
    v = M.upload_report_main("e1", Up("r2.docx", DOCX), "t")
    assert not (tmp / "e1" / first["stored_name"]).exists()
    v = M.upload_report_attachment("e1", Up("p.png", PNG), "t")
    assert len(v["attachments"]) == 1 and v["attachments"][0]["previewable"]
    # bad uploads
    assert code(M.upload_report_attachment, "e1", Up("x.exe", PDF), "t") == 415
    assert code(M.upload_report_attachment, "e1", Up("x.png", PDF), "t") == 400
    assert code(M.upload_report_main, "e1", Up("x.png", PNG), "t") == 415
    # remove attachment
    att = v["attachments"][0]["file_id"]
    v = M.remove_report_file("e1", att, "t")
    assert v["attachments"] == []
    assert code(M.remove_report_file, "e1", "nope", "t") == 404
    M.upload_report_attachment("e1", Up("p.jpg", JPG), "t")
    # submit
    v = M.submit_event_report("e1", "t")
    assert v["report_status"] == "pending_faculty" and not v["can_edit"]
    assert code(M.upload_report_main, "e1", Up("z.pdf", PDF), "t") == 409
    assert code(M.submit_event_report, "e1", "t") == 409
    # faculty of club reviews
    as_(who, "u-fac")
    assert M.get_event_report("e1", "t")["can_review"]
    assert code(M.review_event_report, "e1", M.ReportReviewBody(decision="rejected", remarks="short"), "t") == 400
    v = M.review_event_report("e1", M.ReportReviewBody(decision="rejected", remarks="Add the attendance sheet please"), "t")
    assert v["report_status"] == "rejected"
    notes = db.notifications.docs
    assert notes and notes[-1]["recipient"] == "u-club"
    # organizer corrects: replace main (old file kept: part of submission 1)
    as_(who, "u-club")
    sub1_main = db.event_reports.find_one({"event_id": "e1"})["submissions"][0]["main_report"]
    v = M.get_event_report("e1", "t"); assert v["can_edit"]
    M.upload_report_main("e1", Up("fixed.pdf", PDF), "t")
    assert (tmp / "e1" / sub1_main["stored_name"]).is_file()
    v = M.submit_event_report("e1", "t")
    assert v["report_status"] == "pending_faculty" and len(v["submissions"]) == 2
    assert v["submissions"][0]["decisions"][0]["remarks"].startswith("Add the")
    # full approval chain; wrong stage rejected
    as_(who, "u-hod"); assert code(M.review_event_report, "e1", M.ReportReviewBody(decision="approved"), "t") == 404  # hidden until Faculty approves
    as_(who, "u-fac"); M.review_event_report("e1", M.ReportReviewBody(decision="approved"), "t")
    as_(who, "u-hod2"); assert code(M.get_event_report, "e1", "t") == 403
    as_(who, "u-hod"); assert M.get_event_report("e1", "t")["can_review"]
    pend = M.pending_event_reports("t"); assert len(pend["reports"]) == 1
    M.review_event_report("e1", M.ReportReviewBody(decision="approved"), "t")
    as_(who, "u-dean"); M.review_event_report("e1", M.ReportReviewBody(decision="approved"), "t")
    as_(who, "u-admin"); v = M.review_event_report("e1", M.ReportReviewBody(decision="approved"), "t")
    assert v["report_status"] == "approved"
    assert len(db.event_reports.find_one({"event_id": "e1"})["history"]) == 7
    as_(who, "u-club"); assert not M.get_event_report("e1", "t")["can_edit"]


def test_faculty_submission_starts_at_hod_and_download_rules(world):
    db, who, M, tmp = world
    as_(who, "u-fac")
    M.upload_report_main("e1", Up("r.pdf", PDF), "t")
    v = M.submit_event_report("e1", "t")
    assert v["report_status"] == "pending_hod"
    fid = v["main_report"]["file_id"]
    r = M.download_report_file("e1", fid, "t")
    assert r.media_type == "application/pdf" and r.headers["Content-Disposition"].startswith("inline")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    as_(who, "u-other"); assert code(M.download_report_file, "e1", fid, "t") == 403
    as_(who, "u-stud"); assert code(M.download_report_file, "e1", fid, "t") == 403
    as_(who, "u-hod"); assert M.download_report_file("e1", fid, "t").path
    as_(who, "u-hod2"); assert code(M.download_report_file, "e1", fid, "t") == 403
    as_(who, "u-hod"); assert code(M.download_report_file, "e1", "../../x", "t") == 404


def test_reviewer_sees_submitted_not_draft_and_limits(world):
    db, who, M, tmp = world
    M.upload_report_main("e1", Up("r.pdf", PDF), "t"); M.submit_event_report("e1", "t")
    db.event_reports.find_one({"event_id": "e1"})["status"] = "rejected"
    M.upload_report_main("e1", Up("new.pdf", PDF), "t")  # draft change after rejection
    db.event_reports.find_one({"event_id": "e1"})["status"] = "pending_admin"  # as if it reached Admin
    as_(who, "u-admin")
    assert M.get_event_report("e1", "t")["main_report"]["name"] == "r.pdf"
    db.event_reports.find_one({"event_id": "e1"})["status"] = "rejected"
    as_(who, "u-club")
    for i in range(rf.MAX_ATTACHMENTS):
        M.upload_report_attachment("e1", Up(f"{i}.png", PNG), "t")
    assert code(M.upload_report_attachment, "e1", Up("x.png", PNG), "t") == 409


def test_next_reviewer_is_notified(world):
    db, who, M, tmp = world
    M.upload_report_main("e1", Up("r.pdf", PDF), "t"); M.submit_event_report("e1", "t")
    assert [n["to"] for n in db.notifications.docs if n.get("type") == "report_review"] == ["faculty"]
    as_(who, "u-fac"); M.review_event_report("e1", M.ReportReviewBody(decision="approved"), "t")
    as_(who, "u-hod"); M.review_event_report("e1", M.ReportReviewBody(decision="approved"), "t")
    as_(who, "u-dean"); M.review_event_report("e1", M.ReportReviewBody(decision="approved"), "t")
    roles = [n.get("recipient_role") or n.get("to") for n in db.notifications.docs if n.get("type") == "report_review"]
    assert roles == ["faculty", "hod", "dean", "admin"]
    as_(who, "u-admin"); M.review_event_report("e1", M.ReportReviewBody(decision="approved"), "t")
    assert db.notifications.docs[-1]["recipient"] == "u-club"  # organizer told of final approval


def test_higher_levels_cannot_see_report_until_faculty_approves(world):
    db, who, M, tmp = world
    M.upload_report_main("e1", Up("r.pdf", PDF), "t")
    v = M.submit_event_report("e1", "t")
    fid = v["main_report"]["file_id"]
    for u in ("u-hod", "u-dean", "u-admin"):
        as_(who, u)
        assert code(M.get_event_report, "e1", "t") == 404
        assert code(M.download_report_file, "e1", fid, "t") == 404
        assert M.pending_event_reports("t")["reports"] == []
    as_(who, "u-fac"); M.review_event_report("e1", M.ReportReviewBody(decision="approved"), "t")
    as_(who, "u-hod"); assert M.get_event_report("e1", "t")["can_review"]
    as_(who, "u-dean"); assert code(M.get_event_report, "e1", "t") == 404
    as_(who, "u-admin"); assert code(M.get_event_report, "e1", "t") == 404
    as_(who, "u-hod"); M.review_event_report("e1", M.ReportReviewBody(decision="approved"), "t")
    as_(who, "u-dean"); assert M.get_event_report("e1", "t")["can_review"]
    as_(who, "u-admin"); assert code(M.get_event_report, "e1", "t") == 404


def test_rejection_keeps_report_hidden_from_uninvolved(world):
    db, who, M, tmp = world
    M.upload_report_main("e1", Up("r.pdf", PDF), "t"); M.submit_event_report("e1", "t")
    as_(who, "u-fac"); M.review_event_report("e1", M.ReportReviewBody(decision="rejected", remarks="Please add the attendance sheet"), "t")
    for u in ("u-hod", "u-dean", "u-admin"):
        as_(who, u); assert code(M.get_event_report, "e1", "t") == 404