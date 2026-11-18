"""Tests for the script-aware metric layer and the benchmark scorer.

Two bugs motivated this module: an ASCII-only normaliser that scored every CJK
span 0.0, and a shingle comparison that made any quote shorter than five tokens
unscoreable. Both were metric failures that read as extractor failures, so the
tests here pin the properties that made them visible rather than the exact
numbers a particular document happens to produce.
"""

import fitz
import pytest

from bench import metrics, report
from bench.adapters import ADAPTERS
from eval import retention
from eval.retrieval import BM25, tokenize


# ------------------------------------------------------------------- tokeniser
def test_cjk_is_tokenised_per_character():
    assert retention.script_tokens("日本語の文章") == list("日本語の文章")


def test_spaced_scripts_stay_word_tokens():
    assert retention.script_tokens("The QOL score is 42.") == ["the", "qol", "score", "is", "42"]


def test_retrieval_and_shingles_share_one_tokeniser():
    # The two used to be separate ASCII-only regexes; both failed silently on
    # the same documents. Sharing the function is the fix, not a coincidence.
    assert tokenize("深層学習 2016") == retention.script_tokens("深層学習 2016")


# -------------------------------------------------------------------- coverage
def test_cjk_quote_is_visible_to_the_shingle_metric():
    quote = "の質（quality of life：QOL）は低下しており"
    assert retention.context_coverage(quote, "前置き" + quote + "後書き") == 1.0
    assert retention.context_coverage(quote, "全く別の文章です") == 0.0


def test_short_quote_is_scored_instead_of_failing_by_construction():
    # A four-token label used to be compared against five-token windows, so it
    # could never match, however faithful the extraction was.
    assert retention.context_coverage(
        "2-layer Neural Net", "A “2-layer Neural Net” hides one layer.") == 1.0
    assert retention.context_coverage("2-layer Neural Net", "A 5-layer network.") == 0.0


def test_verbatim_span_is_judged_on_context_alone():
    quote = "患者の生活の質は低下しており"
    assert retention.span_survives(quote, "本文 " + quote + " 以上") is True
    assert retention.span_survives(quote, "本文のみ") is False


def test_cjk_answer_inside_cjk_prose_is_found():
    result = retention.answer_span_recall(
        "", "研究では生活の質が低下していた。", 
        [{"question": "何が低下したか", "answer": "生活の質",
          "context": "研究では生活の質が低下していた。"}])
    assert result["retained"] == 1


def test_bm25_ranks_cjk_text():
    index = BM25(["深層学習の研究", "気象観測の統計"])
    assert index.rank("深層学習")[0] == 0


# --------------------------------------------------------------------- scoring
def _gold():
    return {"readable": True, "spans": [
        {"id": "measured", "kind": "answer", "question": "What was measured?",
         "answer": "42", "quote": "The measured value was 42 units."},
        {"id": "second", "kind": "verbatim",
         "quote": "A second sentence survives here."},
    ]}


REFERENCE = "The measured value was 42 units.\nA second sentence survives here."


def test_score_reports_cost_retention_and_spans():
    record = metrics.score(REFERENCE, REFERENCE, _gold())
    assert record["token_ratio"] == 1.0
    assert record["number_retention"] == 100.0
    assert record["spans"]["retained"] == 2
    assert record["spans"]["lost"] == []


def test_score_names_each_lost_span():
    record = metrics.score(REFERENCE, "Unrelated text entirely.", _gold())
    assert record["spans"]["lost"] == ["measured", "second"]
    assert record["number_retention"] == 0.0


def test_an_image_only_document_has_no_ratio_to_report():
    gold = {"readable": False, "expectation": "report a skip", "spans": []}
    record = metrics.score("", "", gold)
    assert record["token_ratio"] is None
    assert record["number_retention"] is None
    assert record["spans"]["checked"] == 0


def test_run_document_uses_the_raw_extractor_as_its_own_baseline(tmp_path):
    doc = fitz.open()
    page = doc.new_page()
    page.insert_textbox(fitz.Rect(50, 100, 545, 400),
                        "The benchmark scores one document at a time. "
                        "A sentence long enough to survive the line filter.",
                        fontsize=11)
    path = tmp_path / "sample.pdf"
    doc.save(str(path))
    doc.close()

    entry = {"id": "synthetic", "category": "single-column", "pages": 1,
             "source": {"kind": "local", "path": str(path)}}
    record = metrics.run_document(entry, adapters=("raw",),
                                  gold={"readable": True, "spans": []})
    assert record["tools"]["raw"]["status"] == "ok"
    assert record["tools"]["raw"]["token_ratio"] == 1.0


def test_a_missing_tool_is_a_row_not_a_crash(tmp_path):
    if ADAPTERS["docling"].available():
        pytest.skip("docling is installed here")
    doc = fitz.open()
    doc.new_page().insert_text(fitz.Point(72, 72), "Some text.")
    path = tmp_path / "sample.pdf"
    doc.save(str(path))
    doc.close()

    entry = {"id": "synthetic", "category": "single-column", "pages": 1,
             "source": {"kind": "local", "path": str(path)}}
    record = metrics.run_document(entry, adapters=("docling",), gold={})
    assert record["tools"]["docling"]["status"] == "unavailable"
    assert "pip install docling" in record["tools"]["docling"]["detail"]


# ---------------------------------------------------------------------- report
def _results():
    return {
        "categories": {
            "tables": {"documents": 6, "target": 6, "gold": 1},
            "textbook": {"documents": 1, "target": 6, "gold": 0},
        },
        "documents": [{
            "id": "table-example",
            "category": "tables",
            "pages": 41,
            "reference": {"tokens": 100, "chars": 400},
            "gold": {"readable": True, "spans": 8},
            "tools": {
                "quantagen": {
                    "status": "ok", "version": "quantagen 0.1.0", "seconds": 0.5,
                    "tokens": 80, "chars": 320, "token_ratio": 0.8,
                    "number_retention": 99.0, "entity_retention": 100.0,
                    "reference_retention": 100.0,
                    "spans": {"checked": 8, "retained": 7, "lost": ["aje"],
                              "answers": 8, "verbatim": 0},
                    "retrieval": {"questions": 8, "hit_at_3": 100.0, "mrr": 0.9},
                },
                "docling": {"status": "unavailable",
                            "detail": ADAPTERS["docling"].install,
                            "version": "version unknown"},
            },
        }],
    }


def test_report_is_byte_identical_when_nothing_changed():
    results = _results()
    assert report.render(results) == report.render(results)


def test_report_states_which_tools_were_not_measured():
    rendered = report.render(_results())
    assert "not installed on this machine" in rendered
    assert "pip install docling" in rendered


def test_report_names_every_lost_span():
    assert "`aje`" in report.render(_results())


def test_report_lists_categories_below_target_without_numbers():
    rendered = report.render(_results())
    assert "Not yet published" in rendered
    assert "`textbook` (1/6)" in rendered


def test_report_never_prints_a_timestamp_by_default():
    assert "Generated at" not in report.render(_results())


def test_timings_are_opt_in_and_do_not_enter_the_reproducible_table():
    default = report.render(_results())
    assert "s/doc" not in default
    assert "not byte-reproducible" in report.render(_results(), timings=True)
