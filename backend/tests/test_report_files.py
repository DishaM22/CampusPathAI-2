import io, pathlib, sys, zipfile
import pytest
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import report_files as rf


def zipped(*names):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        for n in names:
            z.writestr(n, "x")
    return b.getvalue()

PDF = b"%PDF-1.4\nhello"
DOCX = zipped("[Content_Types].xml", "word/document.xml")
XLSX = zipped("[Content_Types].xml", "xl/workbook.xml")
PPTX = zipped("[Content_Types].xml", "ppt/presentation.xml")
OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"0" * 20
JPG = b"\xff\xd8\xff\xe0" + b"0" * 20
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 20
WEBP = b"RIFF\x00\x00\x00\x00WEBPVP8 "


@pytest.mark.parametrize("name,data", [("a.pdf", PDF), ("a.docx", DOCX), ("A.DOC", OLE)])
def test_main_ok(name, data):
    assert rf.validate_upload(name, data, True)["size"] == len(data)


@pytest.mark.parametrize("name,data", [("a.xlsx", XLSX), ("a.pptx", PPTX), ("a.jpg", JPG), ("a.png", PNG),
                                       ("a.webp", WEBP), ("a.jpeg", JPG), ("a.xls", OLE), ("a.pdf", PDF)])
def test_attachment_ok(name, data):
    assert rf.validate_upload(name, data, False)["ext"] == "." + name.rsplit(".", 1)[1].lower()


def test_main_rejects_images_and_xlsx():
    for n, d in [("a.jpg", JPG), ("a.xlsx", XLSX)]:
        with pytest.raises(rf.ReportFileError) as e:
            rf.validate_upload(n, d, True)
        assert e.value.status == 415


def test_content_mismatch():
    for n, d in [("a.pdf", JPG), ("a.docx", XLSX), ("a.docx", b"PK\x03\x04junk"), ("a.png", PDF), ("a.doc", PDF)]:
        with pytest.raises(rf.ReportFileError):
            rf.validate_upload(n, d, False)


def test_exe_and_double_ext_rejected():
    for n in ["a.exe", "a.pdf.exe", "a.html", "a.svg", "noext"]:
        with pytest.raises(rf.ReportFileError):
            rf.validate_upload(n, PDF, False)


def test_empty_and_size():
    with pytest.raises(rf.ReportFileError):
        rf.validate_upload("a.pdf", b"", True)
    with pytest.raises(rf.ReportFileError) as e:
        rf.validate_upload("a.pdf", PDF + b"0" * rf.MAX_MAIN_BYTES, True)
    assert e.value.status == 413
    with pytest.raises(rf.ReportFileError):
        rf.validate_upload("a.png", PNG + b"0" * rf.MAX_ATTACHMENT_BYTES, False)


def test_display_name_sanitised():
    assert rf.display_name("../../etc/pa\x00sswd.pdf") == "passwd.pdf"
    assert rf.display_name("C:\\x\\y.pdf") == "y.pdf"
    assert len(rf.display_name("a" * 500)) == 100