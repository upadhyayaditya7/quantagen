"""Offline tests for layout-aware extraction.

The ordering, furniture and reflow rules are pure functions over synthetic
blocks, so almost everything here needs no PDF at all. One end-to-end test
builds a throwaway PDF with PyMuPDF to exercise the real extraction path.
"""

import fitz

from rules import layout
from rules.layout import Line, PageBlocks, TextBlock


def make_block(rows, x0=50.0, x1=300.0, y0=100.0, step=12.0, size=10.0, bold=False):
    """Build a TextBlock from line texts laid out top to bottom."""
    if isinstance(rows, str):
        rows = [rows]
    lines = tuple(
        Line(text=text, y0=y0 + i * step, y1=y0 + i * step + step - 2)
        for i, text in enumerate(rows)
    )
    return TextBlock(
        x0=x0, y0=y0, x1=x1, y1=y0 + len(rows) * step,
        lines=lines, size=size, bold=bold,
    )


def make_page(height=842.0, header=None, number=None, body=("Body text about layout.",), y0=100.0):
    """A page made of an optional running head, an optional page number, body."""
    blocks = []
    if header is not None:
        blocks.append(make_block([header], x0=50.0, x1=545.0, y0=20.0))
    if number is not None:
        blocks.append(make_block([number], x0=280.0, x1=310.0, y0=820.0))
    blocks.append(make_block(list(body), x0=50.0, x1=545.0, y0=y0))
    return PageBlocks(height=height, blocks=tuple(blocks))


# ------------------------------------------------------------- reading order

def test_two_column_page_reads_column_by_column():
    left = [
        make_block(["Left alpha", "left beta", "left gamma"], x0=50, x1=290, y0=100),
        make_block(["Left delta", "left epsilon", "left zeta"], x0=50, x1=290, y0=140),
    ]
    right = [
        make_block(["Right alpha", "right beta", "right gamma"], x0=305, x1=545, y0=104),
        make_block(["Right delta", "right epsilon", "right zeta"], x0=305, x1=545, y0=144),
    ]
    ordered = [block.text for block in layout.order_blocks(left + right)]
    # The whole left column, then the whole right column: column-major, not
    # row-major, even though paragraph gaps in the two columns nearly align.
    assert ordered == [
        "Left alpha left beta left gamma",
        "Left delta left epsilon left zeta",
        "Right alpha right beta right gamma",
        "Right delta right epsilon right zeta",
    ]


def test_structural_row_gap_puts_running_head_first():
    header = make_block(["RUNNING HEAD"], x0=200, x1=400, y0=20)
    body = make_block(["Body first line", "body second line"], x0=50, x1=545, y0=100)
    footer = make_block(["123"], x0=280, x1=310, y0=800)
    ordered = [block.text for block in layout.order_blocks([body, footer, header])]
    assert ordered == [
        "RUNNING HEAD",
        "Body first line body second line",
        "123",
    ]


def test_single_column_blocks_are_ordered_top_down():
    blocks = [
        make_block(["third"], y0=300),
        make_block(["first"], y0=100),
        make_block(["second"], y0=200),
    ]
    assert [block.text for block in layout.order_blocks(blocks)] == [
        "first", "second", "third",
    ]


def test_touching_blocks_are_not_split_into_columns():
    # A 2pt gap is letter spacing, not a gutter: justified single-column text
    # must never be reordered into fake columns.
    first = make_block(["aaaa", "bbbb"], x0=50, x1=300, y0=100)
    second = make_block(["cccc"], x0=302, x1=500, y0=104)
    assert [block.text for block in layout.order_blocks([second, first])] == [
        "aaaa bbbb", "cccc",
    ]


# -------------------------------------------------------------------- reflow

def test_reflow_joins_hard_wrapped_lines():
    block = make_block(["The quick brown fox", "jumps over the dog."])
    assert layout.reflow_block(block) == ["The quick brown fox jumps over the dog."]


def test_reflow_repairs_end_of_line_hyphenation():
    block = make_block(["The probability distri-", "bution is normal."])
    assert layout.reflow_block(block) == ["The probability distribution is normal."]


def test_reflow_keeps_hyphen_before_uppercase_tail():
    # Conservative: only a lowercase continuation proves a broken word.
    block = make_block(["Two-step Con-", "Volution kernels."])
    assert layout.reflow_block(block) == ["Two-step Con- Volution kernels."]


def test_reflow_splits_paragraphs_on_wide_internal_gap():
    lines = (
        Line("First paragraph ends here.", 100, 110),
        Line("Still the same paragraph.", 112, 122),
        Line("Second paragraph starts.", 200, 210),
        Line("And continues here.", 212, 222),
    )
    block = TextBlock(x0=50, y0=100, x1=500, y1=222, lines=lines)
    assert layout.reflow_block(block) == [
        "First paragraph ends here. Still the same paragraph.",
        "Second paragraph starts. And continues here.",
    ]


# ----------------------------------------------------------------- furniture

def test_repeated_margin_line_is_dropped_as_furniture():
    pages = [
        make_page(header="IJRTE JOURNAL", number=str(100 + i))
        for i in range(6)
    ]
    dropped = layout.classify_furniture(pages)
    reasons = list(dropped.values())
    assert any("repeated margin line on 6/6 pages" == reason for reason in reasons)


def test_margin_line_that_also_appears_in_body_is_kept():
    # The document title sits in the running-head position *and* on page 1 as
    # real content, so it is not furniture.
    pages = [
        make_page(header="Document Title", number=str(100 + i), body=("Document Title",))
        for i in range(6)
    ]
    dropped = layout.classify_furniture(pages)
    assert (0, 0) not in dropped


def test_page_number_is_dropped_in_short_document():
    # Two pages give no repetition evidence, but a bare number in the margin is
    # a page number whatever the document length.
    pages = [make_page(header=None, number="7") for _ in range(2)]
    dropped = layout.classify_furniture(pages)
    assert dropped[(0, 0)] == "page number in margin band"


def test_long_body_block_in_margin_band_is_never_furniture():
    long_line = "x" * 400  # over max_furniture_chars
    pages = [make_page(header=long_line) for _ in range(6)]
    dropped = layout.classify_furniture(pages)
    assert not any("repeated" in reason for reason in dropped.values())


def test_footer_straddling_the_band_boundary_is_furniture():
    # A footer block that starts just above the bottom band and runs to the page
    # edge is still a footer: 0.5 of its height is inside the band.
    pages = []
    for i in range(6):
        page = make_page(header=None, number=None)
        footer = make_block(["Published by Journal", "Retrieval Number 42"],
                            x0=50, y0=770)
        pages.append(PageBlocks(height=842.0, blocks=page.blocks + (footer,)))
    dropped = layout.classify_furniture(pages)
    assert "repeated" in dropped[(0, 1)]


def test_running_title_removed_but_body_title_kept():
    # The same text is the document title once (page 1, body position) and the
    # running head on every page: the running heads go, the title stays.
    pages = []
    for i in range(6):
        body = ("Document Title",) if i == 0 else ("Body paragraph one.",)
        pages.append(make_page(header="Document Title", number=str(100 + i), body=body))
    dropped = layout.classify_furniture(pages)
    assert (0, 0) in dropped      # running head in the top band
    assert (0, 2) not in dropped  # the real title in the body


def test_furniture_needs_repetition_threshold():
    pages = []
    for i in range(6):
        header = "Journal name" if i < 2 else None
        pages.append(make_page(header=header, number=str(100 + i)))
    dropped = layout.classify_furniture(pages)
    assert not any("repeated" in reason for reason in dropped.values())


# ------------------------------------------------------------------ stitching

def test_stitch_rejoins_paragraph_broken_by_page_break():
    paragraphs = [
        "This paragraph is interrupted by a page break and",
        "continues on the next page with lowercase text.",
    ]
    assert layout.stitch_paragraphs(paragraphs) == [
        "This paragraph is interrupted by a page break and "
        "continues on the next page with lowercase text."
    ]


def test_stitch_survives_a_hyphenated_page_break():
    paragraphs = [
        "The last line of the page ends in a split distri-",
        "bution across the boundary.",
    ]
    assert layout.stitch_paragraphs(paragraphs) == [
        "The last line of the page ends in a split distribution across the boundary."
    ]


def test_stitch_keeps_headings_and_labels_separate():
    paragraphs = ["Machine Learning: The Basics", "and the body starts here."]
    headings = [True, False]
    assert layout.stitch_paragraphs(paragraphs, headings) == paragraphs
    short_addressing = ["model", "hypothesis"]
    assert layout.stitch_paragraphs(short_addressing) == short_addressing


# ----------------------------------------------------------------- end to end

def test_extract_text_end_to_end_from_generated_pdf(tmp_path):
    doc = fitz.open()
    for i in range(6):
        page = doc.new_page(width=595, height=842)
        page.insert_text((180, 28), "RUNNING HEAD", fontsize=8)
        page.insert_text((285, 826), str(101 + i), fontsize=8)
        page.insert_textbox(
            fitz.Rect(50, 90, 545, 400),
            "A paragraph long enough to keep its place in the output stream. "
            "It even contains a split word: informa-\ntion, which must be "
            "repaired into a single token by the reflow stage.",
            fontsize=11,
        )
    path = tmp_path / "layout.pdf"
    doc.save(str(path))
    doc.close()

    opened = fitz.open(str(path))
    losses = []
    text = layout.extract_text(opened, losses)
    opened.close()

    assert "RUNNING HEAD" not in text
    assert "101" not in text.splitlines() and "106" not in text.splitlines()
    assert "information" in text
    assert "A paragraph long enough to keep its place" in text
    assert all(entry["stage"] == "layout extraction" for entry in losses)
    assert len(losses) >= 12  # 6 page numbers + 6 running heads
