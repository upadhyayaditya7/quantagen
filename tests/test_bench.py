"""Tests for the benchmark scaffolding: manifest, hashes, adapters.

Everything here is offline. The corpus is referenced rather than bundled, so the
tests exercise the *rules* -- is the manifest well formed, does a wrong hash get
caught, does a missing tool say so -- and only use documents already in `inputs/`
or generated on the fly.
"""

import json
import os
import re

import fitz
import pytest

from bench import fetch
from bench.adapters import ADAPTERS, ExtractionUnavailable, MissingTool, availability, get

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ------------------------------------------------------------------- manifest

def test_manifest_is_valid_and_ids_are_safe():
    corpus = fetch.load_corpus()
    ids = [entry["id"] for entry in corpus["documents"]]
    assert len(ids) == len(set(ids))
    for entry in corpus["documents"]:
        assert entry["why"], f"{entry['id']} does not say what it stresses"


def test_every_document_has_a_category_with_a_target():
    corpus = fetch.load_corpus()
    for entry in corpus["documents"]:
        assert entry["category"] in corpus["coverage"]


def test_coverage_counts_match_the_documents():
    # A stale "current" would overstate the corpus to anyone reading the manifest.
    corpus = fetch.load_corpus()
    counted = {}
    for entry in corpus["documents"]:
        counted[entry["category"]] = counted.get(entry["category"], 0) + 1
    for category, block in corpus["coverage"].items():
        if category.startswith("_"):
            continue
        assert block["current"] == counted.get(category, 0), category


def test_validate_rejects_a_duplicate_id():
    corpus = fetch.load_corpus()
    duplicated = dict(corpus)
    duplicated["documents"] = [corpus["documents"][0], dict(corpus["documents"][0])]
    with pytest.raises(fetch.CorpusError, match="duplicate id"):
        fetch.validate(duplicated)


def test_validate_rejects_an_unsafe_id():
    # The id becomes a filename under bench/cache, so a path is not an id.
    corpus = fetch.load_corpus()
    broken = dict(corpus)
    entry = dict(corpus["documents"][0], id="../../etc/passwd")
    broken["documents"] = [entry]
    with pytest.raises(fetch.CorpusError, match="safe identifier"):
        fetch.validate(broken)


def test_validate_rejects_an_unknown_category():
    corpus = fetch.load_corpus()
    broken = dict(corpus)
    broken["documents"] = [dict(corpus["documents"][0], category="poetry")]
    with pytest.raises(fetch.CorpusError, match="not in coverage"):
        fetch.validate(broken)


def test_validate_rejects_a_malformed_hash():
    corpus = fetch.load_corpus()
    broken = dict(corpus)
    broken["documents"] = [dict(corpus["documents"][0], sha256="abc123")]
    with pytest.raises(fetch.CorpusError, match="sha256"):
        fetch.validate(broken)


# ---------------------------------------------------------------------- hashes

def test_the_bundled_samples_match_their_pinned_hashes():
    # A drift here means inputs/ changed and every published number is stale.
    for entry in fetch.load_corpus()["documents"]:
        if entry["source"]["kind"] == "local":
            assert fetch.status(entry)["state"] == "verified", entry["id"]


def test_mismatch_is_reported_and_never_overwritten(tmp_path):
    target = tmp_path / "doc.pdf"
    target.write_bytes(b"%PDF-1.4 not the real thing")
    entry = {
        "id": "synthetic",
        "category": "single-column",
        "source": {"kind": "local", "path": str(target)},
        "sha256": "0" * 64,
        "why": "hash check",
    }
    assert fetch.status(entry)["state"] == "mismatch"
    with pytest.raises(fetch.CorpusError, match="Delete"):
        fetch.fetch(entry, allow_download=False)


def test_unpinned_document_is_reported_not_scored(tmp_path):
    target = tmp_path / "doc.pdf"
    target.write_bytes(b"%PDF-1.4 unpinned")
    entry = {
        "id": "synthetic",
        "category": "single-column",
        "source": {"kind": "local", "path": str(target)},
        "sha256": None,
        "why": "pin me",
    }
    state = fetch.status(entry)
    assert state["state"] == "unpinned"
    assert fetch.sha256_of(str(target)) in state["detail"]


def test_check_runs_offline_and_exits_zero(capsys):
    assert fetch.main(["--check"]) == 0
    printed = capsys.readouterr().out
    assert "verified" in printed


# -------------------------------------------------------------------- adapters

def test_registry_lists_every_entrant():
    assert set(ADAPTERS) == {"quantagen", "raw", "pymupdf4llm", "markitdown", "docling"}
    assert set(availability()) == set(ADAPTERS)


def test_unknown_adapter_lists_the_known_ones():
    with pytest.raises(KeyError, match="quantagen"):
        get("no-such-tool")


def test_a_missing_tool_explains_how_to_install_it():
    adapter = ADAPTERS["docling"]
    if adapter.available():
        pytest.skip("docling is installed here")
    with pytest.raises(MissingTool, match="pip install docling"):
        adapter.extract("inputs/researchpaperOnSustainableDev.pdf")


def _verified(entry):
    return fetch.status(entry)["state"] == "verified"


def test_cached_documents_match_their_pinned_page_count():
    # A page-count drift means the URL now serves a different document than the
    # hash was recorded against, which no token ratio should be computed over.
    checked = 0
    for entry in fetch.load_corpus()["documents"]:
        if not _verified(entry):
            continue
        doc = fitz.open(fetch.source_path(entry))
        assert doc.page_count == entry["pages"], entry["id"]
        doc.close()
        checked += 1
    if not checked:
        pytest.skip("nothing is cached yet; run python bench/fetch.py")


def test_scanned_fixtures_have_no_text_layer():
    # This is the entire point of the category: if a fixture grew a text layer it
    # would stop testing the no-OCR path while still reporting "scanned".
    checked = 0
    for entry in fetch.load_corpus()["documents"]:
        if entry["category"] != "scanned" or not _verified(entry):
            continue
        doc = fitz.open(fetch.source_path(entry))
        characters = sum(len(page.get_text()) for page in doc)
        doc.close()
        assert characters == 0, f"{entry['id']} has {characters} extractable characters"
        checked += 1
    if not checked:
        pytest.skip("nothing is cached yet; run python bench/fetch.py")


CJK = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff]")


def test_non_latin_documents_really_contain_cjk():
    # Guards against a document drifting into the one category whose rules it
    # does not exercise.
    checked = 0
    for entry in fetch.load_corpus()["documents"]:
        if entry["category"] != "non-latin" or not _verified(entry):
            continue
        doc = fitz.open(fetch.source_path(entry))
        text = "".join(page.get_text() for page in doc)
        doc.close()
        assert CJK.search(text), entry["id"]
        checked += 1
    if not checked:
        pytest.skip("nothing is cached yet; run python bench/fetch.py")


def test_quantagen_adapter_extracts_a_generated_pdf(tmp_path):
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_textbox(
        fitz.Rect(50, 200, 545, 400),
        "The benchmark measures retention and retrieval on the same document. "
        "A sentence long enough to look like body text survives the filter.",
        fontsize=11,
    )
    path = tmp_path / "sample.pdf"
    doc.save(str(path))
    doc.close()

    text = ADAPTERS["quantagen"].extract(str(path))
    assert "retention and retrieval" in text


def test_raw_adapter_is_the_baseline_it_claims_to_be():
    text = ADAPTERS["raw"].extract(os.path.join(REPO, "inputs", "researchpaperOnSustainableDev.pdf"))
    assert "Sustainable Development" in text
    assert "\n" in text  # not reflowed: this is the unedited stream
