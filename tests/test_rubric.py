"""The rubric file is the only place a measurement parameter may live.

Each measuring module keeps a module-level name so its code stays readable.
These tests fail the moment one of those names stops matching
`bench/rubric.json` -- that is the whole point: a copy of a parameter is only
safe while something proves it is not a second, drifting source of truth.
"""

import copy
import json

import pytest

from bench import goldlib, metrics, report, rubric
from bench.adapters import ADAPTERS
from eval import retention, retrieval
from quantagen.pipeline import DEFAULT_TOKEN_MODEL


def _rubric() -> dict:
    return copy.deepcopy(rubric.RUBRIC)


# ------------------------------------------------------------------ the file
def test_the_shipped_rubric_loads_and_validates():
    assert rubric.load() == rubric.RUBRIC


def test_a_misspelt_key_is_rejected_instead_of_ignored():
    data = _rubric()
    data["retrieval"]["chunk_word"] = 120
    with pytest.raises(rubric.RubricError, match="chunk_word"):
        rubric.validate(data)


def test_an_overlap_as_wide_as_the_window_is_rejected():
    data = _rubric()
    data["retrieval"]["chunk_overlap"] = data["retrieval"]["chunk_words"]
    with pytest.raises(rubric.RubricError, match="chunk_overlap"):
        rubric.validate(data)


def test_a_floor_outside_a_percentage_is_rejected():
    data = _rubric()
    data["floors"]["hit_at_3"] = 800
    with pytest.raises(rubric.RubricError, match="hit_at_3"):
        rubric.validate(data)


def test_a_missing_block_is_rejected():
    data = _rubric()
    del data["gold"]
    with pytest.raises(rubric.RubricError, match="gold"):
        rubric.validate(data)


def test_a_missing_file_says_which_file(tmp_path):
    with pytest.raises(rubric.RubricError, match="rubric.json"):
        rubric.load(str(tmp_path / "rubric.json"))


# ------------------------------------------------------- code reads the file
def test_retrieval_settings_are_the_rubric():
    data = rubric.RUBRIC["retrieval"]
    assert (retrieval.CHUNK_WORDS, retrieval.CHUNK_OVERLAP) == (
        data["chunk_words"], data["chunk_overlap"])
    assert retrieval.TOP_K == data["top_k"]
    assert tuple(retrieval.CHUNK_OFFSETS) == tuple(data["window_offsets"])


def test_retrieval_and_retention_floors_are_the_rubric():
    floors = rubric.RUBRIC["floors"]
    assert retrieval.FLOOR_HIT_AT_3 == floors["hit_at_3"]
    assert retrieval.MRR_TOLERANCE == floors["mrr_tolerance"]
    assert retention.FLOOR_NUMBER_RETENTION == floors["number_retention"]
    assert retention.FLOOR_ENTITY_RETENTION == floors["entity_retention"]
    assert retention.FLOOR_REFERENCE_RETENTION == floors["reference_retention"]


def test_shingle_settings_are_the_rubric():
    data = rubric.RUBRIC["retention"]
    assert retention.CONTEXT_SHINGLE_FLOOR == data["context_shingle_floor"]
    assert retention.SHINGLE_SIZE == data["shingle_size"]


def test_gold_bounds_are_the_rubric():
    data = rubric.RUBRIC["gold"]
    assert tuple(goldlib.SPANS_PER_DOCUMENT) == tuple(data["spans_per_document"])
    assert (goldlib.MIN_QUOTE, goldlib.MIN_CJK) == (data["min_quote"], data["min_cjk"])


def test_the_tokenizer_is_the_one_the_pipeline_defaults_to():
    # The benchmark and the product must count tokens the same way, or the
    # headline ratio compares two different rulers.
    assert metrics.rubric.RUBRIC["tokenizer"]["model"] == DEFAULT_TOKEN_MODEL


def test_adapter_order_comes_from_the_registry():
    assert metrics.ADAPTER_ORDER == tuple(ADAPTERS)
    assert set(metrics.ADAPTER_ORDER) == set(ADAPTERS)


# ------------------------------------------------------------- the artifact
def test_report_prose_is_built_from_the_rubric():
    prose = report.rubric_prose(rubric.RUBRIC)
    assert "over 120-word chunks with 30-word overlap" in prose
    assert f"({DEFAULT_TOKEN_MODEL})" in prose


def test_report_prose_follows_a_changed_rubric():
    data = _rubric()
    data["retrieval"]["chunk_words"] = 47
    data["retrieval"]["window_offsets"] = [0]
    prose = report.rubric_prose(data)
    assert "47-word chunks" in prose
    assert "one window alignment" in prose
    assert "120-word" not in prose


def test_render_echoes_the_rubric_the_run_used():
    # The report must describe the run it renders, not whatever the file says
    # now: a rubric edited after scoring cannot retroactively change a table.
    data = _rubric()
    data["retrieval"]["chunk_words"] = 47
    results = {"categories": {}, "documents": [], "rubric": data}
    assert "47-word chunks" in report.render(results)


def test_results_carry_a_snapshot_not_the_live_object():
    snapshot = rubric.snapshot()
    snapshot["retrieval"]["chunk_words"] = 1
    assert rubric.RUBRIC["retrieval"]["chunk_words"] != 1
