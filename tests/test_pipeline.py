import os
import re

import main
import rules.parser as parser
from main import calc_efficiency, clean_pipeline, sieve_text, trim_structural_edges


# ---------------------------------------------------------------- parity

def test_ui_imports_the_cli_pipeline():
    # CLI/UI parity is guaranteed by construction: app.py must import and
    # call the same clean_pipeline() the CLI uses.
    with open("app.py", "r", encoding="utf-8") as f:
        src = f.read()
    assert re.search(r"from main import", src)
    assert "clean_pipeline" in src


def test_sieve_text_alias_matches_clean_pipeline():
    raw = "Body line.\nREFERENCES\n[1] junk"
    assert sieve_text(raw) == clean_pipeline(raw)


# ------------------------------------------------------- stage attribution

def test_stage_attribution_reports_each_stage():
    raw = (  # something for every stage except the (zeroed) structural trim;
        # line-filter removals must be large enough to move the cheap counter.
        "Page 14\n####  ########\nDRAFT copy\n"
        + "Body line one.\n" * 3
        + "REFERENCES\n[1] citation junk"
    )
    stages = {}
    clean_pipeline(raw, stages)
    assert set(stages) == {
        "structural trim",
        "line filter",
        "stop-marker truncation",
        "recurring-noise patterns",
    }
    assert stages["stop-marker truncation"] > 0
    assert stages["recurring-noise patterns"] > 0
    assert stages["line filter"] > 0
    assert stages["structural trim"] == 0  # zeroed in the hermetic fixture


def test_stage_tokens_sum_to_total_savings():
    raw = "Stamp line.\nSymbol ####\nBody.\nAPPENDIX\nappendix junk"
    stages = {}
    clean = clean_pipeline(raw, stages)
    saved = main_token_count(raw) - main_token_count(clean)
    assert sum(stages.values()) == saved
    assert all(n >= 0 for n in stages.values())


def main_token_count(text):
    # Uses the (mocked) counter main itself sees, so the math is consistent.
    return main.get_token_count(text)


# ------------------------------------------------- structural trim (direct)

def test_trim_removes_head_percent(monkeypatch):
    cfg = dict(parser.load_config())
    cfg["structural_rules"] = {"strip_top_n_percent": 0.1, "strip_bottom_n_percent": 0}
    # trim_structural_edges resolves its config through main's reference.
    monkeypatch.setattr(main, "load_config", lambda: cfg)
    text = "x" * 200
    assert trim_structural_edges(text) == "x" * 180


def test_trim_removes_tail_percent(monkeypatch):
    cfg = dict(parser.load_config())
    cfg["structural_rules"] = {"strip_top_n_percent": 0, "strip_bottom_n_percent": 0.1}
    monkeypatch.setattr(main, "load_config", lambda: cfg)
    # int(200 * 0.1) == 20 chars removed from the tail.
    text = "A" + "x" * 198 + "B"
    assert trim_structural_edges(text) == "A" + "x" * 179


def test_trim_is_noop_when_both_zero(monkeypatch):
    cfg = dict(parser.load_config())
    cfg["structural_rules"] = {"strip_top_n_percent": 0, "strip_bottom_n_percent": 0}
    monkeypatch.setattr(main, "load_config", lambda: cfg)
    text = "untouched"
    assert trim_structural_edges(text) == text


def test_trim_returns_original_when_it_would_delete_everything(monkeypatch):
    cfg = dict(parser.load_config())
    cfg["structural_rules"] = {"strip_top_n_percent": 0.9, "strip_bottom_n_percent": 0.9}
    monkeypatch.setattr(main, "load_config", lambda: cfg)
    text = "tiny"
    assert trim_structural_edges(text) == text


# --------------------------------------------------------- config loading

def test_config_resolves_relative_to_package_not_cwd(tmp_path, monkeypatch):
    # Regression: config.json was once opened relative to the CWD, breaking
    # runs from any other directory (and UTF-8 c/copyright patterns on Windows).
    old = os.getcwd()
    os.chdir(tmp_path)
    try:
        cfg = parser.load_config()
        assert "stop_markers" in cfg
        assert cfg == parser.load_config()  # deterministic across calls
    finally:
        os.chdir(old)


# ------------------------------------------------------------- efficiency

def test_calc_efficiency_and_edge_cases():
    assert calc_efficiency("a" * 400, "a" * 200) == 50.0
    assert calc_efficiency("", "whatever whatever") == 0
    assert calc_efficiency("x", "x") == 0
    assert calc_efficiency("x" * 400, "") == 100.0


def test_empty_document_is_handled():
    stages = {}
    assert clean_pipeline("", stages) == ""
    assert set(stages) == {
        "structural trim",
        "line filter",
        "stop-marker truncation",
        "recurring-noise patterns",
    }
