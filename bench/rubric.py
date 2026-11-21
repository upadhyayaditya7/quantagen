"""The measurement parameters, as data rather than as prose.

Every number that appears in a published claim has to come from a file that can
be diffed, validated and echoed into the artifact quoting it. Before this
module, the retrieval window, the tokenizer model, the span bounds and the CI
floors lived as Python constants in three modules *and* as typed prose in
`bench/report.py`, so the report could describe a chunk size the code no longer
used and nothing failed.

`bench/rubric.json` is now the single source. This module validates it on load:
a misspelt key is an error instead of a silent fallback to a stale value. The
modules that measure read their settings from `RUBRIC`, and
`tests/test_rubric.py` asserts each module-level copy still equals the file, so
a hardcoded value cannot creep back in beside the data it duplicates.
"""

from __future__ import annotations

import copy
import json
import os

BENCH = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(BENCH, "rubric.json")
SCHEMA_VERSION = 1

# Blocks and the keys each must hold. A key outside these sets is rejected:
# "chunk_word" instead of "chunk_words" would otherwise read as a missing value
# in one place and an unused value in the other.
REQUIRED = {
    "tokenizer": ("library", "model"),
    "answers": ("provider", "base_url", "model", "temperature",
                "max_output_tokens", "seed", "prompt_mode", "accuracy_tolerance"),
    "retrieval": ("engine", "chunk_words", "chunk_overlap", "window_offsets", "top_k"),
    "retention": ("reference", "context_shingle_floor", "shingle_size"),
    "gold": ("spans_per_document", "min_quote", "min_cjk"),
    "floors": ("number_retention", "entity_retention", "reference_retention",
               "hit_at_3", "mrr_tolerance"),
}


class RubricError(ValueError):
    """The rubric is missing a field or holds a value the code cannot use."""


def _integer(value, where: str, minimum: int = 0) -> int:
    # `bool` is an `int` in Python, and a JSON `true` where a window size
    # belongs is a typo, not a measurement.
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise RubricError(f"{where} must be an integer >= {minimum}, got {value!r}")
    return value


def _percentage(value, where: str) -> float:
    if (not isinstance(value, (int, float)) or isinstance(value, bool)
            or not 0 <= value <= 100):
        raise RubricError(f"{where} must be a percentage between 0 and 100, got {value!r}")
    return value


def _number(value, where: str, low: float, high: float) -> float:
    if (not isinstance(value, (int, float)) or isinstance(value, bool)
            or not low <= value <= high):
        raise RubricError(f"{where} must be a number between {low} and {high}, got {value!r}")
    return value


def _share(value, where: str, allow_zero: bool = True) -> float:
    if (not isinstance(value, (int, float)) or isinstance(value, bool)
            or not (0 <= value <= 1 if allow_zero else 0 < value <= 1)):
        raise RubricError(f"{where} must be a share between 0 and 1, got {value!r}")
    return value


def _text(value, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RubricError(f"{where} must be a non-empty string, got {value!r}")
    return value


def validate(data: dict, where: str = "bench/rubric.json") -> dict:
    """Raise RubricError for a missing, mistyped or impossible value."""
    if not isinstance(data, dict):
        raise RubricError(f"{where} must hold a JSON object")

    version = _integer(data.get("schema_version"), f"{where}: schema_version", 1)
    if version != SCHEMA_VERSION:
        raise RubricError(f"{where}: schema_version {version}, expected {SCHEMA_VERSION}")

    for block, keys in REQUIRED.items():
        if not isinstance(data.get(block), dict):
            raise RubricError(f"{where}: missing the {block!r} block")
        unknown = [key for key in data[block]
                   if key not in keys and not key.startswith("_")]
        if unknown:
            raise RubricError(f"{where}: unknown {block} key(s): {', '.join(sorted(unknown))}")
        missing = [key for key in keys if key not in data[block]]
        if missing:
            raise RubricError(f"{where}: {block} is missing {', '.join(missing)}")

    tokenizer, retrieval = data["tokenizer"], data["retrieval"]
    retention, gold, floors = data["retention"], data["gold"], data["floors"]

    _text(tokenizer["library"], f"{where}: tokenizer.library")
    _text(tokenizer["model"], f"{where}: tokenizer.model")

    answers = data["answers"]
    _text(answers["provider"], f"{where}: answers.provider")
    _text(answers["base_url"], f"{where}: answers.base_url")
    if not str(answers["base_url"]).startswith(("http://", "https://")):
        raise RubricError(f"{where}: answers.base_url must be an http(s) URL, "
                          f"got {answers['base_url']!r}")
    _text(answers["model"], f"{where}: answers.model")
    _number(answers["temperature"], f"{where}: answers.temperature", 0, 2)
    _integer(answers["max_output_tokens"], f"{where}: answers.max_output_tokens", 1)
    _integer(answers["seed"], f"{where}: answers.seed")
    if answers["prompt_mode"] not in ("rag", "full"):
        raise RubricError(f"{where}: answers.prompt_mode must be 'rag' or 'full', "
                          f"got {answers['prompt_mode']!r}")
    _percentage(answers["accuracy_tolerance"], f"{where}: answers.accuracy_tolerance")

    _text(retrieval["engine"], f"{where}: retrieval.engine")
    words = _integer(retrieval["chunk_words"], f"{where}: retrieval.chunk_words", 1)
    overlap = _integer(retrieval["chunk_overlap"], f"{where}: retrieval.chunk_overlap")
    if overlap >= words:
        raise RubricError(f"{where}: retrieval.chunk_overlap ({overlap}) must be smaller "
                          f"than retrieval.chunk_words ({words})")
    offsets = retrieval["window_offsets"]
    if not isinstance(offsets, list) or not offsets:
        raise RubricError(f"{where}: retrieval.window_offsets must be a non-empty list")
    for index, offset in enumerate(offsets):
        _integer(offset, f"{where}: retrieval.window_offsets[{index}]")
    if offsets != sorted(set(offsets)):
        raise RubricError(f"{where}: retrieval.window_offsets must be unique and ascending")
    _integer(retrieval["top_k"], f"{where}: retrieval.top_k", 1)

    _text(retention["reference"], f"{where}: retention.reference")
    _share(retention["context_shingle_floor"],
           f"{where}: retention.context_shingle_floor", allow_zero=False)
    _integer(retention["shingle_size"], f"{where}: retention.shingle_size", 1)

    bounds = gold["spans_per_document"]
    if not isinstance(bounds, list) or len(bounds) != 2:
        raise RubricError(f"{where}: gold.spans_per_document must be [low, high]")
    low = _integer(bounds[0], f"{where}: gold.spans_per_document[0]", 1)
    high = _integer(bounds[1], f"{where}: gold.spans_per_document[1]", 1)
    if low > high:
        raise RubricError(f"{where}: gold.spans_per_document low ({low}) exceeds high ({high})")
    _integer(gold["min_quote"], f"{where}: gold.min_quote", 1)
    _integer(gold["min_cjk"], f"{where}: gold.min_cjk", 1)

    for key in ("number_retention", "entity_retention", "reference_retention", "hit_at_3"):
        _percentage(floors[key], f"{where}: floors.{key}")
    _share(floors["mrr_tolerance"], f"{where}: floors.mrr_tolerance")

    return data


def load(path: str = PATH) -> dict:
    """Read and validate one rubric file."""
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        raise RubricError(f"{path} is missing: every measured parameter is read from it") from None
    except json.JSONDecodeError as exc:
        raise RubricError(f"{path}: not valid JSON ({exc})") from exc
    return validate(data, path)


# Loaded once: a rubric that does not validate should stop the import, not
# surface as one extractor mysteriously reading zero tokens.
RUBRIC = load()


def snapshot() -> dict:
    """The rubric this process measured with, for embedding in a run's results."""
    return copy.deepcopy(RUBRIC)
