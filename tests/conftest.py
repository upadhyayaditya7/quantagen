import copy

import pytest

import main
import rules.parser as parser

# Capture the real tiktoken-backed counter BEFORE any test replaces it,
# so one opted-in test can still exercise the genuine encoder.
_real_get_token_count = main.get_token_count


@pytest.fixture(autouse=True)
def offline_token_counter(monkeypatch):
    """Replace tiktoken with a cheap deterministic counter.

    Keeps the suite fast, network-free and makes stage-attribution math
    exact. Every test runs offline; the real encoder is covered by the
    single opted-in smoke test below.
    """

    def cheap_count(text: str, model: str = "gpt-4o") -> int:
        # No floor: an empty string is genuinely 0 tokens, so calc_'s
        # empty-raw guard stays exercisable. len//4 keeps math deterministic.
        return len(text) // 4

    monkeypatch.setattr(main, "get_token_count", cheap_count)
    yield


@pytest.fixture(autouse=True)
def hermetic_config(monkeypatch):
    """Zero out the blunt percent-based structural trim for the suite.

    Trimming 5% off each end is correct production behavior for documents
    with cover pages/colophons, but it churns the head/tail of the tiny
    synthetic strings most tests use, making them fragile for no benefit.
    The trim itself gets its own explicit tests in test_pipeline.py.
    """

    cfg = copy.deepcopy(parser.load_config())
    cfg["structural_rules"] = {"strip_top_n_percent": 0, "strip_bottom_n_percent": 0}
    monkeypatch.setattr(parser, "load_config", lambda: cfg)
    monkeypatch.setattr(main, "load_config", lambda: cfg)
    yield cfg


@pytest.fixture
def real_token_counter(monkeypatch):
    """Restore the genuine tiktoken-backed counter for one test."""
    monkeypatch.setattr(main, "get_token_count", _real_get_token_count)
    return _real_get_token_count
