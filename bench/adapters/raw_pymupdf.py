"""The baseline every other number is compared against: raw PyMuPDF text."""

import fitz


def extract(path: str) -> str:
    doc = fitz.open(path)
    try:
        return "\n".join(page.get_text() for page in doc)
    finally:
        doc.close()
