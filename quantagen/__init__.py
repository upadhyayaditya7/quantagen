"""Quantagen: turn PDFs into token-efficient, LLM-ready text you can audit.

The cleaning is lossy by design, so it ships with its own evidence: a loss
ledger for every removal, a retention audit, and a retrieval benchmark. See
README.md for the measured numbers and eval/ for how they are produced.
"""

__version__ = "0.1.0"

from .pipeline import (  # noqa: E402  (must follow __version__ above)
    DOCUMENT_REPORTS,
    calc_efficiency,
    clean_pipeline,
    extract_text_from_stream,
    filter_noise,
    get_token_count,
    process_document,
    sieve_text,
    trim_structural_edges,
    write_report,
)

__all__ = [
    "DOCUMENT_REPORTS",
    "calc_efficiency",
    "clean_pipeline",
    "extract_text_from_stream",
    "filter_noise",
    "get_token_count",
    "process_document",
    "sieve_text",
    "trim_structural_edges",
    "write_report",
]
