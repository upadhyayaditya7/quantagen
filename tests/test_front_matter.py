"""Front/back-matter detection: cut on evidence, never on a percentage.

The percent trim this replaced was measured costing real content on the bundled
6-page paper (49 numbers, 51 entities, including the start of its abstract),
because 5% of a short document is not a cover page. These tests pin the cases
where a percentage is catastrophically wrong and detection is right.
"""

import copy

from quantagen import pipeline
from quantagen.pipeline import detect_front_matter_end, trim_structural_edges

PARAGRAPH = (
    "This paragraph is long enough to read as body text, because a real body "
    "line on a real page carries a sentence like this one."
)

TOC_DOC = (
    "A Book About Things\n"
    "Jane Doe, 2024\n"
    "Preface\n"
    "Some preface prose that the reader may skip.\n"
    "1 Introduction ......... 12\n"
    "2 Methods .............. 34\n"
    "3 Results .............. 56\n"
    "4 Conclusion ........... 78\n"
    + "\n".join([PARAGRAPH] * 10) + "\n"
)


def _cfg(**overrides):
    cfg = copy.deepcopy(pipeline.load_config())
    rules = {"mode": "detect", "max_front_matter_pct": 0.15}
    rules.update(overrides)
    cfg["structural_rules"] = rules
    return cfg


def _use(monkeypatch, **overrides):
    cfg = _cfg(**overrides)
    monkeypatch.setattr(pipeline, "load_config", lambda: cfg)
    return cfg


# --------------------------------------------------------------- contents page

def test_table_of_contents_ends_the_front_matter(monkeypatch):
    _use(monkeypatch)
    end, reason = detect_front_matter_end(TOC_DOC)
    assert TOC_DOC[end:].startswith("This paragraph is long enough")
    assert "table of contents" in reason
    # The cover, author line and preface all go; the body stays.
    assert trim_structural_edges(TOC_DOC) == TOC_DOC[end:]


def test_prose_periods_are_not_a_table_of_contents(monkeypatch):
    # Counting periods per line matched ordinary prose ("i.e.", years, reference
    # lists) and invented a contents page inside the bundled paper's body. Only
    # a real dot leader -- dots with at most one space between them -- counts.
    prose = (
        "Sustainable development is the key, i.e. the overall prosperity of the world, 2014.\n"
        "See Smith et al. 2019. A. B. Author, editors. Handbook, 2018.\n"
        "M. J. Wainwright and M. I. Jordan, volume 1 of Foundations, 2008.\n"
        + PARAGRAPH + "\n"
    )
    _use(monkeypatch)
    _end, reason = detect_front_matter_end(prose)
    assert "table of contents" not in reason


def test_one_dotted_line_is_not_a_table_of_contents(monkeypatch):
    # Dot leaders appear in prose; three in a row are evidence of a contents
    # page, one is not. The cut here is a title block, and the body survives.
    doc = "Short title\n" + "word . . . . . . more\n" + PARAGRAPH + "\n"
    _use(monkeypatch)
    _end, reason = detect_front_matter_end(doc)
    assert "table of contents" not in reason
    assert trim_structural_edges(doc).endswith(PARAGRAPH + "\n")


# ------------------------------------------------------------------ title block

def test_title_block_stops_at_the_first_paragraph(monkeypatch):
    doc = (
        "A Research on Sustainable Development in India\n"
        "Sajjan Choudhuri\n"
        "\n"
        "Abstract— " + PARAGRAPH + "\n"
    )
    _use(monkeypatch)
    trimmed = trim_structural_edges(doc)
    # The title and author go; the abstract is *content* and must survive.
    assert trimmed.startswith("Abstract—")
    assert "Sajjan Choudhuri" not in trimmed


def test_document_that_starts_with_body_text_is_untouched(monkeypatch):
    doc = PARAGRAPH + "\nSecond line of the same document.\n"
    _use(monkeypatch)
    assert detect_front_matter_end(doc) == (0, "")
    assert trim_structural_edges(doc) == doc


def test_short_line_table_is_never_eaten_as_a_title_block(monkeypatch):
    # Hitting the line cap without ever meeting prose means this is a table or
    # a list, not a cover page: a percent trim would delete the top rows.
    doc = "\n".join(["Model Acc F1", "ResNet 0.91 0.90", "BERT 0.88 0.87"] * 8)
    _use(monkeypatch)
    assert detect_front_matter_end(doc) == (0, "")
    assert trim_structural_edges(doc) == doc


def test_leading_blank_lines_are_not_counted_as_a_title(monkeypatch):
    doc = "\n\n\n" + PARAGRAPH + "\n"
    _use(monkeypatch)
    # Only the blank lines can go: the paragraph is the document.
    assert trim_structural_edges(doc).strip() == PARAGRAPH


# --------------------------------------------------------------------- safety

def test_cut_is_refused_when_it_would_exceed_the_configured_cap(monkeypatch):
    # max_front_matter_pct bounds what detection may remove, so a suspicious
    # "title block" of several long lines cannot eat a fifth of the document.
    title_block = "\n".join(["A Rather Long Title Line Filling The Width Of Page One".ljust(80)] * 4)
    doc = title_block + "\n" + "\n".join([PARAGRAPH] * 16) + "\n"

    _use(monkeypatch, max_front_matter_pct=0.01)   # cap = the 200-char floor
    assert trim_structural_edges(doc) == doc

    _use(monkeypatch, max_front_matter_pct=0.15)   # cap now fits the block
    assert trim_structural_edges(doc).startswith(PARAGRAPH)


def test_ledger_records_why_the_front_matter_went(monkeypatch):
    _use(monkeypatch)
    losses = []
    trimmed = trim_structural_edges(TOC_DOC, losses=losses)
    assert len(losses) == 1
    entry = losses[0]
    assert entry["stage"] == "structural trim"
    assert "table of contents" in entry["reason"]
    assert entry["chars"] == len(TOC_DOC) - len(trimmed)
    assert entry["removed"] == TOC_DOC[:len(entry["removed"])]
    assert "_full" not in entry or entry["_full"] == TOC_DOC[:entry["chars"]]


# ------------------------------------------------------- percent mode is intact

def test_percent_mode_still_honours_the_configured_percentages(monkeypatch):
    # Projects that pinned the old behaviour keep it by asking for it.
    _use(monkeypatch, mode="percent", strip_top_n_percent=0.1,
         strip_bottom_n_percent=0.1)
    text = "\n".join(f"line {i} of the document." for i in range(10))
    trimmed = trim_structural_edges(text)
    assert trimmed.startswith("line 1 of the document.")
    assert trimmed.endswith("line 8 of the document.")


def test_missing_mode_defaults_to_percent(monkeypatch):
    cfg = copy.deepcopy(pipeline.load_config())
    cfg["structural_rules"] = {"strip_top_n_percent": 0.5, "strip_bottom_n_percent": 0}
    monkeypatch.setattr(pipeline, "load_config", lambda: cfg)
    assert trim_structural_edges("x" * 100) == "x" * 50
