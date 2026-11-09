"""Tests for the head-to-head comparison harness (eval/compare.py).

The harness must not need the other tool to be installed: it also accepts
already-extracted text, which is how these tests exercise the scoring without
pulling an OCR stack.
"""

import pytest

from eval import compare


def test_compare_scores_variants_against_the_raw_document():
    raw = "The study reported 42 samples in 2019. " * 30
    shorter = "The study reported 42 samples in 2019."
    items = [{
        "question": "How many samples did the study report?",
        "answer": "42",
        "context": "The study reported 42 samples in 2019.",
    }]

    result = compare.compare(raw, {"trimmed": shorter}, items)

    assert result["reference"]["tokens"] > result["variants"]["trimmed"]["tokens"]
    assert result["variants"]["trimmed"]["answer_spans_retained"] == 1
    assert result["variants"]["trimmed"]["tokens_vs_reference_pct"] < 0
    assert result["variants"]["trimmed"]["answer_spans_delta"] == 0


def test_compare_reports_a_variant_that_loses_an_answer():
    raw = "The study reported 42 samples. A second pass used 7 sites."
    damaged = "The study reported samples. A second pass used 7 sites."
    items = [
        {"question": "How many samples?", "answer": "42",
         "context": "The study reported 42 samples."},
        {"question": "How many sites?", "answer": "7",
         "context": "A second pass used 7 sites."},
    ]

    result = compare.compare(raw, {"damaged": damaged}, items)
    assert result["variants"]["damaged"]["answer_spans_retained"] == 1
    assert result["variants"]["damaged"]["answer_spans_delta"] == -1


def test_unknown_tool_names_the_supported_ones():
    with pytest.raises(ValueError, match="pymupdf4llm"):
        compare.extract_with("no-such-tool", "inputs/x.pdf")
