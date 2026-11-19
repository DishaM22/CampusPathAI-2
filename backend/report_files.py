"""
Post-event report files: validation only (no database, no FastAPI).

A file is accepted only if
  - its extension is on the allowed list for its role (main report / attachment),
  - its real content (magic bytes / OOXML structure) matches that extension,
  - it is within the size limit.
The stored name is always random; the original name is kept for display only.
"""

import io
import os
import re
import zipfile

MB = 1024 * 1024

MAX_MAIN_BYTES = 15 * MB
MAX_ATTACHMENT_BYTES = 10 * MB
MAX_TOTAL_BYTES = 60 * MB
MAX_ATTACHMENTS = 20

# extension -> (kind that the content must have, content type)
MAIN_TYPES = {
    ".pdf": ("pdf", "application/pdf"),
    ".doc": ("ole", "application/msword"),
    ".docx": (
        "docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ),
}

ATTACHMENT_TYPES = {
    **MAIN_TYPES,
    ".xls": ("ole", "application/vnd.ms-excel"),
    ".xlsx": (
        "xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ),
    ".ppt": ("ole", "application/vnd.ms-powerpoint"),
    ".pptx": (
        "pptx",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ),
    ".jpg": ("jpg", "image/jpeg"),
    ".jpeg": ("jpg", "image/jpeg"),
    ".png": ("png", "image/png"),
    ".webp": ("webp", "image/webp"),
}

PREVIEWABLE = {".pdf", ".jpg", ".jpeg", ".png", ".webp"}

_OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


class ReportFileError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def detect_kind(data):
    """pdf / ole / docx / xlsx / pptx / jpg / png / webp from the CONTENT, else None."""
    if data.startswith(b"%PDF-"):
        return "pdf"
    if data.startswith(_OLE):
        return "ole"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                names = set(archive.namelist())
        except zipfile.BadZipFile:
            return None
        if "[Content_Types].xml" not in names:
            return None
        if "word/document.xml" in names:
            return "docx"
        if "xl/workbook.xml" in names:
            return "xlsx"
        if "ppt/presentation.xml" in names:
            return "pptx"
    return None


def display_name(filename):
    """Original name for display: no path, no control characters, short."""
    name = os.path.basename(str(filename or "").replace("\\", "/"))
    name = re.sub(r"[\x00-\x1f\x7f]", "", name).strip()
    return (name or "file")[:100]


def validate_upload(filename, data, main):
    """
    Returns {"ext", "content_type", "name", "size"} or raises ReportFileError.
    `main` selects the stricter main-report rules.
    """
    name = display_name(filename)
    ext = os.path.splitext(name)[1].lower()
    allowed = MAIN_TYPES if main else ATTACHMENT_TYPES

    if ext not in allowed:
        wanted = "PDF, DOC or DOCX" if main else (
            "PDF, DOC, DOCX, XLS, XLSX, PPT, PPTX, JPG, PNG or WEBP"
        )
        raise ReportFileError(f"Unsupported file type. Allowed: {wanted}.", 415)

    limit = MAX_MAIN_BYTES if main else MAX_ATTACHMENT_BYTES
    if len(data) == 0:
        raise ReportFileError("The file is empty.")
    if len(data) > limit:
        raise ReportFileError(
            f"The file is too large (maximum {limit // MB} MB).", 413
        )

    expected, content_type = allowed[ext]
    if detect_kind(data) != expected:
        raise ReportFileError(
            f"The content of '{name}' does not match its {ext} extension."
        )

    return {"ext": ext, "content_type": content_type, "name": name, "size": len(data)}