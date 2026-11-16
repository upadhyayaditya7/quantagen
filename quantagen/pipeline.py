"""The extraction and cleaning pipeline: one implementation for CLI and UI.

Stages, in order: raw PyMuPDF text -> layout-aware extraction (reading order,
page furniture, reflow) -> structural edge trim -> line-level noise filter ->
stop-marker truncation -> config-driven recurring-noise patterns. Every
removal is optional-ledgered through `losses`, which is what makes the audit
trail in outputs/report.json trustworthy rather than decorative.
"""

import os
import json
import shutil
from datetime import datetime, timezone

import fitz
import tiktoken
import re

from . import __version__
from .rules.parser import (
    strip_recurring_noise, truncate_at_stop_markers, extract_sections, load_config
)
from .rules import layout
def get_token_count(text: str, model: str = "gpt-4o") -> int:
    encoder = tiktoken.encoding_for_model(model)
    return len(encoder.encode(text))

# A line that carries nothing but a page number: "12", "253", "Page 7".
# Kept short (<= 3 digits) so four-digit years and numbered headings survive;
# layout removes real margin page numbers by geometry, this is the safety net.
_PAGE_FURNITURE_LINE = re.compile(r"^(?:\d{1,3}|Page\s+\d{1,4})$", re.IGNORECASE)

def filter_noise(text: str, losses=None) -> str:
    r"""Universal structural filter: Removes noise based on layout density.

    Unicode-aware: letters and digits from any script count as content, so
    non-Latin text (Japanese, Cyrillic, Arabic, ...) is preserved. Only lines
    that are genuinely symbol-dense are removed. Underscore runs count as
    symbols (they are layout, not content).

    If `losses` is a list, every removed line is appended to it as a ledger
    entry (stage, reason, removed preview, char count).
    """
    clean_lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped: continue
            
        # 1. Structural check: a line that is *only* a bare numeral (or
        #    "Page N") is page furniture. Deliberately narrow: layout already
        #    removes margin page numbers by geometry, and the older, broader
        #    pattern (`^[a-zA-Z\s]+\s?\d+$`) also deleted headings such as
        #    "Chapter 1" and standalone years like "2020" from the body.
        if len(stripped) <= 40 and _PAGE_FURNITURE_LINE.match(stripped):
            if losses is not None:
                losses.append({
                    "stage": "line filter",
                    "reason": "page-number-like line",
                    "removed": stripped[:160],
                    "chars": len(stripped),
                    "_full": stripped,
                })
            continue
            
        # 2. Density check: Removes lines that are mostly symbols (non-textual noise).
        #    [^\W_] = Unicode letters/digits; whitespace also counts as content.
        content_chars = len(re.findall(r"[^\W_]|\s", stripped))
        if len(stripped) > 0 and ((len(stripped) - content_chars) / len(stripped)) > 0.5:
            if losses is not None:
                losses.append({
                    "stage": "line filter",
                    "reason": "symbol-dense line (>50% non-letter characters)",
                    "removed": stripped[:160],
                    "chars": len(stripped),
                    "_full": stripped,
                })
            continue
            
        clean_lines.append(stripped)
    return "\n".join(clean_lines)

# Front matter is removed by evidence, not by percentage
# (structural_rules.mode == "detect"): a table of contents is recognisable by
# its dot leaders and a title block by short leading lines that stop at the
# first real paragraph. Nothing is cut when neither signal is present, because
# a missed cover page costs a few hundred tokens while a wrong cut costs
# content -- and the percentage cut was measured doing exactly that (49 numbers
# and 51 entities off the 6-page sample, including the start of its abstract).
# A contents line carries a dot leader: ". . . . . ." or ".........". Counting
# periods instead of a run was measured matching ordinary prose ("i.e.", "2014.",
# reference lists) and inventing a table of contents inside a paper's body.
TOC_LEADER = re.compile(r"(?:\.\s?){5,}")
TOC_MIN_LINES = 3          # consecutive entries needed to call it a contents
TITLE_BLOCK_MAX_LINES = 12 # never cut more leading lines than this
PROSE_LINE_CHARS = 120     # a line this long is body text, not a title block
# A one-page paper's title block is a couple of dozen characters but a fifth
# of its text, so the percentage cap gets an absolute floor for short inputs.
MIN_FRONT_MATTER_CHARS = 200


def _toc_run(lines, start: int) -> int:
    """Length of the run of table-of-contents lines beginning at `start`.

    Blank lines inside the run are tolerated (contents pages break columns),
    but the run only counts when at least `TOC_MIN_LINES` entries carry dot
    leaders -- one odd line with dots in prose must not cut a document.
    """
    index, entries = start, 0
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            index += 1
            continue
        if len(line.strip()) >= 12 and TOC_LEADER.search(line):
            entries += 1
            index += 1
            continue
        break
    return index if entries >= TOC_MIN_LINES else 0


def detect_front_matter_end(text: str) -> tuple:
    """Offset where body text starts, plus the evidence for cutting there.

    Returns ``(0, "")`` when nothing is confidently detected, so the caller
    keeps the whole document instead of falling back to a percentage guess.
    """
    lines = text.splitlines(keepends=True)
    if not lines:
        return 0, ""

    # 1. A table of contents. Everything before its last entry (cover, title,
    #    preface, lists of symbols) is front matter; the cut ends inside the
    #    document's opening quarter, which is where contents pages live.
    search_limit = min(len(lines), max(200, int(len(lines) * 0.15)))
    for start in range(search_limit):
        end_line = _toc_run(lines, start)
        if not end_line:
            continue
        while end_line < len(lines) and not lines[end_line].strip():
            end_line += 1
        removed = f"front matter before a {end_line - start}-line table of contents"
        return sum(len(line) for line in lines[:end_line]), removed

    # 2. A title block: short leading lines that stop at the first paragraph.
    #    Hitting the line cap without meeting prose means the document is a
    #    table or a list of short lines, which is content -- cut nothing.
    consumed = 0
    for index, line in enumerate(lines[:TITLE_BLOCK_MAX_LINES]):
        if len(line.strip()) >= PROSE_LINE_CHARS:
            break
        consumed = index + 1
    else:
        return 0, ""
    if not consumed:
        return 0, ""
    removed = f"title block ({consumed} lines before the first paragraph)"
    return sum(len(line) for line in lines[:consumed]), removed


def _record(losses, reason: str, span: str) -> None:
    """Append one structural-trim ledger entry for a removed span."""
    if losses is None or not span:
        return
    losses.append({
        "stage": "structural trim",
        "reason": reason,
        "removed": span[:160],
        "chars": len(span),
        "_full": span,
    })


def _trim_tail_percent(text: str, rules: dict, losses=None) -> str:
    """Honour an explicitly configured bottom percentage (default: none)."""
    bottom = float(rules.get("strip_bottom_n_percent", 0) or 0)
    if bottom <= 0 or not text:
        return text
    cut = int(len(text) * bottom)
    if cut <= 0:
        return text
    newline = text.rfind("\n", 0, len(text) - cut)
    end = newline if newline != -1 else len(text) - cut
    if end <= 0 or not text[:end].strip():
        return text
    _record(losses, "tail trim", text[end:])
    return text[:end]


def _trim_detected_front_matter(text: str, rules: dict, losses=None) -> str:
    """Cut the front matter a document can be shown to have, capped by config."""
    end, reason = detect_front_matter_end(text)
    if not end:
        return text
    cap = max(MIN_FRONT_MATTER_CHARS,
              int(len(text) * float(rules.get("max_front_matter_pct", 0.15) or 0)))
    if end > cap or not text[end:].strip():
        return text
    _record(losses, reason, text[:end])
    return text[end:]


def trim_structural_edges(text: str, losses=None) -> str:
    """Front/back-matter trim driven by config.json structural_rules.

    Two modes. ``detect`` (the default) removes front matter only where the
    document shows a table of contents or a title block, and leaves back
    matter to stop-marker truncation. ``percent`` removes the top/bottom n% of
    the text, which is what earlier versions did unconditionally. Removed spans
    go to `losses` when provided.
    """
    rules = load_config().get("structural_rules", {}) or {}
    mode = str(rules.get("mode") or "percent").strip().lower()
    if mode == "detect":
        return _trim_tail_percent(
            _trim_detected_front_matter(text, rules, losses), rules, losses)
    return _trim_edges_percent(text, rules, losses)


def _trim_edges_percent(text: str, rules: dict, losses=None) -> str:
    """The original blind trim: top n% and bottom n% of the text."""
    top = float(rules.get("strip_top_n_percent", 0) or 0)
    bottom = float(rules.get("strip_bottom_n_percent", 0) or 0)
    if top <= 0 and bottom <= 0:
        return text
    n = len(text)
    start = int(n * top) if top > 0 else 0
    end = n - int(n * bottom) if bottom > 0 else n
    # Snap both boundaries to line starts. A character offset lands mid-word
    # ("...different" became "rent weights ...") and mid-number, so the kept
    # text would begin and end with a fragment of real content.
    if start > 0:
        newline = text.find("\n", start)
        start = newline + 1 if newline != -1 else start
    if end < n:
        newline = text.rfind("\n", 0, end)
        end = newline if newline != -1 else end
    if start >= end or not text[start:end].strip():
        return text
    if losses is not None:
        for part, label in ((text[:start], "head trim"), (text[end:], "tail trim")):
            if part:
                _record(losses, label, part)
    return text[start:end]

def clean_pipeline(raw_text: str, stage_tokens: dict | None = None,
                   losses: list | None = None) -> str:
    """The single canonical cleaning pipeline, shared by CLI and UI.

    Order: structural edge trim -> line-level noise filter -> stop-marker
    truncation -> config-driven recurring-noise patterns.

    If `stage_tokens` is a dict, it is filled with the tokens removed at each
    stage, so the audit can show where the savings actually come from.
    If `losses` is a list, every removed span is appended to it as a loss-
    ledger entry: {stage, reason, removed (<=160-char preview), chars, tokens}. 
    """
    stage_fns = (
        ("structural trim", trim_structural_edges),
        ("line filter", filter_noise),
        ("stop-marker truncation", truncate_at_stop_markers),
        ("recurring-noise patterns", strip_recurring_noise),
    )
    text = raw_text
    prev_tokens = get_token_count(text)
    for name, fn in stage_fns:
        text = fn(text, losses) if losses is not None else fn(text)
        if stage_tokens is not None:
            tokens = get_token_count(text)
            stage_tokens[name] = max(0, prev_tokens - tokens)
            prev_tokens = tokens
    if losses is not None:
        for entry in losses:
            if "tokens" not in entry:
                # Count the span that was actually removed. `removed` is a
                # <=160-char preview, so tokenizing it reported 45 tokens for
                # the book's 8,671-token head trim and left report.json
                # contradicting its own stage_tokens summary (100 vs 18,526).
                span = entry.pop("_full", None)
                entry["tokens"] = get_token_count(
                    entry["removed"] if span is None else span)
    return text

def sieve_text(raw_text):
    """Backwards-compatible alias for clean_pipeline()."""
    return clean_pipeline(raw_text)


# A page with fewer extractable characters than this has no usable text layer:
# a scanned image page yields 0, a real text page yields words.
MIN_PAGE_CHARS = 24

# Below this total, a document is image-only even if a scan left a few stray
# characters behind (a stamped bates number, a speck of embedded text).
MIN_DOCUMENT_CHARS = 200

OCR_DPI = 300


def blank_pages(doc, min_chars: int = MIN_PAGE_CHARS) -> list:
    """Zero-based indexes of pages whose text layer is empty or unusable."""
    return [index for index, page in enumerate(doc)
            if len(page.get_text().strip()) < min_chars]


def needs_ocr(doc, min_chars: int = MIN_PAGE_CHARS,
              min_document_chars: int = MIN_DOCUMENT_CHARS) -> bool:
    """True when the PDF is image-only, so text extraction cannot work at all.

    Detecting this matters more than the OCR itself: without it the pipeline
    writes an empty .txt and reports a token reduction, which reads as success
    when the document was simply never readable.
    """
    if doc.page_count == 0:
        return False
    blank = blank_pages(doc, min_chars)
    if len(blank) == doc.page_count:
        return True
    total = sum(len(page.get_text().strip()) for page in doc)
    return total < min_document_chars and len(blank) > doc.page_count // 2


def ocr_engine() -> str | None:
    """'tesseract' when PyMuPDF can drive OCR on this machine, else None.

    PyMuPDF shells out to the tesseract binary, so OCR availability is a
    property of the machine rather than of the wheel. TESSERACT_CMD overrides
    the PATH lookup for installations elsewhere.
    """
    if not hasattr(fitz.Page, "get_textpage_ocr"):
        return None
    executable = os.environ.get("TESSERACT_CMD") or shutil.which("tesseract")
    return "tesseract" if executable else None


def ocr_text(doc, language: str = "eng", dpi: int = OCR_DPI) -> str:
    """OCR every page in page order. Call only when ocr_engine() is not None."""
    pages = []
    for page in doc:
        textpage = page.get_textpage_ocr(language=language, dpi=dpi, full=True)
        pages.append(page.get_text(textpage=textpage))
    return "\n".join(pages)


def process_document(file_path: str, output_folder: str = "outputs"):
    """Extract, clean and audit one PDF; returns (cleaned_text, sections).

    `output_folder` is where the cleaned .txt is written; the CLI exposes it as
    --out so the function no longer assumes the current directory is a checkout.
    """
    category = os.path.basename(os.path.dirname(file_path))
    file_name = os.path.basename(file_path)
    os.makedirs(output_folder, exist_ok=True)
    
    try:
        doc = fitz.open(file_path)

        # 0. Image-only documents: refuse to write an empty file and call it a
        #    win. OCR when the machine can, skip loudly when it cannot.
        ocr_info = None
        if needs_ocr(doc):
            engine = ocr_engine()
            blank = blank_pages(doc)
            if engine is None:
                print(f"\n--- Skipped: {file_name} ---")
                print(f"No text layer on {len(blank)} of {doc.page_count} page(s): "
                      "this PDF is scanned images.")
                print("OCR needs the tesseract binary on PATH (or TESSERACT_CMD). "
                      "Install it, or supply a PDF with a text layer. "
                      "No output written.")
                DOCUMENT_REPORTS.append({
                    "document": file_name,
                    "category": category,
                    "status": "skipped",
                    "reason": "scanned PDF with no text layer; OCR engine unavailable",
                    "pages": doc.page_count,
                    "blank_pages": len(blank),
                })
                return None, None
            ocr_info = {"engine": engine, "pages": doc.page_count, "dpi": OCR_DPI,
                        "language": "eng", "baseline": "ocr text"}

        # 1. Extract raw text (naive per-page stream: the honest raw baseline).
        #    For a scanned document the OCR output *is* the raw text: measuring
        #    savings against an empty text layer would be meaningless.
        raw_text = ocr_text(doc) if ocr_info else "\n".join(page.get_text() for page in doc)
        raw_token_count = get_token_count(raw_text)

        # 2. Layout extraction: reading order, margin furniture, paragraph reflow.
        #    Layout reconstruction needs text blocks, which OCR pages do not have.
        stage_tokens = {}
        losses = []
        extracted = raw_text if ocr_info else layout.extract_text(doc, losses)
        stage_tokens["layout extraction"] = max(0, raw_token_count - get_token_count(extracted))

        # 3. Process (single canonical pipeline, same as the UI)
        step2 = clean_pipeline(extracted, stage_tokens, losses)
        
        # 4. Extract Metadata
        sections = extract_sections(step2)
        clean_token_count = get_token_count(step2)
        # 5. Accurate Impact Analysis
        saved_tokens = max(0, raw_token_count - clean_token_count)
        reduction_pct = (saved_tokens / raw_token_count * 100) if raw_token_count > 0 else 0
        
        # 5b. Loss ledger entry for the machine-readable audit trail
        document_report = {
            "document": file_name,
            "category": category,
            "raw_tokens": raw_token_count,
            "cleaned_tokens": clean_token_count,
            "reduction_pct": round(reduction_pct, 4),
            "stage_tokens": stage_tokens,
            "sections": sections,
            "losses": losses,
        }
        if ocr_info:
            document_report["ocr"] = ocr_info
        DOCUMENT_REPORTS.append(document_report)
        
        # 6. Save final result
        clean_name = file_name.replace('.pdf', '.txt')
        output_filename = os.path.join(output_folder, f"cleaned_{category}_{clean_name}")
        
        with open(output_filename, "w", encoding="utf-8") as f:
            f.write(step2)
        
        # 7. Audit Report
        print(f"\n--- Audit Report: {file_name} ---")
        if ocr_info:
            print(f"OCR:             {ocr_info['engine']} on all {ocr_info['pages']} page(s); "
                  "raw baseline is the OCR text")
        print(f"Raw Tokens:      {raw_token_count}")
        print(f"Cleaned Tokens:  {clean_token_count}")
        print(f"Efficiency:      {reduction_pct:.4f}% ({saved_tokens} tokens removed)")
        for name, n in stage_tokens.items():
            if n:
                print(f"  - {name:<26}{n:>7} tokens")
        if sections:
            print("Sections:        " + ", ".join(f"{k} ({len(v)} chars)" for k, v in sections.items()))
        else:
            print("Sections:        none detected")
        if losses:
            print(f"Loss ledger:     {len(losses)} spans removed (full trail in report.json)")
        
        return step2, sections
        
    except Exception as e:
        import traceback
        print(f"Failed to process {file_path}: {e}")
        traceback.print_exc()
        return None, None

# Per-document audit records, accumulated across a batch run.
DOCUMENT_REPORTS = []


def write_report(path="outputs/report.json"):
    """Persist the accumulated loss ledger / audit trail as report.json."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({
            "engine_version": __version__,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "documents": DOCUMENT_REPORTS,
        }, f, indent=2, ensure_ascii=False)
    return path


def extract_text_from_stream(file_stream):
    """Layout text for the UI; OCRs image-only PDFs or says why it cannot."""
    doc = fitz.open(stream=file_stream.read(), filetype="pdf")
    if needs_ocr(doc):
        if ocr_engine() is None:
            raise RuntimeError(
                "This PDF has no text layer (scanned images) and OCR needs the "
                "tesseract binary on PATH (or TESSERACT_CMD)."
            )
        return ocr_text(doc)
    return layout.extract_text(doc)

def calc_efficiency(raw_text, clean_text):
    raw = get_token_count(raw_text)
    clean = get_token_count(clean_text)
    saved = raw - clean
    return (saved / raw * 100) if raw > 0 else 0
