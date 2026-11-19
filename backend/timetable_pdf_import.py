"""
timetable_pdf_import.py

Import many class timetables from ONE PDF (aSc Timetables export: one page per
section, a grid of periods x weekdays).

  PART 1  PDF reading     page -> grid -> entries            (needs pdfplumber)
  PART 2  Conversion      entries -> our timetable blocks + class details
  PART 3  Saving          creates/updates classes, saves drafts, optional publish
  PART 4  FastAPI router  /api/admin/timetable-import/preview and /commit

Nothing here guesses silently: anything the reader is unsure about is returned
as a warning and shown on the review screen before anything is saved.

Install once:   pip install pdfplumber
"""

import base64
import binascii
import io
import logging
import math
import re
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

import class_timetable as ct

logger = logging.getLogger("campuspath.timetable_import")

__all__ = ["create_timetable_import_router", "parse_pdf_bytes", "commit_import", "PdfImportError"]

# ============================================================
# PART 1 - PDF READING
# ============================================================

DAY_NAMES = ("monday", "tuesday", "wednesday", "thursday", "friday")
DAY_PREFIX = {"mon": "monday", "tue": "tuesday", "wed": "wednesday",
              "thu": "thursday", "fri": "friday"}

# A subject code: 2-4 capital letters + 3-4 digits (+ "_P" for a practical).
SUBJECT_RE = re.compile(r"^[A-Z]{2,4}\d{3,4}(?:_P)?$")
# A room: 1-5 capital letters + exactly 2 digits (+ optional lower-case letter).
ROOM_RE = re.compile(r"^[A-Z]{1,5}\d{2}[a-z]?$")
# A batch label: B1, Bat1, Batch1.
BATCH_RE = re.compile(r"^B(?:at(?:ch)?)?\s?(\d)$")
TIME_RE = re.compile(r"(\d{1,2}):(\d{2})")
SECTION_CODE_RE = re.compile(r"^(?:(\d)([A-Za-z]+)|([A-Za-z]+))(\d{2})$")

MAX_PAGES = 400
MAX_PDF_BYTES = 25 * 1024 * 1024


class PdfImportError(Exception):
    """A problem with the uploaded file that the admin should hear about."""


def _require_pdfplumber():
    try:
        import pdfplumber  # noqa: F401
        return pdfplumber
    except ImportError:
        raise PdfImportError(
            "The PDF reader is not installed on the server. "
            "Run: pip install pdfplumber"
        )


def _hhmm(h: str, m: str) -> str:
    return f"{int(h):02d}:{int(m):02d}"


def _lines_from_page(page) -> Tuple[List[Tuple[float, float, float]], List[Tuple[float, float, float]]]:
    """
    Cell borders. Returns (vertical, horizontal):
      vertical   = [(x, y_top, y_bottom)]
      horizontal = [(y, x_left, x_right)]
    Thin rectangles are treated as lines too.
    """
    vertical: List[Tuple[float, float, float]] = []
    horizontal: List[Tuple[float, float, float]] = []

    for line in page.lines:
        if abs(line["x0"] - line["x1"]) < 0.8:
            vertical.append((line["x0"], min(line["top"], line["bottom"]), max(line["top"], line["bottom"])))
        elif abs(line["top"] - line["bottom"]) < 0.8:
            horizontal.append((line["top"], min(line["x0"], line["x1"]), max(line["x0"], line["x1"])))

    for rect in page.rects:
        width, height = rect["x1"] - rect["x0"], rect["bottom"] - rect["top"]
        if width < 1.5 and height > 5:
            vertical.append(((rect["x0"] + rect["x1"]) / 2, rect["top"], rect["bottom"]))
        elif height < 1.5 and width > 5:
            horizontal.append(((rect["top"] + rect["bottom"]) / 2, rect["x0"], rect["x1"]))

    return vertical, horizontal


def _page_title(page) -> str:
    """The section code is the biggest upright text on the page."""
    words = page.extract_words(extra_attrs=["size", "upright"])
    # (the weekday names are big too, but they sit lower down in the grid)
    big = [w for w in words if w["upright"] and w["size"] > 20 and w["top"] < 70]
    if not big:
        return ""
    big.sort(key=lambda w: w["x0"])
    return "".join(w["text"] for w in big).strip()


def _read_columns(page, vertical) -> List[Dict[str, Any]]:
    """
    The period columns, from the vertical grid lines of the body and the
    header cells above them. Each: {x0, x1, label, start, end}.
    """
    body_top = min((y0 for _, y0, _ in vertical if y0 > 100), default=113.0)
    xs = sorted({round(x, 1) for x, y0, y1 in vertical if y0 >= body_top - 2 and x > 50})

    merged: List[float] = []
    for x in xs:
        if not merged or x - merged[-1] > 3:
            merged.append(x)

    words = page.extract_words(extra_attrs=["size", "upright"])
    header = [w for w in words if w["upright"] and 62 < w["top"] < body_top - 1]

    columns = []
    for left, right in zip(merged, merged[1:]):
        inside = [w for w in header if left <= (w["x0"] + w["x1"]) / 2 <= right]
        label_words = [w for w in inside if w["size"] > 12]
        time_words = sorted([w for w in inside if w["size"] <= 12], key=lambda w: w["x0"])

        label = " ".join(w["text"] for w in label_words).strip()
        times = TIME_RE.findall(" ".join(w["text"] for w in time_words))
        start = _hhmm(*times[0]) if len(times) >= 1 else None
        end = _hhmm(*times[1]) if len(times) >= 2 else None

        columns.append({"x0": left, "x1": right, "label": label, "start": start, "end": end})

    return columns


def _read_rows(page, vertical, horizontal) -> List[Dict[str, Any]]:
    """
    The five weekday bands. They are the coloured day cells at the left edge
    (not the thin divider lines inside cells, which would cut a day in pieces).
    """
    words = page.extract_words(extra_attrs=["size", "upright"])
    day_words = [w for w in words if w["upright"] and w["size"] > 20 and w["text"][:3].lower() in DAY_PREFIX]

    day_cells = [
        r for r in page.rects
        if r["x0"] < 30 and r["x1"] - r["x0"] > 40 and r["bottom"] - r["top"] > 30 and r["top"] > 100
    ]
    day_cells.sort(key=lambda r: r["top"])

    rows = []
    for word in sorted(day_words, key=lambda w: w["top"]):
        centre = (word["top"] + word["bottom"]) / 2
        cell = next((r for r in day_cells if r["top"] - 1 <= centre <= r["bottom"] + 1), None)
        if cell is not None:
            top, bottom = cell["top"], cell["bottom"]
        else:  # fallback: lines that run under the day names
            tops = [y for y, x0, x1 in horizontal if y < centre - 5 and x0 < 30]
            bottoms = [y for y, x0, x1 in horizontal if y > centre + 5 and x0 < 30]
            if not tops or not bottoms:
                continue
            top, bottom = max(tops), min(bottoms)
        rows.append({"day": DAY_PREFIX[word["text"][:3].lower()], "top": top, "bottom": bottom})

    return rows


def _rotated_labels(page, columns) -> Dict[int, str]:
    """
    Break / Lunch columns hold vertical (rotated) text. Returns
    {column index: "lunch" | "break"}.
    """
    found: Dict[int, List[Any]] = {}
    for char in page.chars:
        if char.get("upright", True) or not char["text"].strip():
            continue
        cx = (char["x0"] + char["x1"]) / 2
        for index, column in enumerate(columns):
            if column["x0"] <= cx <= column["x1"]:
                found.setdefault(index, []).append(char)

    labels: Dict[int, str] = {}
    for index, chars in found.items():
        text = "".join(c["text"] for c in sorted(chars, key=lambda c: c["top"]))
        folded = (text + text[::-1]).lower()
        labels[index] = "lunch" if "lunch" in folded else "break"
    return labels


def _enclosing_cell(cx: float, cy: float, vertical, horizontal) -> Tuple[float, float, float, float]:
    """The grid-line rectangle around a point."""
    lefts = [x for x, y0, y1 in vertical if x <= cx and y0 - 1 <= cy <= y1 + 1]
    rights = [x for x, y0, y1 in vertical if x >= cx and y0 - 1 <= cy <= y1 + 1]
    tops = [y for y, x0, x1 in horizontal if y <= cy and x0 - 1 <= cx <= x1 + 1]
    bottoms = [y for y, x0, x1 in horizontal if y >= cy and x0 - 1 <= cx <= x1 + 1]

    return (
        max(lefts) if lefts else -1.0,
        max(tops) if tops else -1.0,
        min(rights) if rights else 9999.0,
        min(bottoms) if bottoms else 9999.0,
    )


def _classify(words: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Split the words of one cell into subject / room / batch / faculty."""
    ordered = sorted(words, key=lambda w: (round(w["top"] / 2.5), w["x0"]))

    subjects, rooms, batches, rest = [], [], [], []
    for word in ordered:
        text = word["text"].strip()
        if not text:
            continue
        if SUBJECT_RE.match(text):
            subjects.append(text)
        elif BATCH_RE.match(text):
            batches.append("B" + BATCH_RE.match(text).group(1))
        elif ROOM_RE.match(text):
            rooms.append(text)
        else:
            rest.append(text)

    return {"subjects": subjects, "rooms": rooms, "batches": batches,
            "faculty": " ".join(rest).replace(" / ", " / ").strip()}


def read_page(page) -> Dict[str, Any]:
    """One page -> {code, columns, entries, warnings}."""
    warnings: List[str] = []
    code = _page_title(page)

    vertical, horizontal = _lines_from_page(page)
    columns = _read_columns(page, vertical)
    rows = _read_rows(page, vertical, horizontal)

    if not code:
        warnings.append("Could not read the section name on this page.")
    if len(columns) < 3 or len(rows) < 1:
        warnings.append("This page does not look like a timetable grid.")
        return {"code": code, "columns": columns, "entries": [], "lunch": [], "warnings": warnings}

    special = _rotated_labels(page, columns)
    lunch_columns = [i for i, kind in special.items() if kind == "lunch"]

    # Upright body words, grouped by the cell (grid rectangle) they sit in.
    words = page.extract_words(extra_attrs=["size", "upright"])
    body_top = min(row["top"] for row in rows)
    body_bottom = max(row["bottom"] for row in rows)
    cells: Dict[Tuple[float, float, float, float], List[Dict[str, Any]]] = {}

    for word in words:
        if not word["upright"] or word["top"] < body_top - 1 or word["bottom"] > body_bottom + 1:
            continue
        cx, cy = (word["x0"] + word["x1"]) / 2, (word["top"] + word["bottom"]) / 2
        if cx < columns[0]["x0"] - 1:  # the day names
            continue
        rect = _enclosing_cell(cx, cy, vertical, horizontal)
        key = tuple(round(v, 0) for v in rect)
        cells.setdefault(key, []).append(word)

    entries: List[Dict[str, Any]] = []
    for rect, cell_words in cells.items():
        left, top, right, bottom = rect
        cy = (top + bottom) / 2

        row = next((r for r in rows if r["top"] - 2 <= cy <= r["bottom"] + 2), None)
        covered = [i for i, c in enumerate(columns) if left - 2 <= (c["x0"] + c["x1"]) / 2 <= right + 2]
        if row is None or not covered:
            warnings.append("Some text on this page could not be placed in the grid.")
            continue

        first, last = columns[covered[0]], columns[covered[-1]]
        raw = " ".join(w["text"] for w in sorted(cell_words, key=lambda w: (round(w["top"] / 2.5), w["x0"])))
        parts = _classify(cell_words)

        if not parts["subjects"]:
            warnings.append(f"{row['day'].capitalize()} {first['start']}: text without a subject code was skipped: “{raw}”.")
            continue

        if len(parts["subjects"]) > 1:
            warnings.append(
                f"{row['day'].capitalize()} {first['start']}: several subjects in one cell "
                f"({', '.join(parts['subjects'])}); please check."
            )

        if not first["start"] or not last["end"]:
            warnings.append(f"{row['day'].capitalize()}: the period times could not be read.")
            continue

        entries.append({
            "day": row["day"],
            "start": first["start"],
            "end": last["end"],
            "periods": [columns[i]["label"] for i in covered],
            "subjects": parts["subjects"],
            "faculty": parts["faculty"],
            "rooms": parts["rooms"],
            "batches": parts["batches"],
            "raw": raw,
        })

    lunch = [
        {"start": columns[i]["start"], "end": columns[i]["end"]}
        for i in lunch_columns
        if columns[i]["start"] and columns[i]["end"]
    ]

    return {"code": code, "columns": columns, "entries": entries, "lunch": lunch, "warnings": warnings}


def read_pdf(data: bytes) -> List[Dict[str, Any]]:
    pdfplumber = _require_pdfplumber()

    if not data or data[:4] != b"%PDF":
        raise PdfImportError("That file is not a PDF.")
    if len(data) > MAX_PDF_BYTES:
        raise PdfImportError("That PDF is too large (limit 25 MB).")

    pages = []
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            if len(pdf.pages) > MAX_PAGES:
                raise PdfImportError(f"That PDF has too many pages (limit {MAX_PAGES}).")
            for number, page in enumerate(pdf.pages, start=1):
                result = read_page(page)
                result["page"] = number
                pages.append(result)
    except PdfImportError:
        raise
    except Exception as error:
        logger.exception("Could not read the PDF")
        raise PdfImportError(f"The PDF could not be read ({type(error).__name__}).")

    return pages


# ============================================================
# PART 2 - CONVERSION (entries -> our timetable blocks + class details)
# ============================================================

def _minutes(hhmm: str) -> int:
    hours, minutes = hhmm.split(":")
    return int(hours) * 60 + int(minutes)


def section_details(code: str) -> Dict[str, Any]:
    """
    "3CAI01"   -> semester 3, programme "CAI", section "01"
    "1MTAIE01" -> semester 1, programme "MTAIE", section "01"
    "SIC01"    -> no semester in the name (the admin must choose one)
    """
    match = SECTION_CODE_RE.match((code or "").strip())
    if not match:
        return {"semester": None, "program": "", "section": "", "year_of_study": None}

    semester_digit, program_with_sem, program_plain, section = match.groups()
    semester = int(semester_digit) if semester_digit else None
    if semester is not None and not 1 <= semester <= 8:
        semester = None

    return {
        "semester": semester,
        "program": (program_with_sem or program_plain or "").upper(),
        "section": section,
        "year_of_study": math.ceil(semester / 2) if semester else None,
    }


def guess_academic_year(file_name: str = "", today: Optional[datetime] = None) -> str:
    match = re.search(r"(\d{4})\s*[-_/]\s*(\d{4})", file_name or "")
    if match and int(match.group(2)) == int(match.group(1)) + 1:
        return f"{match.group(1)}-{match.group(2)}"

    today = today or datetime.now()
    start = today.year if today.month >= 6 else today.year - 1
    return f"{start}-{start + 1}"


def _cluster(entries: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    """Entries whose times overlap (lab batches side by side) belong together."""
    clusters: List[List[Dict[str, Any]]] = []
    for entry in sorted(entries, key=lambda e: (_minutes(e["start"]), _minutes(e["end"]))):
        placed = False
        for group in clusters:
            group_start = min(_minutes(e["start"]) for e in group)
            group_end = max(_minutes(e["end"]) for e in group)
            if _minutes(entry["start"]) < group_end and _minutes(entry["end"]) > group_start:
                group.append(entry)
                placed = True
                break
        if not placed:
            clusters.append([entry])
    return clusters


def _distinct(values: List[str]) -> List[str]:
    seen: List[str] = []
    for value in values:
        if value and value not in seen:
            seen.append(value)
    return seen


def _fit(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _entry_label(entry: Dict[str, Any], position: int) -> str:
    return entry["batches"][0] if entry["batches"] else f"#{position}"


def _block_from_cluster(group: List[Dict[str, Any]], counter: int, warnings: List[str]) -> Dict[str, Any]:
    group = sorted(group, key=lambda e: (e["batches"], e["rooms"], e["faculty"]))
    start = min(group, key=lambda e: _minutes(e["start"]))["start"]
    end = max(group, key=lambda e: _minutes(e["end"]))["end"]

    if len({(e["start"], e["end"]) for e in group}) > 1:
        warnings.append(
            f"{group[0]['day'].capitalize()} {start}-{end}: sessions with different times were "
            "combined into one block; please check."
        )

    subjects = _distinct([code for e in group for code in e["subjects"]])
    is_lab = any(code.endswith("_P") for code in subjects)

    if len(group) == 1:
        entry = group[0]
        faculty = entry["faculty"]
        room = ", ".join(_distinct(entry["rooms"]))
        notes = f"Batch {entry['batches'][0]}" if entry["batches"] else ""
    else:
        labelled = [(_entry_label(e, i + 1), e) for i, e in enumerate(group)]
        faculties = _distinct([e["faculty"] for e in group])
        rooms = _distinct([", ".join(e["rooms"]) for e in group])

        faculty = (
            faculties[0] if len(faculties) == 1
            else " · ".join(f"{label}: {e['faculty']}" for label, e in labelled if e["faculty"])
        )
        room = (
            rooms[0] if len(rooms) == 1
            else " · ".join(f"{label}: {', '.join(e['rooms'])}" for label, e in labelled if e["rooms"])
        )
        notes = ""
        if len(subjects) > 1:
            notes = " | ".join(
                f"{label}: {', '.join(e['subjects'])}"
                + (f" · {e['faculty']}" if e["faculty"] else "")
                + (f" · {', '.join(e['rooms'])}" if e["rooms"] else "")
                for label, e in labelled
            )

    return {
        "id": f"imp_{group[0]['day'][:3]}_{counter}",
        "subject": _fit(" / ".join(subjects), 80),
        "courseCode": _fit(" / ".join(subjects), 40),
        "type": "lab" if is_lab else "lecture",
        "faculty": _fit(faculty, 200),
        "room": _fit(room, 120),
        "startTime": start,
        "endTime": end,
        "notes": _fit(notes, 600),
    }


def build_timetable(page: Dict[str, Any], include_lunch: bool = True) -> Tuple[Dict[str, List[Dict[str, Any]]], List[str]]:
    warnings: List[str] = list(page.get("warnings", []))
    timetable: Dict[str, List[Dict[str, Any]]] = {day: [] for day in DAY_NAMES}
    counter = 0

    for day in DAY_NAMES:
        entries = [e for e in page["entries"] if e["day"] == day]
        for group in _cluster(entries):
            counter += 1
            timetable[day].append(_block_from_cluster(group, counter, warnings))

        if include_lunch and page.get("lunch"):
            lunch = page["lunch"][0]
            counter += 1
            timetable[day].append({
                "id": f"imp_{day[:3]}_{counter}", "subject": "Lunch Break", "courseCode": "",
                "type": "lunch", "faculty": "", "room": "", "startTime": lunch["start"],
                "endTime": lunch["end"], "notes": "",
            })

        timetable[day].sort(key=lambda b: _minutes(b["startTime"]))

    return timetable, warnings


def page_config(page: Dict[str, Any]) -> Dict[str, Any]:
    starts = [c["start"] for c in page.get("columns", []) if c.get("start")]
    ends = [c["end"] for c in page.get("columns", []) if c.get("end")]
    config = dict(ct.DEFAULT_CONFIG)
    if starts and ends:
        config["collegeStartTime"] = min(starts, key=_minutes)
        config["collegeEndTime"] = max(ends, key=_minutes)
    return config


def convert_page(page: Dict[str, Any]) -> Dict[str, Any]:
    """One page -> the class + timetable shown on the review screen."""
    details = section_details(page["code"])
    config = page_config(page)
    timetable, warnings = build_timetable(page)

    clean, errors = ct.normalize_timetable(timetable, config)
    cfg, cfg_errors = ct.normalize_config(config)
    problems = errors + cfg_errors

    if details["semester"] is None:
        warnings.append("The section name has no semester number: choose the semester before importing.")
    if not page["entries"]:
        warnings.append("No classes were found on this page.")

    return {
        "code": page["code"],
        "page": page.get("page"),
        "name": page["code"],
        "program": details["program"],
        "section": details["section"],
        "batch": details["program"],  # the programme code is kept as the specialization
        "semester": details["semester"],
        "year_of_study": details["year_of_study"],
        "timetable": clean if not problems else timetable,
        "config": cfg,
        "sessions": ct.count_sessions(clean),
        "class_sessions": ct.count_class_sessions(clean),
        "warnings": warnings,
        "errors": problems,
        "valid": not problems and bool(page["entries"]),
    }


def parse_pdf_bytes(data: bytes, file_name: str = "") -> Dict[str, Any]:
    pages = read_pdf(data)

    classes = [convert_page(page) for page in pages]

    seen: Dict[str, int] = {}
    for item in classes:
        seen[item["code"]] = seen.get(item["code"], 0) + 1
    for item in classes:
        if seen[item["code"]] > 1:
            item["warnings"].append(f"The name {item['code']} appears on {seen[item['code']]} pages.")

    programs: Dict[str, int] = {}
    for item in classes:
        programs[item["program"]] = programs.get(item["program"], 0) + 1

    return {
        "file_name": file_name,
        "pages": len(pages),
        "academic_year": guess_academic_year(file_name),
        "programs": [{"code": code, "classes": count} for code, count in sorted(programs.items())],
        "classes": classes,
    }


# ============================================================
# PART 3 - SAVING
# ============================================================

MAX_CLASSES_PER_IMPORT = 500


def _commit_one(db, admin, academic_year: str, item: Dict[str, Any], publish: bool) -> Dict[str, Any]:
    fields = {
        "name": item.get("name") or item.get("code") or "",
        "department": item.get("department"),
        "academic_year": academic_year,
        "year_of_study": item.get("year_of_study"),
        "semester": item.get("semester"),
        "section": item.get("section"),
        "batch": item.get("batch") or "",
    }

    status = "created"
    try:
        created = ct.create_class(db, admin, fields)
        class_id = created["id"]
    except ct.ServiceError as error:
        if error.status != 409:
            raise
        # The class already exists: add the timetable to it instead.
        clean, errors = ct.normalize_class_fields(fields)
        if errors:
            raise ct.ServiceError(422, ct.format_errors(errors))
        key = ct.class_identity_key(
            clean["department"], clean["academic_year"], clean["semester"],
            clean["section"], clean.get("batch", ""),
        )
        existing = db.classes.find_one({"identity_key": key, "is_active": True})
        if not existing:
            raise
        class_id = str(existing["_id"])
        status = "updated"

    draft = ct.save_draft(
        db, admin, class_id,
        {"timetable": item.get("timetable"), "config": item.get("config")},
    )

    published = False
    publish_error = ""
    if publish:
        try:
            ct.publish(db, admin, draft["id"])
            published = True
        except ct.ServiceError as error:
            publish_error = error.message

    return {
        "code": item.get("code"), "name": fields["name"], "status": status,
        "class_id": class_id, "timetable_id": draft["id"], "published": published,
        "publish_error": publish_error, "sessions": draft.get("sessions"),
    }


def commit_import(db, admin: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
    academic_year = (payload.get("academic_year") or "").strip()
    items = payload.get("classes") or []
    publish = bool(payload.get("publish"))

    if not isinstance(items, list) or not items:
        raise ct.ServiceError(422, "Choose at least one class to import.")
    if len(items) > MAX_CLASSES_PER_IMPORT:
        raise ct.ServiceError(422, f"Too many classes in one import (limit {MAX_CLASSES_PER_IMPORT}).")

    results: List[Dict[str, Any]] = []
    for item in items:
        code = (item or {}).get("code", "")
        try:
            results.append(_commit_one(db, admin, academic_year, item, publish))
        except ct.ServiceError as error:
            results.append({"code": code, "name": item.get("name", code), "status": "failed", "error": error.message})
        except Exception:
            logger.exception("Import failed for %s", code)
            results.append({"code": code, "name": item.get("name", code), "status": "failed",
                            "error": "Unexpected error while saving this class."})

    ct.log_activity(
        db, admin, "timetable_pdf_import", "timetables",
        f"Imported {sum(1 for r in results if r['status'] != 'failed')} of {len(results)} timetables from a PDF.",
    )

    return {
        "created": sum(1 for r in results if r["status"] == "created"),
        "updated": sum(1 for r in results if r["status"] == "updated"),
        "failed": sum(1 for r in results if r["status"] == "failed"),
        "published": sum(1 for r in results if r.get("published")),
        "results": results,
    }


# ============================================================
# PART 4 - FASTAPI ROUTER
# ============================================================

def _decode_pdf(text: str) -> bytes:
    text = (text or "").strip()
    if text.startswith("data:"):
        text = text.split(",", 1)[-1]
    try:
        data = base64.b64decode(text, validate=False)
    except (binascii.Error, ValueError):
        raise PdfImportError("The uploaded file could not be read.")
    return data


def create_timetable_import_router(db, get_user_id_from_token: Callable[[Optional[str]], str]):
    from bson import ObjectId
    from fastapi import APIRouter, Header, HTTPException
    from pydantic import BaseModel

    class PreviewBody(BaseModel):
        file_name: Optional[str] = None
        data: str

    class CommitBody(BaseModel):
        academic_year: str
        publish: Optional[bool] = False
        classes: List[Dict[str, Any]]

    router = APIRouter()

    def require_admin(authorization: Optional[str]) -> Dict[str, Any]:
        user_id = get_user_id_from_token(authorization)
        try:
            user = db.students.find_one({"_id": ObjectId(user_id)})
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid user ID")
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        if user.get("role") != "admin":
            raise HTTPException(status_code=403, detail="Only administrators can import timetables.")
        return user

    @router.post("/api/admin/timetable-import/preview")
    def preview(body: PreviewBody, authorization: str = Header(None)):
        require_admin(authorization)
        try:
            result = parse_pdf_bytes(_decode_pdf(body.data), body.file_name or "")
        except PdfImportError as error:
            raise HTTPException(status_code=400, detail=str(error))
        return {"status": "success", **result}

    @router.post("/api/admin/timetable-import/commit")
    def commit(body: CommitBody, authorization: str = Header(None)):
        admin = require_admin(authorization)
        try:
            result = commit_import(
                db, admin,
                {"academic_year": body.academic_year, "publish": body.publish, "classes": body.classes},
            )
        except ct.ServiceError as error:
            raise HTTPException(status_code=error.status, detail=error.message)
        return {"status": "success", **result}

    return router