"""Layout-aware extraction: reading order, page furniture, and reflow.

PyMuPDF's per-page text is not reading order. On a two-column page the stream
interleaves the columns, and on many pages the running head, the footer and the
page number are emitted *between* body blocks. On top of that, every visual line
ends in a hard break, so words are split across lines ("informa-" / "tion") and
paragraph structure is lost. Cleaning such a stream still works, but it is not
something you would feed to a model.

This module turns a document into reading-ordered, paragraph-shaped text:

  page_blocks()         text blocks with bbox, font size and per-line geometry
  classify_furniture()  repeating margin lines (running heads/footers, page
                        numbers) that can be dropped with attribution
  order_blocks()        recursive XY-cut: rows first, then columns
  reflow_block()        paragraphs: de-hyphenated, hard wraps removed
  extract_text()        glue used by both the CLI and the UI

Ordering/reflow/furniture logic is pure (blocks in, blocks or text out), so it
is tested without any PDF; only page_blocks() and extract_text() touch PyMuPDF.
"""

import math
import re
import statistics
from collections import defaultdict
from dataclasses import dataclass

from rules import parser

# Fallback tunables; config.json ["layout"] overrides any of them.
DEFAULT_LAYOUT = {
    "top_band_pct": 0.08,          # share of page height treated as top margin
    "bottom_band_pct": 0.08,       # share of page height treated as bottom margin
    "page_number_band_pct": 0.15,  # wider band for bare page numbers
    "furniture_min_pages": 3,      # fewer pages than this: no repeat evidence
    "furniture_page_ratio": 0.5,   # line must repeat on this share of pages
    "max_furniture_chars": 240,    # only short lines can be furniture
    "min_gutter_pt": 8.0,          # narrowest whitespace band that splits columns
    "row_gap_pt": 12.0,            # vertical gap that separates structural rows
    "heading_size_ratio": 1.15,    # block this much larger than body text: heading
    "stitch_paragraphs": True,     # rejoin paragraphs broken by a page break
}

HYPHEN_CHARS = ("-", "\u2010", "\u00ad")
MAX_CUT_DEPTH = 8
ROW_GAP_PT = 12.0

# A margin line shaped like a page number (arabic, roman or "Page N of M").
_PAGE_NUMBER_RE = re.compile(
    r"^(?:(?:page|p\.)\s*)?[-–—\s]*\d{1,4}\s*(?:(?:of|/)\s*\d{1,4})?[-–—\s]*$"
    r"|^[ivxlc]{1,6}$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Line:
    """One visual line of a text block."""

    text: str
    y0: float
    y1: float


@dataclass(frozen=True)
class TextBlock:
    """A PyMuPDF text block: geometry plus its (stripped) lines."""

    x0: float
    y0: float
    x1: float
    y1: float
    lines: tuple
    size: float = 0.0
    bold: bool = False

    @property
    def text(self) -> str:
        return _collapse(" ".join(line.text for line in self.lines))


@dataclass(frozen=True)
class PageBlocks:
    """All text blocks of one page, plus the page height they were measured in."""

    height: float
    blocks: tuple


# --------------------------------------------------------------------- config

def layout_config():
    """config.json["layout"], merged over DEFAULT_LAYOUT."""
    raw = parser.load_config().get("layout", {}) or {}
    cfg = dict(DEFAULT_LAYOUT)
    cfg.update({k: v for k, v in raw.items() if v is not None})
    return cfg


def _collapse(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


# ------------------------------------------------------------ PDF -> blocks

def page_blocks(page) -> list:
    """Text blocks of one PyMuPDF page, images excluded.

    Uses "dict" mode because it carries line geometry, which is what makes both
    column detection and paragraph reflow possible.
    """
    data = page.get_text("dict")
    blocks = []
    for raw in data.get("blocks", []):
        if raw.get("type", 0) != 0:  # 1 == image block
            continue
        lines = []
        sizes = []
        bold = False
        for raw_line in raw.get("lines", []):
            parts = []
            for span in raw_line.get("spans", []):
                text = span.get("text", "")
                if not text:
                    continue
                parts.append(text)
                sizes.append(float(span.get("size", 0) or 0))
                if int(span.get("flags", 0) or 0) & 16:  # PyMuPDF bold flag bit
                    bold = True
            text = _collapse("".join(parts))
            if not text:
                continue
            bbox = raw_line.get("bbox", (0.0, 0.0, 0.0, 0.0))
            lines.append(Line(text=text, y0=float(bbox[1]), y1=float(bbox[3])))
        if not lines:
            continue
        bbox = raw.get("bbox", (0.0, 0.0, 0.0, 0.0))
        blocks.append(TextBlock(
            x0=float(bbox[0]), y0=float(bbox[1]),
            x1=float(bbox[2]), y1=float(bbox[3]),
            lines=tuple(lines),
            size=max(sizes) if sizes else 0.0,
            bold=bold,
        ))
    return blocks


def document_pages(doc) -> list:
    """(height, blocks) per page, in page order."""
    return [
        PageBlocks(height=float(page.rect.height), blocks=tuple(page_blocks(page)))
        for page in doc
    ]


# -------------------------------------------------------------- page furniture

def _furniture_key(text: str) -> str:
    """Normalize a margin line so page numbers and spacing do not defeat matching."""
    return re.sub(r"\d+", "#", _collapse(text).casefold())


def _in_band(block, top_edge: float, bottom_edge: float, share: float = 0.5) -> bool:
    """True when at least `share` of the block's height lies inside a margin band.

    Straddling the boundary must still count: a footer whose block starts just
    above the band and runs to the page edge is a footer, and an all-or-nothing
    test would keep it.
    """
    height = max(block.y1 - block.y0, 1e-6)
    top_overlap = max(0.0, min(block.y1, top_edge) - block.y0)
    bottom_overlap = max(0.0, block.y1 - max(block.y0, bottom_edge))
    return (top_overlap / height) >= share or (bottom_overlap / height) >= share


def classify_furniture(pages, cfg=None) -> dict:
    """Map (page_index, block_index) -> reason for margin lines to drop.

    Two independent rules, both restricted to the top/bottom margin bands:

    * page-number shape: dropped even in a short document (a "12" in the
      bottom margin is a page number whatever the document length);
    * repeated margin line: the same normalized text on at least
      `furniture_page_ratio` of the pages ("Published By: ...", running heads).
      A line that also appears outside the margins on any page is *kept* — it is
      real content that merely wrapped into the band.
    """
    cfg = cfg or layout_config()
    if not pages:
        return {}

    max_chars = int(cfg["max_furniture_chars"])
    top_pct = float(cfg["top_band_pct"])
    bottom_pct = float(cfg["bottom_band_pct"])
    number_pct = float(cfg.get("page_number_band_pct", bottom_pct))

    margin_flags = []   # per page: list[bool], positional with page.blocks
    number_flags = []   # same, for the wider page-number band
    margin_pages = defaultdict(set)
    body_pages = defaultdict(set)

    for index, page in enumerate(pages):
        top_band = page.height * top_pct
        bottom_band = page.height * (1.0 - bottom_pct)
        number_top = page.height * number_pct
        number_bottom = page.height * (1.0 - number_pct)
        flags = []
        numbers = []
        for block in page.blocks:
            in_margin = _in_band(block, top_band, bottom_band)
            flags.append(in_margin)
            numbers.append(_in_band(block, number_top, number_bottom))
            text = block.text
            if not text:
                continue
            key = _furniture_key(text)
            if in_margin and len(text) <= max_chars:
                margin_pages[key].add(index)
            elif not in_margin:
                body_pages[key].add(index)
        margin_flags.append(flags)
        number_flags.append(numbers)

    page_count = len(pages)
    threshold = max(
        int(cfg["furniture_min_pages"]),
        math.ceil(page_count * float(cfg["furniture_page_ratio"])),
    )
    # A key counts as furniture when it repeats in the margins more often than it
    # appears in the body. A paper title on page 1 plus the same text as a
    # running head on every later page is furniture *in the margin* and content
    # where it is set as the title.
    repeated = {
        key for key, seen_on in margin_pages.items()
        if len(seen_on) >= threshold and len(seen_on) > len(body_pages[key])
    }

    dropped = {}
    for index, page in enumerate(pages):
        for position, block in enumerate(page.blocks):
            in_margin = margin_flags[index][position]
            in_number_band = number_flags[index][position]
            if not in_margin and not in_number_band:
                continue
            text = block.text
            if not text or len(text) > max_chars:
                continue
            # Page numbers get the specific reason first: their normalized key
            # repeats on every page, but "page number" is what they are.
            if in_number_band and _PAGE_NUMBER_RE.match(text.strip()):
                dropped[(index, position)] = "page number in margin band"
            elif in_margin and _furniture_key(text) in repeated:
                key = _furniture_key(text)
                dropped[(index, position)] = (
                    f"repeated margin line on {len(margin_pages[key])}/{page_count} pages"
                )
    return dropped


# --------------------------------------------------------------- reading order

def order_blocks(blocks, cfg=None) -> list:
    """Order blocks into reading order with a recursive XY-cut.

    A wide vertical gap (>= row_gap_pt, and crossed by no block) separates
    structural rows first: that is what lifts a running head or footer away
    from the body. Inside such a row a genuinely empty whitespace gutter splits
    columns, and columns are read left to right before the next row.

    The row gap is deliberately wider than normal paragraph spacing. With a
    small threshold, two columns whose paragraph gaps happen to line up would
    be read row-wise like a table; with a wide one, the gutter survives and the
    page reads column by column.
    """
    cfg = cfg or layout_config()
    return _cut(
        list(blocks),
        float(cfg["min_gutter_pt"]),
        float(cfg.get("row_gap_pt", ROW_GAP_PT)),
        0,
    )


def _cut(blocks, min_gutter, row_gap, depth):
    if len(blocks) <= 1 or depth > MAX_CUT_DEPTH:
        return sorted(blocks, key=lambda b: (round(b.y0, 1), b.x0))

    rows = _split_rows(blocks, row_gap)
    if len(rows) > 1:
        ordered = []
        for row in rows:
            ordered.extend(_cut(row, min_gutter, row_gap, depth + 1))
        return ordered

    columns = _split_columns(blocks, min_gutter)
    if len(columns) > 1:
        ordered = []
        for column in columns:
            ordered.extend(_cut(column, min_gutter, row_gap, depth + 1))
        return ordered

    return sorted(blocks, key=lambda b: (round(b.y0, 1), b.x0))


def _split_rows(blocks, row_gap):
    """Group blocks into rows separated by a whitespace gap no block crosses."""
    rows = []
    current_end = None
    for block in sorted(blocks, key=lambda b: (b.y0, b.x0)):
        if current_end is not None and block.y0 > current_end + row_gap:
            rows.append([block])
        elif rows:
            rows[-1].append(block)
        else:
            rows.append([block])
        current_end = block.y1 if current_end is None else max(current_end, block.y1)
    return rows


def _split_columns(blocks, min_gutter):
    """Split at the widest empty vertical gutter, if one exists."""
    intervals = sorted((b.x0, b.x1) for b in blocks)
    gaps = []
    current_end = intervals[0][1]
    for x0, x1 in intervals[1:]:
        if x0 > current_end + min_gutter:
            gaps.append((current_end, x0))
        current_end = max(current_end, x1)
    if not gaps:
        return [blocks]

    left_edge, right_edge = max(gaps, key=lambda gap: gap[1] - gap[0])
    middle = (left_edge + right_edge) / 2.0
    left = [b for b in blocks if b.x1 <= middle]
    right = [b for b in blocks if b.x0 >= middle]
    if not left or not right:
        return [blocks]
    return [left, right]


# -------------------------------------------------------------------- reflow

def reflow_block(block) -> list:
    """Paragraphs of one block: hard wraps joined, hyphenation repaired.

    A block is normally one paragraph, but some producers emit a whole column as
    a single block, so internal line gaps are used to recover paragraph breaks.
    """
    lines = [line for line in block.lines if line.text.strip()]
    if not lines:
        return []
    paragraphs = []
    for group in _split_paragraphs(lines):
        text = _join_lines([line.text for line in group])
        if text:
            paragraphs.append(text)
    return paragraphs


def _split_paragraphs(lines):
    if len(lines) < 3:
        return [lines]
    gaps = [lines[i + 1].y0 - lines[i].y0 for i in range(len(lines) - 1)]
    positive = [gap for gap in gaps if gap > 0]
    if not positive:
        return [lines]
    baseline = statistics.median(positive)
    if baseline <= 0:
        return [lines]
    groups = [[lines[0]]]
    for gap, line in zip(gaps, lines[1:]):
        if gap > baseline * 1.6:
            groups.append([line])
        else:
            groups[-1].append(line)
    return groups


def is_heading(block, body_size: float, cfg=None) -> bool:
    """True when a block is set larger (or bold and short) than body text."""
    cfg = cfg or layout_config()
    if body_size > 0 and block.size >= body_size * float(cfg["heading_size_ratio"]):
        return True
    return bool(block.bold) and len(block.text) <= 80


def stitch_paragraphs(paragraphs, headings=None) -> list:
    """Rejoin paragraphs that a page (or block) break cut in half.

    A body paragraph interrupted by a page break ends without terminal
    punctuation and resumes with a lowercase word. Headings (flagged by their
    font size), list items and figure labels are short or start with a capital,
    so they stay separate.
    """
    flags = headings or [False] * len(paragraphs)
    stitched = []
    previous_heading = False
    for paragraph, heading in zip(paragraphs, flags):
        text = paragraph.strip()
        if not text:
            continue
        if stitched and not previous_heading and _continues(stitched[-1], text):
            stitched[-1] = _join_lines([stitched[-1], text])
        else:
            stitched.append(text)
        previous_heading = heading
    return stitched


_TERMINAL = ".!?:;\"')]}"


def _continues(previous: str, following: str) -> bool:
    if len(previous) < 40 or not following:
        return False
    if previous[-1] in _TERMINAL:
        return False
    return following[:1].islower()


def _join_lines(texts):
    """Join visual lines into a paragraph, undoing end-of-line hyphenation."""
    joined = ""
    for raw in texts:
        text = raw.strip()
        if not text:
            continue
        if not joined:
            joined = text
            continue
        tail = joined[-1]
        if tail in HYPHEN_CHARS and text[:1].islower():
            # "informa-" + "tion" -> "information"
            joined = joined.rstrip("".join(HYPHEN_CHARS)) + text
        else:
            joined = f"{joined} {text}"
    return _collapse(joined)


# ---------------------------------------------------------------------- glue

def extract_text(doc, losses=None, cfg=None) -> str:
    """Reading-ordered, paragraph-shaped text for a whole document.

    Every dropped margin line is appended to `losses` when a list is given, so
    the ledger stays a complete account of what cleaning removed and where.
    """
    cfg = cfg or layout_config()
    pages = document_pages(doc)
    furniture = classify_furniture(pages, cfg)
    body_size = _median_size(pages)

    paragraphs = []
    headings = []
    for index, page in enumerate(pages):
        kept = []
        for position, block in enumerate(page.blocks):
            reason = furniture.get((index, position))
            if reason is None:
                kept.append(block)
            elif losses is not None:
                losses.append({
                    "stage": "layout extraction",
                    "reason": reason,
                    "removed": block.text[:160],
                    "chars": len(block.text),
                })
        for block in order_blocks(kept, cfg):
            heading = is_heading(block, body_size, cfg)
            for paragraph in reflow_block(block):
                paragraphs.append(paragraph)
                headings.append(heading)
    if cfg.get("stitch_paragraphs", True):
        paragraphs = stitch_paragraphs(paragraphs, headings)
    return "\n".join(paragraphs)


def _median_size(pages) -> float:
    sizes = [block.size for page in pages for block in page.blocks if block.size > 0]
    return statistics.median(sizes) if sizes else 0.0
