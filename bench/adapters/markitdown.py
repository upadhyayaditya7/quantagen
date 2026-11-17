"""markitdown: Microsoft's converter, scored on the same questions."""

from markitdown import MarkItDown


def extract(path: str) -> str:
    return MarkItDown().convert(path).text_content
