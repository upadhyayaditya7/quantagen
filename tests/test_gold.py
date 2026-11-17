"""Tests for the gold answer spans.

The gold files exist to be checked by someone else, so the tests check them: every
quote has to be on the page it names, every answer has to be inside its own quote,
and a document with no text layer must not pretend to have answers. The negative
tests matter as much as the positive one -- a validator that never rejects anything
would make the whole directory decorative.
"""

import copy

import pytest

from bench import fetch, goldlib

GOLD = goldlib.load_all()


def _corpus():
    return {entry["id"]: entry for entry in fetch.load_corpus()["documents"]}


def _one(document_id="arxiv-attention"):
    return copy.deepcopy(GOLD[document_id])


# ------------------------------------------------------------------- the corpus

def test_every_gold_file_matches_a_document():
    corpus = _corpus()
    assert GOLD, "there are no gold files at all"
    for document_id, gold in GOLD.items():
        assert document_id in corpus, f"{document_id} is not in corpus.json"
        assert gold["id"] == document_id
        assert gold["sha256"] == corpus[document_id]["sha256"]
        assert gold["category"] == corpus[document_id]["category"]


def test_gold_validates_without_the_documents():
    corpus = _corpus()
    for document_id, gold in GOLD.items():
        goldlib.validate(gold, corpus[document_id])


def test_gold_quotes_are_on_the_page_they_name():
    corpus = _corpus()
    checked = 0
    for document_id, gold in GOLD.items():
        if not gold["readable"]:
            continue
        pages = goldlib.document_pages(corpus[document_id])
        if pages is None:
            continue
        goldlib.validate(gold, corpus[document_id], pages)
        checked += 1
    if not checked:
        pytest.skip("no curated document is cached; run python bench/fetch.py")


def test_the_full_check_reports_no_problems():
    corpus = _corpus()
    readable = [document_id for document_id, gold in GOLD.items() if gold["readable"]]
    with_documents = any(
        goldlib.document_pages(corpus[document_id]) is not None for document_id in readable)
    assert goldlib.check(with_documents=with_documents) == []


def test_coverage_counts_the_curated_documents():
    counts = goldlib.coverage()
    assert sum(block["with_gold"] for block in counts.values()) == len(GOLD)


# ------------------------------------------------------------------- the rules

def test_an_answer_outside_its_own_quote_is_rejected():
    gold = _one()
    gold["spans"][0]["answer"] = "9.9 million"
    with pytest.raises(goldlib.GoldError, match="not inside its own quote"):
        goldlib.validate(gold)


def test_a_quote_too_short_to_be_evidence_is_rejected():
    # A bare number is not a span: it would pass every extractor that keeps digits.
    gold = _one()
    gold["spans"][0]["quote"] = "4.5"
    with pytest.raises(goldlib.GoldError, match="too short"):
        goldlib.validate(gold)


def test_a_short_cjk_quote_is_judged_by_cjk_length():
    # The character floor is Latin-centric; 10 CJK characters is a sentence fragment.
    gold = _one("jstage-dermatol-133-407")
    gold["spans"][0]["quote"] = "炎症性疾患"  # five characters: a bare phrase
    with pytest.raises(goldlib.GoldError, match="too short"):
        goldlib.validate(gold)
    gold["spans"][0]["quote"] = "慢性炎症性疾患である"  # exactly the ten-character floor
    goldlib.validate(gold)


def test_a_quote_that_is_not_on_its_page_is_rejected():
    gold = _one()
    gold["spans"][0]["page_index"] = 1
    with pytest.raises(goldlib.GoldError, match="not on page"):
        goldlib.validate(gold, None, ["nothing to see here"] * 20)


def test_a_span_count_outside_the_contract_is_rejected():
    gold = _one()
    gold["spans"] = gold["spans"][:3]
    with pytest.raises(goldlib.GoldError, match="expected 8-10"):
        goldlib.validate(gold)


def test_an_unreadable_document_cannot_carry_spans():
    gold = copy.deepcopy(GOLD["scan-pdfbin-clean-300"])
    gold["spans"] = [{
        "id": "invented",
        "kind": "verbatim",
        "quote": "text that a scan without OCR cannot contain",
        "page_index": 0,
    }]
    with pytest.raises(goldlib.GoldError, match="cannot have answer spans"):
        goldlib.validate(gold)


def test_an_unreadable_document_must_state_the_expectation():
    gold = copy.deepcopy(GOLD["scan-pdfbin-clean-300"])
    del gold["expectation"]
    with pytest.raises(goldlib.GoldError, match="correct behaviour"):
        goldlib.validate(gold)


def test_gold_against_different_bytes_is_rejected():
    gold = _one()
    drifted = dict(_corpus()["arxiv-attention"], sha256="0" * 64)
    with pytest.raises(goldlib.GoldError, match="different bytes"):
        goldlib.validate(gold, drifted)


def test_normalise_flattens_line_breaks_and_invisible_characters():
    # Extractors disagree about line breaks, so a quote is compared flattened.
    assert goldlib.normalise("a  b\n\tc\u00a0d\u00ade") == "a b c de"
