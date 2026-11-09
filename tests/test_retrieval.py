"""Tests for the retrieval benchmark (eval/retrieval.py).

The benchmark's job is to make a claim that retention cannot: the cleaned text
is still findable. These tests pin the parts that could quietly invalidate that
claim -- the chunker losing text, BM25 being non-deterministic, or the "clean"
variant silently not being the pipeline's real output.
"""

import json
import os

import pytest

from eval import retrieval

GOLD_PATH = os.path.join("eval", "gold.json")
PAPER = os.path.join("inputs", "researchpaperOnSustainableDev.pdf")


def _gold():
    with open(GOLD_PATH, encoding="utf-8") as handle:
        return json.load(handle)["documents"]


# ------------------------------------------------------------------- chunking

def test_chunker_keeps_every_word_within_the_window():
    words = [f"w{i}" for i in range(400)]
    windows = retrieval.chunks(" ".join(words), size=120, overlap=30)

    assert all(len(window.split()) <= 120 for window in windows)
    assert windows[0].startswith("w0")
    assert windows[-1].endswith("w399")

    seen = set()
    for window in windows:
        seen.update(window.split())
    assert seen == set(words)


def test_chunker_returns_short_text_unchanged():
    assert retrieval.chunks("a short sentence") == ["a short sentence"]
    assert retrieval.chunks("   ") == []


def test_chunker_rejects_overlap_that_would_never_advance():
    with pytest.raises(ValueError):
        retrieval.chunks("some text", size=10, overlap=10)


# ---------------------------------------------------------------------- bm25

def test_bm25_puts_the_matching_chunk_first():
    index = retrieval.BM25([
        "cats and dogs are common household pets",
        "quantum physics describes subatomic particles",
        "the garden needs watering every morning",
    ])
    assert index.rank("subatomic particles physics")[0] == 1


def test_bm25_ignores_terms_absent_from_the_corpus():
    index = retrieval.BM25(["alpha beta", "gamma delta"])
    scores = index.scores("zzz")
    assert scores == [0.0, 0.0]
    # Ties still resolve deterministically, which matters for CI.
    assert index.rank("zzz") == [0, 1]


# ------------------------------------------------------------------ evaluation

def test_evaluate_reports_the_rank_of_the_answer_chunk():
    chunk_list = ["alpha beta gamma delta", "the study reported 42 samples"]
    items = [{
        "question": "How many samples did the study report?",
        "answer": "42",
        "context": "The study reported 42 samples.",
    }]
    result = retrieval.evaluate(items, "", chunk_list=chunk_list)
    assert result["hit_at_1_pct"] == 100.0
    assert result["mrr"] == 1.0


def test_evaluate_reports_a_missing_answer_instead_of_a_near_miss():
    chunk_list = ["alpha beta gamma delta", "the study reported 42 samples"]
    items = [{
        "question": "Which year was the survey run?",
        "answer": "2019",
        "context": "The survey was conducted during the autumn of 2019.",
    }]
    result = retrieval.evaluate(items, "", chunk_list=chunk_list)
    assert result["hit_at_5_pct"] == 0.0
    assert result["mrr"] == 0.0
    assert result["missed"] == ["Which year was the survey run?"]


# ------------------------------------------------------------------ real files

def test_clean_variant_is_the_real_pipeline_output():
    report = retrieval.audit_pdf(PAPER, {})
    variants = report["variants"]

    assert set(variants) == {"raw", "layout", "clean"}
    assert variants["clean"]["questions"] == 0  # no gold items passed in
    # Cleaning can only remove text, so the index must not grow.
    assert variants["clean"]["words"] < variants["raw"]["words"]
    assert variants["clean"]["chunks"] <= variants["layout"]["chunks"]


def test_cleaning_does_not_hurt_retrieval_on_the_paper():
    report = retrieval.audit_pdf(PAPER, _gold())
    clean, raw = report["variants"]["clean"], report["variants"]["raw"]

    assert clean["questions"] == 8
    assert clean["hit_at_3_pct"] == 100.0
    assert clean["mrr"] >= raw["mrr"] - retrieval.MRR_TOLERANCE
    assert clean["chunks"] < raw["chunks"]
