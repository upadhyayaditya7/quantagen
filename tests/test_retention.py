"""Tests for the retention audit (eval/retention.py).

The audit exists to keep the project honest about what cleaning destroys, so
these tests are mostly about the *bookkeeping*: scope decisions must be
reported without being forgiven, damage must never be forgiven, and the audit
must measure the real pipeline rather than a parallel reimplementation.
"""

import copy
import json
import os

import fitz

from quantagen import pipeline
from quantagen.rules import parser
from eval import retention

GOLD_PATH = os.path.join("eval", "gold.json")


def _gold():
    with open(GOLD_PATH, encoding="utf-8") as handle:
        return json.load(handle)["documents"]


# ------------------------------------------------------------------ extractors

def test_numbers_are_normalised_for_commas_and_decimals():
    counts = retention.numbers_in("Cost 1,234.5 in 2019, up 3.5%.")
    assert counts["1234.5"] == 1
    assert counts["2019"] == 1
    assert counts["3.5"] == 1


def test_numeric_answers_need_the_whole_value():
    numbers = retention.numbers_in("100 and 1,010")
    assert "100" in numbers
    assert "10" not in numbers  # a "10" answer must not match "100"


def test_entities_keep_acronyms_and_drop_sentence_words():
    entities = retention.entities_in("The ICMR study was done in India.")
    assert {"ICMR", "India"} <= entities
    assert "The" not in entities


def test_references_capture_figures_tables_and_citations():
    refs = retention.references_in("See Figure 3, Table 2 and [12] for details.")
    assert refs == {"Figure 3", "Table 2", "[12]"}


# --------------------------------------------------------------- pipeline link

def test_stage_chain_reproduces_the_real_pipeline():
    # If clean_pipeline() ever gains, loses or reorders a stage, the audit
    # would silently measure a different pipeline. This is the tripwire.
    text = "\n".join(["Heading", "8", "Body line one 2019.", "______", "More 42 text."])
    assert retention.stage_texts(text)[-1][1] == pipeline.clean_pipeline(text)


# ------------------------------------------------------------- forgiveness

def test_scope_losses_are_reported_but_not_penalised(monkeypatch):
    cfg = copy.deepcopy(parser.load_config())
    cfg["structural_rules"] = {"strip_top_n_percent": 0.25, "strip_bottom_n_percent": 0}
    monkeypatch.setattr(pipeline, "load_config", lambda: cfg)

    text = "\n".join(["2019 front matter", "second line", "third line", "fourth line"])
    report = retention.audit_text(text)

    trim = report["numbers"]["scope_removed"].get("structural trim")
    assert trim and trim["count"] >= 1          # the cost is reported...
    assert report["numbers"]["retention_pct"] == 100.0   # ...but it is the documented scope


def test_line_filter_damage_is_never_forgiven():
    # A bare numeral is dropped by the line filter; that is damage, not scope.
    report = retention.audit_text("Body text 2019.\n42\nMore body text.")
    assert report["numbers"]["unintended_losses"]["line filter"]["count"] == 1
    assert report["numbers"]["retention_pct"] < 100.0


# -------------------------------------------------------------- answer spans

def test_answer_span_requires_the_surrounding_sentence():
    gold = [{
        "question": "By which year are the targets to be achieved?",
        "answer": "2030",
        "context": "The study recommended reforms for achieving 2030 targets of food security.",
    }]
    orphan = retention.answer_span_recall("", "2030 appears alone here.", gold)
    assert orphan["retained"] == 0

    survived = retention.answer_span_recall(
        "", "The study recommended reforms for achieving 2030 targets of food security.", gold)
    assert survived["retained"] == 1


def test_answer_span_recall_rejects_substring_numeric_match():
    text = "The threshold is 10.53 units above the baseline."
    items = [{"question": "What is the threshold?", "answer": "0.5",
              "context": "The threshold is 10.53 units above the baseline."}]
    assert retention.answer_span_recall(text, text, items)["retained"] == 0


def test_answer_span_recall_keeps_an_exact_decimal():
    text = "See Section 3.1 for the shared hypothesis space."
    items = [{"question": "Which section?", "answer": "3.1",
              "context": "See Section 3.1 for the shared hypothesis space."}]
    assert retention.answer_span_recall(text, text, items)["retained"] == 1


def test_context_coverage_tolerates_reflow_but_not_deletion():
    context = "Mountain hikers might be interested in data points that represent tours."
    reflowed = "Mountain hikers might be\ninterested in data points that represent tours."
    assert retention.context_coverage(context, reflowed) == 1.0
    assert retention.context_coverage(context, "Something else entirely.") < 0.2


# ------------------------------------------------------------- reproducibility

def test_extractor_is_pinned_and_reportable():
    # The floors are calibrated against one extractor build. A newer PyMuPDF
    # splits figure text differently and moves the book's number retention by
    # ~3 points with no code change, so a loose "PyMuPDF>=1.24" is how CI came
    # to disagree with the numbers in README.md. The pin is a test, not a habit.
    version = retention.extractor_version()
    assert version.startswith("PyMuPDF ") and version != "PyMuPDF unknown"

    with open("requirements.txt", encoding="utf-8") as handle:
        pins = [line.strip() for line in handle
                if line.strip().lower().startswith("pymupdf")]
    assert len(pins) == 1
    assert "==" in pins[0]


# ------------------------------------------------------------- real documents

def test_gold_contexts_exist_in_the_source_documents():
    # Guards against a curated question that was never really in the PDF.
    for name, items in _gold().items():
        doc = fitz.open(os.path.join("inputs", name))
        raw = "\n".join(page.get_text() for page in doc)
        for item in items:
            coverage = retention.context_coverage(item["context"], raw)
            assert coverage >= 0.9, (name, item["question"], coverage)


def test_paper_retains_every_number_entity_and_answer_span():
    report = retention.audit_pdf(
        os.path.join("inputs", "researchpaperOnSustainableDev.pdf"), _gold())

    assert report["numbers"]["gold"] > 50
    assert report["numbers"]["retention_pct"] == 100.0
    assert report["entities"]["retention_pct"] == 100.0
    assert report["references"]["retention_pct"] == 100.0

    spans = report["answer_spans"]
    assert spans["checked"] >= 8
    assert spans["retained"] == spans["checked"]

    # Reference truncation is scope, and its cost is disclosed, not hidden.
    assert report["numbers"]["scope_removed"]["stop-marker truncation"]["count"] > 0
