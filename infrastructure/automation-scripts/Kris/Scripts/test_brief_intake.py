"""Тесты brief_intake — без сети."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import brief_intake as bi  # noqa: E402


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Ни один тест не ходит в сеть: по умолчанию публичный экспорт «падает»."""

    def _fail(url, timeout, max_bytes):
        raise bi.urllib.error.URLError("network disabled in tests")

    monkeypatch.setattr(bi, "_http_get", _fail)


# --- classify_url -----------------------------------------------------------


@pytest.mark.parametrize(
    "url,expected_kind,expected_id",
    [
        ("https://docs.google.com/document/d/1AbC_dEf-123/edit", "gdoc", "1AbC_dEf-123"),
        ("https://docs.google.com/presentation/d/2XyZ_pres-99/edit#slide=id.p", "gslides", "2XyZ_pres-99"),
        ("https://docs.google.com/spreadsheets/d/3Sheet_id-77/edit?gid=0", "gsheet", "3Sheet_id-77"),
        ("https://www.figma.com/file/FiGmA123/Brief?node-id=1-2", "figma", "FiGmA123"),
        ("https://example.com/brief", "other", None),
    ],
)
def test_classify_url(url, expected_kind, expected_id):
    assert bi.classify_url(url) == (expected_kind, expected_id)


# --- ingest_text ------------------------------------------------------------


def test_ingest_text(tmp_path):
    res = bi.ingest_text("Бриф: ролик про капельное орошение, 30 сек.", tmp_path)
    assert res.kind == "text"
    assert res.dir.parent.name == "inbox"
    pages = res.dir / "pages.md"
    assert pages.exists()
    body = pages.read_text(encoding="utf-8")
    assert "## Страница 1" in body
    assert "капельное орошение" in body
    assert pages in res.paths
    block = bi.prompt_block(res)
    assert "pages.md" in block
    assert str(res.dir) in block


def test_ingest_text_empty_warns(tmp_path):
    res = bi.ingest_text("   ", tmp_path)
    assert res.warnings


# --- ingest_url -------------------------------------------------------------


def test_ingest_url_figma(tmp_path):
    res = bi.ingest_url("https://www.figma.com/file/FiGmA123/Brief", tmp_path)
    assert res.kind == "figma_unsupported"
    assert "PDF-экспорт" in res.summary
    assert (res.dir / "source.url").exists()


def test_ingest_url_gdoc_without_drive(tmp_path):
    res = bi.ingest_url("https://docs.google.com/document/d/1AbC_dEf-123/edit", tmp_path, drive=None)
    assert res.kind == "unknown_link"
    assert res.warnings
    assert any("Drive" in w for w in res.warnings)
    assert "все, у кого есть ссылка" in res.summary


def test_ingest_url_gdoc_public_export(tmp_path, monkeypatch):
    calls = []

    def fake_get(url, timeout, max_bytes):
        calls.append(url)
        return "Публичный текст брифа".encode("utf-8"), "text/plain; charset=utf-8"

    monkeypatch.setattr(bi, "_http_get", fake_get)
    res = bi.ingest_url("https://docs.google.com/document/d/1AbC_dEf-123/edit", tmp_path, drive=None)
    assert res.kind == "gdoc"
    assert calls == ["https://docs.google.com/document/d/1AbC_dEf-123/export?format=txt"]
    assert (res.dir / "source.txt").exists()
    assert "публичная ссылка" in res.summary
    assert "Публичный текст брифа" in (res.dir / "pages.md").read_text(encoding="utf-8")


def test_ingest_url_gsheet_login_page(tmp_path, monkeypatch):
    monkeypatch.setattr(
        bi, "_http_get", lambda url, timeout, max_bytes: (b"<!DOCTYPE html><html>Sign in</html>", "text/html; charset=utf-8")
    )
    res = bi.ingest_url("https://docs.google.com/spreadsheets/d/3Sheet_id-77/edit", tmp_path, drive=None)
    assert res.kind == "unknown_link"
    assert "все, у кого есть ссылка" in res.summary
    assert "PDF" in res.summary
    assert not (res.dir / "source.csv").exists()


def test_ingest_url_other(tmp_path):
    res = bi.ingest_url("https://example.com/brief", tmp_path)
    assert res.kind == "unknown_link"


def test_ingest_url_gdoc_with_drive(tmp_path):
    exported = tmp_path / "exported.txt"
    exported.write_text("Текст из Google Doc", encoding="utf-8")

    class FakeDrive:
        def __init__(self):
            self.calls = []

        def export(self, file_id, mime):
            self.calls.append((file_id, mime))
            return exported

    drive = FakeDrive()
    res = bi.ingest_url("https://docs.google.com/document/d/1AbC_dEf-123/edit", tmp_path, drive=drive)
    assert res.kind == "gdoc"
    assert drive.calls == [("1AbC_dEf-123", "text/plain")]
    assert "Текст из Google Doc" in (res.dir / "pages.md").read_text(encoding="utf-8")


# --- PDF --------------------------------------------------------------------

fitz = pytest.importorskip("fitz", reason="pymupdf не установлен")


def _make_pdf(path: Path) -> Path:
    doc = fitz.open()
    page1 = doc.new_page()
    page1.insert_text((72, 100), "Titul")  # мало текста → PNG
    page2 = doc.new_page()
    page2.insert_textbox(fitz.Rect(50, 50, 550, 750), "Kapelnoe oroshenie. " * 30)  # много текста
    doc.save(str(path))
    doc.close()
    return path


def test_ingest_pdf(tmp_path):
    pdf = _make_pdf(tmp_path / "brief.pdf")
    res = bi.ingest_file(pdf, tmp_path / "wd")
    assert res.kind == "pdf"
    body = (res.dir / "pages.md").read_text(encoding="utf-8")
    assert "## Страница 1" in body
    assert "## Страница 2" in body
    assert "[картинка: page-01.png]" in body
    assert (res.dir / "page-01.png").exists()
    assert not (res.dir / "page-02.png").exists()
    assert "Kapelnoe oroshenie" in body
    assert (res.dir / "source.pdf").exists()
    assert "картинка" in bi.prompt_block(res) or "PNG" in bi.prompt_block(res)


def test_ingest_unsupported(tmp_path):
    f = tmp_path / "brief.xyz"
    f.write_bytes(b"\x00\x01")
    res = bi.ingest_file(f, tmp_path / "wd")
    assert res.kind == "unsupported"
    assert res.warnings


# --- Публичный экспорт Google Slides -----------------------------------------

SLIDES_URL = "https://docs.google.com/presentation/d/2XyZ_pres-99/edit#slide=id.p"


def test_ingest_url_gslides_public_export(tmp_path, monkeypatch):
    pdf_bytes = _make_pdf(tmp_path / "deck.pdf").read_bytes()
    calls = []

    def fake_get(url, timeout, max_bytes):
        calls.append((url, timeout, max_bytes))
        return pdf_bytes, "application/pdf"

    monkeypatch.setattr(bi, "_http_get", fake_get)
    res = bi.ingest_url(SLIDES_URL, tmp_path / "wd", drive=None)
    assert res.kind == "gslides"
    assert calls[0][0] == "https://docs.google.com/presentation/d/2XyZ_pres-99/export/pdf"
    assert calls[0][1] == bi.PUBLIC_EXPORT_TIMEOUT
    assert (res.dir / "source.pdf").read_bytes() == pdf_bytes
    assert res.summary.startswith("Google Slides (публичная ссылка) → PDF. ")
    body = (res.dir / "pages.md").read_text(encoding="utf-8")
    assert "Kapelnoe oroshenie" in body


def test_ingest_url_gslides_login_page(tmp_path, monkeypatch):
    monkeypatch.setattr(
        bi, "_http_get", lambda url, timeout, max_bytes: (b"<html><body>Sign in - Google Accounts</body></html>", "text/html")
    )
    res = bi.ingest_url(SLIDES_URL, tmp_path / "wd", drive=None)
    assert res.kind == "unknown_link"
    assert "Доступ: все, у кого есть ссылка" in res.summary
    assert any("страница входа" in w for w in res.warnings)
    assert not (res.dir / "source.pdf").exists()


def test_ingest_url_gslides_http_error(tmp_path, monkeypatch):
    def fake_get(url, timeout, max_bytes):
        raise bi.urllib.error.HTTPError(url, 404, "Not Found", None, None)

    monkeypatch.setattr(bi, "_http_get", fake_get)
    res = bi.ingest_url(SLIDES_URL, tmp_path / "wd", drive=None)
    assert res.kind == "unknown_link"
    assert any("HTTP 404" in w for w in res.warnings)


def test_ingest_url_gslides_drive_raises_falls_back(tmp_path, monkeypatch):
    pdf_bytes = _make_pdf(tmp_path / "deck.pdf").read_bytes()
    monkeypatch.setattr(bi, "_http_get", lambda url, timeout, max_bytes: (pdf_bytes, "application/pdf"))

    class BrokenDrive:
        def export(self, file_id, mime):
            raise PermissionError("403 insufficient permissions")

    res = bi.ingest_url(SLIDES_URL, tmp_path / "wd", drive=BrokenDrive())
    assert res.kind == "gslides"
    assert "публичная ссылка" in res.summary
    assert any("Drive-экспорт не удался" in w for w in res.warnings)
