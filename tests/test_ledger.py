import json

from quantagen import pipeline
from quantagen.pipeline import clean_pipeline, trim_structural_edges


def test_ledger_records_page_number_line_with_reason():
    losses = []
    clean_pipeline("8\nBody line.\n", losses=losses)
    page_entries = [e for e in losses if e["reason"] == "page-number-like line"]
    assert page_entries and page_entries[0]["removed"] == "8"
    assert page_entries[0]["stage"] == "line filter"


def test_ledger_records_symbol_dense_line():
    losses = []
    clean_pipeline("#### ##########\nBody.\n", losses=losses)
    assert any(e["reason"].startswith("symbol-dense") for e in losses)


def test_ledger_records_stamp_line_with_pattern_reason():
    losses = []
    # No trailing digit: "DRAFT copy 9" would be page-number-like and get
    # caught earlier by the line filter; this stamp must reach the patterns.
    out = clean_pipeline("DRAFT copy\nKeep me.\n", losses=losses)
    assert "DRAFT copy" not in out
    stamp = [e for e in losses if e["stage"] == "recurring-noise patterns"]
    assert stamp and "matched" in stamp[0]["reason"]
    assert stamp[0]["removed"] == "DRAFT copy"


def test_ledger_stage_goes_to_first_catcher_not_last():
    # "Page N of M" cannot fully match the filter's short-line regex, so it
    # survives the filter and is attributed to the patterns stage that
    # actually removed it. "8"-style lines ARE attributed to the filter.
    losses = []
    clean_pipeline("Page 12 of 30\nKeep me.\n", losses=losses)
    stages_of = {e["stage"] for e in losses}
    assert stages_of == {"recurring-noise patterns"}

    losses2 = []
    clean_pipeline("8\nKeep me.\n", losses=losses2)
    assert {e["stage"] for e in losses2} == {"line filter"}


def test_ledger_records_truncation_with_heading_reason():
    losses = []
    out = clean_pipeline("Body.\nREFERENCES\n[1] citation spam.\n[2] more spam.", losses=losses)
    trunc = [e for e in losses if e["stage"] == "stop-marker truncation"]
    assert len(trunc) == 1
    assert "REFERENCES" in trunc[0]["reason"]
    assert trunc[0]["chars"] > 0


def test_ledger_records_structural_trim_head_and_tail(monkeypatch):
    cfg = dict(pipeline.load_config())
    cfg["structural_rules"] = {"strip_top_n_percent": 0.1, "strip_bottom_n_percent": 0.1}
    monkeypatch.setattr(pipeline, "load_config", lambda: cfg)
    text = "H" * 100 + "T" * 100
    losses = []
    trim_structural_edges(text, losses=losses)
    stages = {e["reason"]: e for e in losses}
    assert stages["head trim"]["removed"] == "H" * 20
    assert stages["tail trim"]["removed"] == "T" * 20


def test_ledger_entries_carry_tokens_and_chars():
    losses = []
    clean_pipeline("8\n####  ##########\nBody.\n", losses=losses)
    for e in losses:
        assert isinstance(e["tokens"], int)
        assert e["tokens"] >= 0
        assert isinstance(e["chars"], int)
        assert len(e["removed"]) <= 160
        # The full-span carrier is internal: it must never reach report.json.
        assert "_full" not in e


def test_ledger_tokens_account_for_the_whole_span_not_its_preview(monkeypatch):
    # Regression: every entry tokenized its own <=160-char preview, so the
    # book's 18,526-token head+tail trim was ledgered as 100 tokens and
    # report.json contradicted its own stage_tokens summary by 185x. A lexical
    # test (`isinstance(tokens, int)`) is what let that ship; this one checks
    # that the ledger reconciles with the per-stage audit it sits next to.
    cfg = dict(pipeline.load_config())
    cfg["structural_rules"] = {"strip_top_n_percent": 0.1, "strip_bottom_n_percent": 0.1}
    monkeypatch.setattr(pipeline, "load_config", lambda: cfg)

    body = "\n".join(f"Body line {i} carries real words and a value {i}." for i in range(300))
    text = body + "\nREFERENCES\n" + "[1] citation entry.\n" * 150

    losses, stages = [], {}
    clean_pipeline(text, stages, losses)

    ledgered = {}
    for entry in losses:
        ledgered[entry["stage"]] = ledgered.get(entry["stage"], 0) + entry["tokens"]
        assert "_full" not in entry

    # Trim and truncation each remove one or two spans, so their ledger sum has
    # to match the stage summary (token boundaries at a span edge may shift it
    # by a token or two, hence the small tolerance).
    for stage in ("structural trim", "stop-marker truncation"):
        assert stages[stage] > 0
        assert abs(ledgered[stage] - stages[stage]) <= 2, (
            stage, ledgered[stage], stages[stage])

    # No removed span may be accounted for at the price of its preview.
    for entry in losses:
        if entry["chars"] > 200:
            assert entry["tokens"] >= entry["chars"] / 8


def test_clean_text_never_reappears_lossless_sum_notice():
    # Ledger preview is <=160 chars by design; the full removed span of a
    # truncation is too big, so entries are previews, not full text.
    losses = []
    clean_pipeline("Body.\nREFERENCES\n" + "[cite]\n" * 500, losses=losses)
    trunc = losses[0]
    assert len(trunc["removed"]) <= 160
    assert trunc["chars"] > 160


def test_no_losses_when_clean_document():
    losses = []
    out = clean_pipeline("A perfectly normal body paragraph with real words.\n", losses=losses)
    assert losses == []
    assert "perfectly normal body paragraph" in out


def test_ledger_is_backward_compatible_when_unused():
    assert clean_pipeline("Body.\nREFERENCES\njunk") == "Body.\n"
