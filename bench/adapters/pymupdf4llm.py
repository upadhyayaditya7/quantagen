"""pymupdf4llm: Markdown output, the comparison eval/compare.py already uses."""

import pymupdf4llm


def extract(path: str) -> str:
    return pymupdf4llm.to_markdown(path)
