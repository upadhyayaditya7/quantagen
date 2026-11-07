# Quantagen

[![CI](https://github.com/upadhyayaditya7/quantagen/actions/workflows/ci.yml/badge.svg)](https://github.com/upadhyayaditya7/quantagen/actions/workflows/ci.yml)

**A small PDF → LLM-ready text pipeline with a token-savings audit report and a full loss ledger.**

Quantagen extracts text from research PDFs, rebuilds it in reading order, removes layout noise (running heads, footers, page numbers, boilerplate), truncates reference/appendix sections, and reports how many tokens you saved — with a per-stage breakdown of where the savings come from. It ships as both a CLI batch runner and a Streamlit web UI, and both run the **same** pipeline.

> **Status: early-stage / experimental.** This is a working proof-of-concept (~870 lines of application code), not a production tool. The numbers it reports are honest and reproducible, but there is no automated fidelity evaluation yet — see [Known limitations](#known-limitations).

---

## What it does

```
PDF (PyMuPDF)
   │  text blocks with bbox, font size and line geometry
   ▼
layout.extract_text()           ← reading order via recursive XY-cut (columns, rows)
   │  margin furniture dropped · hard wraps joined · words de-hyphenated
   │  paragraphs stitched across page breaks (rules/layout.py)
   ▼
trim_structural_edges()         ← config-driven % trim of document head/tail (main.py)
   │
   ▼
filter_noise()                  ← Unicode-aware line-level filter (main.py)
   │  drops page numbers, symbol-dense lines; keeps non-Latin text
   ▼
truncate_at_stop_markers()      ← cuts at standalone REFERENCES / APPENDIX / INDEX headings
   │                               (rules/parser.py; prose like "index r." can never fire it)
   ▼
strip_recurring_noise()         ← config-driven regex cleanup from config.json
   │
   ├──► cleaned .txt written to outputs/
   ├──► token counts (tiktoken, gpt-4o encoding) + per-stage audit report
   ├──► loss ledger → outputs/report.json (every removed span: stage, reason, preview)
   └──► abstract/conclusion extraction (printed in the CLI audit)
```

Both `main.py` (CLI) and `app.py` (UI) call the same `layout.extract_text()` + `clean_pipeline()`, so their outputs are byte-identical for the same input.

## Features

- **Layout-aware extraction** — PyMuPDF's per-page text is not reading order, so the raw stream is rebuilt: a recursive XY-cut reads two-column pages column by column and lifts running heads/footers out of the body, blocks keep their bounding boxes for margin analysis, hard-wrapped lines are rejoined, end-of-line hyphenation is repaired (`distri-` + `bution` → `distribution`), and paragraphs interrupted by a page break are stitched back together. On the bundled 288-page book this took the output from 8,960 mid-sentence fragments to 1,930 real paragraphs.
- **Page-furniture removal with attribution** — repeating margin lines (journal names, `Published By:` footers, running titles) and page numbers are dropped from the top/bottom bands using document frequency, not hand-written regexes, and each drop is ledgered with the page count that justified it. A line that is the *document title* once but a *running head* on later pages is kept where it is content and dropped where it is furniture.
- **Recursive batch CLI** — walks `inputs/` for PDFs, processes each, prints a per-document audit report.
- **Streamlit UI** (`app.py`) — multi-file upload, one tab per document with raw/cleaned token metrics, a 600px preview pane, per-file `.txt` download, and a batch summary table.
- **Token accounting with stage breakdown** — raw vs. cleaned token counts via `tiktoken`, the reduction percentage, and *which stage* removed how many tokens (layout extraction / structural trim / line filter / truncation / patterns). Savings are attributable, not just asserted.
- **Loss ledger (`outputs/report.json`)** — every removed span is recorded with the stage that removed it, a human-readable reason, a ≤160-char preview of the removed text, and its char/token counts. Cleaning is auditable span-by-span, not just summarized; the ledger is attributed to the stage that *actually* removed each span.
- **Unicode-aware filtering** — letters from any script (Japanese, Cyrillic, Arabic, …) count as content; only genuinely symbol-dense lines are dropped.
- **Heading-safe truncation** — stop markers must start their own line and be heading-shaped, so a sentence containing the word "index" cannot truncate a document mid-prose.
- **Config-driven rules** (`config.json`) — noise regexes, stop markers, layout thresholds and abstract/conclusion section markers live outside the code. `config.json` is located relative to the package, so you can run from any directory.

## Measured effect of layout-aware extraction

Same two PDFs, naive per-page extraction versus the layout-aware path (both then run the identical cleaning pipeline, same tokenizer):

| | MLBasicsBook.pdf (288 pp) | researchpaperOnSustainableDev.pdf (6 pp) |
|---|---|---|
| Cleaned tokens | 130,127 → **129,333** | 3,758 → **3,183** |
| Reduction vs. raw | 16.08% → **16.59%** | 56.00% → **62.73%** |
| End-of-line hyphen splits | 239 → **1** | 0 → **0** |
| Paragraphs split mid-sentence | 3,692 → **38** | 195 → **0** |
| Page furniture occurrences | 0 → **0** | 11 → **0** |
| Output units | 8,960 lines → **1,930 paragraphs** | 332 lines → **50 paragraphs** |

The token win is modest on the book (furniture was already largely caught by regexes there), but the *readability* win is large: the cleaned output is now paragraphs, not fractured column interleavings. On the journal paper the win is direct — 1280 tokens of repeating journal footers and running titles are gone at the extraction level, before any regex runs.

## Repository layout

```
quantagen/
├── main.py            # CLI entrypoint, clean_pipeline(), filter_noise(), token/audit helpers
├── app.py             # Streamlit UI (uses the same extraction + pipeline)
├── rules/layout.py    # reading order, page furniture, de-hyphenation, paragraph stitching
├── rules/parser.py    # config-driven truncation, noise stripping, section extraction
├── config.json        # structural rules, layout thresholds, noise patterns, stop markers
├── tests/             # 66 offline pytest tests (7 modules); real-token smoke test included
├── .github/workflows/ # CI: pytest on push/PR (Python 3.11 + 3.13)
├── inputs/            # sample PDFs (a Springer textbook + a research paper)
├── outputs/           # cleaned .txt files + report.json ledger (gitignored; generated locally)
├── requirements.txt   # dependencies
└── LICENSE            # Apache-2.0
```

## Quickstart

```bash
pip install -r requirements.txt
```

> `tiktoken` downloads its tokenizer on first use, so the first run needs network access.

Run the test suite (offline — token counting is mocked; one opt-in test exercises the real encoder when its cache is available):

```bash
pip install pytest
python -m pytest tests -q   # 66 tests
```

### CLI

```bash
python main.py
```

Recursively scans `inputs/` for `*.pdf`, writes cleaned files to `outputs/`, and prints an audit report per document. Real numbers from the bundled samples:

```
--- Audit Report: MLBasicsBook.pdf ---
Raw Tokens:      155055
Cleaned Tokens:  129333
Efficiency:      16.5890% (25722 tokens removed)
  - layout extraction            7357 tokens
  - structural trim             16956 tokens
  - line filter                  1409 tokens
Sections:        abstract (1421 chars), conclusion (3346 chars)
Loss ledger:     527 spans removed (full trail in report.json)

--- Audit Report: researchpaperOnSustainableDev.pdf ---
Raw Tokens:      8540
Cleaned Tokens:  3183
Efficiency:      62.7283% (5357 tokens removed)
  - layout extraction            1280 tokens
  - structural trim               743 tokens
  - stop-marker truncation       3334 tokens
Sections:        conclusion (960 chars)
Loss ledger:     24 spans removed (full trail in report.json)
```

Note how different document types behave honestly differently: the textbook keeps most of its body (its savings come from front matter and layout noise), while the research paper legitimately drops ~50% to its references section. An earlier version of this tool reported ~53% for *both* documents — that number was wrong (see [Fixed issues](#fixed-issues)).

### Streamlit UI

```bash
streamlit run app.py
```

Upload one or more PDFs, inspect the cleaned text per tab, and download the results. The UI runs the identical pipeline as the CLI.

### Configuration

All tunables live in [`config.json`](config.json):

| Key | Purpose |
|---|---|
| `structural_rules` | Fraction of the top/bottom of the document to strip (0–1). Set to `0` to disable. |
| `layout.top_band_pct` / `layout.bottom_band_pct` | Share of page height treated as running-head / footer margin (default `0.08`). |
| `layout.page_number_band_pct` | Wider band in which a bare number is treated as a page number (default `0.15`). |
| `layout.furniture_min_pages` / `layout.furniture_page_ratio` | Evidence required before a margin line counts as furniture (default ≥ 3 pages and ≥ 50% of pages). |
| `layout.min_gutter_pt` | Narrowest empty whitespace band that may split a page into columns (default `8`). |
| `layout.row_gap_pt` | Vertical gap that separates structural rows; kept wider than paragraph spacing so columns are not read row-wise (default `12`). |
| `layout.stitch_paragraphs` | Rejoin paragraphs interrupted by a page break (default `true`). |
| `global_noise_patterns` | Regexes for recurring boilerplate (ISSN lines, © lines, `Page N of M`, stamp lines like `DRAFT` / `CONFIDENTIAL`) |
| `stop_markers` | Truncate at the first **standalone heading line** matching one of these (`REFERENCES`, `APPENDIX`, `INDEX`…). Must start the line; only a colon or section letter/number may follow. |
| `section_markers` | Headings used to extract `abstract` and `conclusion` |

---

## Known limitations

1. **No automated fidelity evaluation.** The pipeline removes what its heuristics consider noise, but nothing yet *measures* whether answers computed from the cleaned text match answers from the raw text. Until a QA-retention or precision/recall harness exists, treat the output as unvalidated.
2. **Reading order is a heuristic, not a layout model.** The XY-cut needs a genuinely empty gutter to split columns, and it separates structural rows before columns. Table- or figure-like regions whose whitespace aligns are therefore read row-wise, and a multi-column page with an irregular gutter can fall back to top-to-bottom order. It fixes the common cases (papers, books); it is not a document-AI model.
3. **Reflow is heuristic.** An end-of-line hyphen is only removed when the next line starts lowercase, so compound words split at a line break stay hyphenated. Paragraph stitching needs a long previous line and a lowercase continuation, so some page breaks remain visible (38 in the bundled book).
4. **`structural_rules` is still a blunt instrument.** Trimming 5% off each end is right for documents with cover pages/colophons and wrong for documents without them. It is off-by-default-able (set to `0`) but not content-aware; margin-furniture detection now handles running heads and footers, but tables of contents and colophons are still the percent trim's job.
5. **Section extraction is heuristic.** In books with per-chapter "Summary" headings, the extracted "conclusion" is the first such section's text, not the book's conclusion. Fine for papers, imperfect for books.
6. **Detection is English-centric.** Non-Latin *text survives cleaning*, but the stop markers, section markers and noise patterns themselves target English/journal conventions.
7. **Loss ledger entries are previews, not full text.** Each entry carries exact char/token accounting for the removed span, but records at most a 160-char preview of the text — enough to eyeball *what* and *why* per span.
8. **`process_document()` still catches broad exceptions** (it prints the full traceback). The pipeline itself (`clean_pipeline`) raises normally, and 66 offline tests + CI guard all documented behaviors.

## Fixed issues

For the record — these shipped broken and were fixed after being reproduced against the bundled samples (see git history for the individual commits):

- **Extraction ignored layout:** the raw stream was `"\n".join(page.get_text())`, so two-column pages interleaved their columns, running heads and footers landed *between* body blocks (the journal paper's footer appeared before its first paragraph), and every visual line stayed a hard-wrapped fragment with split words. Extraction now rebuilds reading order, drops margin furniture with attribution, de-hyphenates and emits paragraphs.
- **Noise patterns never ran:** `strip_recurring_noise()` looked up the wrong config key (`noise_patterns` vs `global_noise_patterns`), lacked `re.MULTILINE` for `^…$`-anchored patterns, and loaded the config without UTF-8 (breaking the `©` pattern on Windows). The `draft/confidential` pattern also matched the word "drafts" in prose; it is now anchored to stamp-shaped lines.
- **Stop-marker truncation cut documents mid-prose:** matching was case-insensitive with no line anchoring, so the word "index" inside a sentence truncated the sample book at 51%, silently discarding its final chapters. Truncation now requires a standalone heading-shaped line, and the audit shows truncation's contribution separately.
- **Non-Latin text was deleted wholesale:** the density filter counted only `[a-zA-Z\d\s]` as content, so Japanese/Cyrillic/Arabic lines scored as ~100% symbols. The filter is now Unicode-aware while still dropping symbol-dense layout lines.
- **CLI and UI ran different pipelines:** the UI skipped `filter_noise()`. Both now share one `clean_pipeline()` (and one extraction path).
- **`structural_rules` was dead config** and **extracted sections were computed but discarded**: both are wired up and surfaced in the CLI audit.

## Roadmap

1. **Fidelity evaluation harness** — hand-labeled noise lines for precision/recall of the filter, and/or QA retention (answer questions against raw vs. cleaned text) to report "token reduction @ ≥95% answer accuracy".
2. **Markdown re-emission** — paragraphs now survive extraction, so headings/lists/tables can be emitted as Markdown, improving downstream RAG retrieval.
3. **Content-aware front/back-matter detection** — TOC and colophon detection to retire the blunt `structural_rules` percent trim, and learned corpus-level furniture instead of per-document frequency.
4. **Packaging** — `pyproject.toml`, `pip install quantagen`, a proper `quantagen` CLI entry point, and fixture-PDF tests (the 66-test offline suite plus CI exist; fixture PDFs are the remaining part).

## License

[Apache-2.0](LICENSE)
