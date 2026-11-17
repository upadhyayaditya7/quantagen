"""The extractor under test: layout extraction plus the cleaning pipeline."""

import fitz

from quantagen import pipeline
from quantagen.rules import layout

from . import ExtractionUnavailable


def extract(path: str) -> str:
    doc = fitz.open(path)
    try:
        # Same decision process_document() makes, minus the file writing: an
        # image-only PDF must be reported as unreadable, not scored as an empty
        # string that "saved 100% of its tokens".
        if pipeline.needs_ocr(doc):
            if pipeline.ocr_engine() is None:
                raise ExtractionUnavailable("image-only PDF and tesseract is not on PATH")
            return pipeline.clean_pipeline(pipeline.ocr_text(doc))
        return pipeline.clean_pipeline(layout.extract_text(doc))
    finally:
        doc.close()
