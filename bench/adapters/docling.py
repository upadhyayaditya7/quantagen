"""Docling: the strongest competitor on layout and tables, so it must be scored.

If Docling wins a category, the benchmark says so. A comparison that omits the
best available tool is a marketing table, not a measurement.
"""

from docling.document_converter import DocumentConverter


def extract(path: str) -> str:
    result = DocumentConverter().convert(path)
    return result.document.export_to_text()
