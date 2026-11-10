"""Image-only PDFs: detect them, never emit an empty file, OCR when possible.

The OCR engine itself (tesseract) is a machine-level dependency, so these tests
stub it. What they pin is the behaviour that matters and can be verified
offline: the failure is detected, reported and never disguised as success.
"""

import io
from pathlib import Path

import fitz
import pytest

from quantagen import pipeline

REPO = Path(__file__).resolve().parents[1]
TEXT_PDF = REPO / "inputs" / "researchpaperOnSustainableDev.pdf"


@pytest.fixture(autouse=True)
def fresh_reports(monkeypatch):
    monkeypatch.setattr(pipeline, "DOCUMENT_REPORTS", [])
    yield


def _scanned_pdf(path: Path, pages: int = 2) -> Path:
    """A PDF whose pages are drawn shapes only: no text layer at all."""
    doc = fitz.open()
    for _ in range(pages):
        page = doc.new_page()
        page.draw_rect(fitz.Rect(60, 60, 400, 500), color=(0, 0, 0), fill=(0.3, 0.3, 0.3))
    doc.save(str(path))
    doc.close()
    return path


def test_text_document_needs_no_ocr():
    with fitz.open(str(TEXT_PDF)) as doc:
        assert pipeline.blank_pages(doc) == []
        assert pipeline.needs_ocr(doc) is False


def test_image_only_document_is_detected(tmp_path):
    with fitz.open(str(_scanned_pdf(tmp_path / "scan.pdf"))) as doc:
        assert pipeline.blank_pages(doc) == [0, 1]
        assert pipeline.needs_ocr(doc) is True


def test_a_stray_page_number_is_not_a_text_layer(tmp_path):
    path = tmp_path / "stamp.pdf"
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), "12")   # scan artifact, no body text
    doc.save(str(path))
    doc.close()

    with fitz.open(str(path)) as doc:
        assert pipeline.blank_pages(doc) == [0]
        assert pipeline.needs_ocr(doc) is True


def test_scanned_pdf_without_an_engine_is_skipped_not_empty(tmp_path, monkeypatch, capsys):
    path = _scanned_pdf(tmp_path / "scan.pdf")
    monkeypatch.setattr(pipeline, "ocr_engine", lambda: None)

    out = tmp_path / "out"
    text, sections = pipeline.process_document(str(path), output_folder=str(out))

    assert (text, sections) == (None, None)
    # The old behaviour wrote a 0-byte .txt and reported a large reduction.
    assert not list(out.glob("*.txt"))
    assert "tesseract" in capsys.readouterr().out
    entry = pipeline.DOCUMENT_REPORTS[0]
    assert entry["status"] == "skipped"
    assert entry["blank_pages"] == 2


def test_ocr_path_labels_the_baseline_it_measured_against(tmp_path, monkeypatch):
    path = _scanned_pdf(tmp_path / "scan.pdf")
    stub = "The study reported 42 samples in 2019. " * 20
    monkeypatch.setattr(pipeline, "ocr_engine", lambda: "tesseract")
    monkeypatch.setattr(pipeline, "ocr_text", lambda doc, **kwargs: stub)

    out = tmp_path / "out"
    text, _sections = pipeline.process_document(str(path), output_folder=str(out))

    assert text and len(list(out.glob("*.txt"))) == 1
    entry = pipeline.DOCUMENT_REPORTS[0]
    assert entry["ocr"] == {"engine": "tesseract", "pages": 2, "dpi": pipeline.OCR_DPI,
                            "language": "eng", "baseline": "ocr text"}
    # Savings are measured from the OCR text, not from an empty text layer.
    assert entry["raw_tokens"] == pipeline.get_token_count(stub)
    assert entry["cleaned_tokens"] <= entry["raw_tokens"]


def test_ui_stream_path_raises_instead_of_showing_nothing(tmp_path, monkeypatch):
    path = _scanned_pdf(tmp_path / "scan.pdf")
    monkeypatch.setattr(pipeline, "ocr_engine", lambda: None)

    with pytest.raises(RuntimeError, match="tesseract"):
        pipeline.extract_text_from_stream(io.BytesIO(path.read_bytes()))
