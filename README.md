# Quantagen

[![CI](https://github.com/upadhyayaditya7/quantagen/actions/workflows/ci.yml/badge.svg)](https://github.com/upadhyayaditya7/quantagen/actions/workflows/ci.yml)

**A small PDF → LLM-ready text pipeline with a token-savings audit report, a full loss ledger, and a content-retention audit.**

Quantagen extracts text from research PDFs, rebuilds it in reading order, removes layout noise (running heads, footers, page numbers, boilerplate), truncates reference/appendix sections, and reports *both* halves of the claim: how many tokens it saved **and** how much of the document's numbers, entities and figure references survived. It ships as a CLI batch runner and a Streamlit web UI, and both run the **same** pipeline.

> **Status: early-stage / experimental.** This is a working proof-of-concept (~1,000 lines of application code), not a production tool. Its savings and retention numbers are honest and reproducible, but retention is a *proxy* metric — there is no answer-accuracy evaluation yet. See [Known limitations](#known-limitations).

---

## What it does

```
PDF (PyMuPDF)
   │  text blocks with bbox, font size and line geometry
   ▼
layout.extract_text()           ← reading order via recursive XY-cut (columns, rows)
   │  margin furniture dropped · hard wraps joined · words de-hyphenated
   │  paragraphs stitched across page breaks (quantagen/rules/layout.py)
   ▼
trim_structural_edges()         ← front-matter detection: title block, table of contents
   │                               (pipeline.py; a % trim is still available, off by default)
   │
   ▼
filter_noise()                  ← Unicode-aware line-level filter (pipeline.py)
   │  drops stray page numbers, symbol-dense lines; keeps non-Latin text
   ▼
truncate_at_stop_markers()      ← cuts at standalone REFERENCES / APPENDIX / INDEX headings
   │                               (quantagen/rules/parser.py; prose like "index r." can never fire it)
   ▼
strip_recurring_noise()         ← config-driven regex cleanup from config.json
   │
   ├──► cleaned .txt written to outputs/
   ├──► token counts (tiktoken, gpt-4o encoding) + per-stage audit report
   ├──► loss ledger → outputs/report.json (every removed span: stage, reason, preview)
   ├──► retention audit → outputs/retention.json (numbers/entities/references kept,
   │                        plus the measured cost of each scope decision)
   └──► abstract/conclusion extraction (printed in the CLI audit)
```

The `quantagen` CLI (`quantagen/cli.py`) and `app.py` (UI) both call the same `layout.extract_text()` + `clean_pipeline()`, so their outputs are byte-identical for the same input.

## Features

- **Layout-aware extraction** — PyMuPDF's per-page text is not reading order, so the raw stream is rebuilt: a recursive XY-cut reads two-column pages column by column and lifts running heads/footers out of the body, blocks keep their bounding boxes for margin analysis, hard-wrapped lines are rejoined, end-of-line hyphenation is repaired (`distri-` + `bution` → `distribution`), and paragraphs interrupted by a page break are stitched back together. On the bundled 288-page book this took the output from 9,018 hard-wrapped lines to 1,962 paragraphs, cutting mid-sentence line breaks from 3,692 to 39.
- **Scanned-document detection, then OCR if it can** — before anything else, the document is checked for a text layer (`< 24` characters on a page counts as blank). An image-only PDF is never cleaned and written out as an empty file with a proud token-reduction number: with tesseract available it is OCR'd and the report says the raw baseline *is* the OCR text; without it the document is skipped loudly, recorded as `status: skipped`, and the CLI exits non-zero. Mixed documents keep their text pages.
- **Page-furniture removal with attribution** — repeating margin lines (journal names, `Published By:` footers, running titles) and page numbers are dropped from the top/bottom bands using document frequency, not hand-written regexes, and each drop is ledgered with the page count that justified it. A line that is the *document title* once but a *running head* on later pages is kept where it is content and dropped where it is furniture.
- **Retention audit** (`eval/retention.py`) — the pipeline is lossy, so it proves what it kept: numbers, capitalised entities and figure/table/citation references are extracted before and after cleaning, every loss is attributed to the stage that caused it, and hand-curated answer-bearing passages (18 across the two samples) are checked for survival. Deliberate scope decisions (front/back-matter trim, reference truncation) are reported with their measured cost instead of being silently excused. CI fails the build if retention drops below its floors.
- **Retrieval-fidelity benchmark** (`eval/retrieval.py`) — survival is not the same as findable, so the same 18 curated questions are BM25-ranked over raw, post-layout and cleaned text (hit@1/3/5, MRR). All 18 curated answers stay in the top 3 in every variant, the paper's MRR improves, and the index shrinks 10.2% and 38.8%; scores are averaged over three window alignments so chunk boundaries cannot decide the result. CI fails if cleaning loses an answer the raw text found.
- **Head-to-head comparison** (`eval/compare.py`) — another extractor's output is scored on the same questions and metrics. Measured against `pymupdf4llm` 2.1: 34% fewer tokens on the book (17.5% below raw, versus 24.9% *above* raw), one more curated answer retained, and better MRR on both samples — with the unfair parts of that comparison written down rather than omitted.
- **Installable console script** — `pip install -e .` puts a `quantagen` command on PATH: `quantagen` scans `./inputs`, `quantagen report.pdf papers/ --out build` takes explicit files and directories and writes cleaned text plus the audit trail wherever you point it. The same entry point runs without the script via `python -m quantagen`.
- **Streamlit UI** (`app.py`) — multi-file upload, one tab per document with raw/cleaned token metrics, a 600px preview pane, per-file `.txt` download, and a batch summary table.
- **Token accounting with stage breakdown** — raw vs. cleaned token counts via `tiktoken`, the reduction percentage, and *which stage* removed how many tokens. Savings are attributable, not just asserted.
- **Loss ledger (`outputs/report.json`)** — every removed span is recorded with the stage that removed it, a human-readable reason, a ≤160-char preview, and its char/token counts. Cleaning is auditable span-by-span, not just summarized.
- **Unicode-aware filtering** — letters from any script (Japanese, Cyrillic, Arabic, …) count as content; only genuinely symbol-dense lines are dropped.
- **Heading-safe truncation** — stop markers must start their own line and be heading-shaped, so a sentence containing the word "index" cannot truncate a document mid-prose.
- **Config-driven rules** (`config.json`) — noise regexes, stop markers, layout thresholds and section markers live outside the code. Resolution order is `$QUANTAGEN_CONFIG`, then `./config.json`, then the copy shipped inside the package, so an installed CLI is self-contained *and* a project can tune the rules without touching site-packages.

## Measured effect of layout-aware extraction

Same two PDFs, naive per-page extraction versus the layout-aware path (both then run the identical cleaning pipeline, same tokenizer):

| | MLBasicsBook.pdf (288 pp) | researchpaperOnSustainableDev.pdf (6 pp) |
|---|---|---|
| Cleaned tokens | 138,657 → **131,177** | 4,106 → **3,434** |
| Reduction vs. raw | 10.58% → **15.40%** | 51.92% → **59.79%** |
| End-of-line hyphen splits | 239 → **1** | 0 → **0** |
| Paragraphs split mid-sentence | 3,692 → **39** | 195 → **0** |
| Page furniture occurrences | 0 → **0** | 11 → **0** |
| Output units | 9,628 lines → **1,978 paragraphs** | 365 lines → **53 paragraphs** |

The token win is modest on the book (furniture was already largely caught by regexes there), but the *readability* win is large: the cleaned output is now paragraphs, not fractured column interleavings. On the journal paper the win is direct — 1,280 tokens of repeating journal footers and running titles are gone at the extraction level, before any regex runs.

## What cleaning costs: the retention audit

Token savings say nothing about what was thrown away, so `eval/retention.py` measures the other side. Definitions (all deterministic and offline — no labels, no model calls, no tokenizer):

- **base** — text after layout extraction, before any cleaning stage.
- **scope** — what the documented scope decisions removed (front matter, reference/index truncation). Reported with counts and examples, never silently excused.
- **gold** — `base` minus `scope`: the content the pipeline promised to keep.
- **retention** — retained / gold. This is the number worth quoting next to the token reduction.

Numbers are compared as a multiset (a value appearing twice must survive twice), entities and figure/table/citation references as sets. An answer-bearing passage counts as retained only when its answer survives **and** at least 60% of the sentence's 5-word shingles survive, so a lucky matching number elsewhere cannot pass as a kept answer.

The figures below were measured with **PyMuPDF 1.27.2.3**, which `requirements.txt` pins. Extraction is the one dependency that moves the denominator: PyMuPDF 2.x splits figure text into more lines, which takes the book's number retention to 94.5% without a single line of pipeline code changing (entities, references and all 18 answer passages still survive). So the pin is load-bearing — upgrading the extractor means re-measuring these numbers and the CI floors, not just bumping a version. The audit prints the build it used and records it in `outputs/retention.json`.

| | MLBasicsBook.pdf | researchpaperOnSustainableDev.pdf |
|---|---|---|
| Numbers retained | 6,326 / 6,494 → **97.41%** | 69 / 69 → **100.00%** |
| Entities retained | 498 / 498 → **100.00%** | 194 / 194 → **100.00%** |
| Figure/table/citation refs | 113 / 113 → **100.00%** | 1 / 1 → **100.00%** |
| Curated answer passages | 10 / 10 → **100%** | 8 / 8 → **100%** |
| Removed by design (front matter) | 278 numbers, 57 entities | — |
| Removed by design (references, index) | 752 numbers, 606 entities | 306 numbers, 485 entities |

The only unintended number loss is **168 bare numerals in the book**: chart tick labels (`1 1 1` then `2 3 4 5 6 7 Time` inside a figure) that the line filter cannot distinguish from page numbers. The audit reports them rather than excluding them, which is why the book's gate is 97% and not 100%. Everything else the audit found was fixed (see [Fixed issues](#fixed-issues)).

Run it:

```bash
python eval/retention.py           # audit inputs/, write outputs/retention.json
python eval/retention.py --check   # exit non-zero if a retention floor is breached (CI runs this)
```

## Can a retriever still find the answer?

Retention proves the content survived; it cannot prove a retriever would surface it, because a number can sit in a chunk that nothing ever ranks. `eval/retrieval.py` asks the downstream question directly: the same 18 curated questions, chunked identically (120 words, 30-word overlap), ranked by BM25 over three versions of each document.

| MLBasicsBook.pdf | raw | post-layout | cleaned |
|---|---|---|---|
| Chunks indexed | 1,085 | 1,085 | 974 |
| hit@1 / hit@3 / hit@5 | 90.0% / 100% / 100% | 90.0% / 100% / 100% | 90.0% / 100% / 100% |
| MRR | 0.911 | 0.894 | 0.884 |

| researchpaperOnSustainableDev.pdf | raw | post-layout | cleaned |
|---|---|---|---|
| Chunks indexed | 49 | 47 | 30 |
| hit@1 / hit@3 / hit@5 | 87.5% / 100% / 100% | 87.5% / 100% / 100% | 87.5% / 100% / 100% |
| MRR | 0.903 | 0.889 | 0.931 |

Cleaning is retrieval-neutral, not retrieval-damaging: **all 18 curated answers land in the top 3 in every variant**, the paper's MRR improves (0.903 → 0.931), and the index shrinks by 10.2% and 38.8%. The book's MRR sits 0.027 lower (0.911 → 0.884) because two of ten questions slip a single rank while one gains one — every answer is still in the top 3, and the retention section above is where the costs of cleaning are disclosed. CI fails if cleaned hit@3 falls below 80%, if it falls below the raw text's hit@3, or if MRR falls more than 0.05 below raw (one question moving one rank on a ten-question set is 0.05, so a tighter gate measures the chunker's luck rather than the text).

Honest limits: BM25 is a lexical baseline, not a neural retriever, so this measures findability by the question's own words; 18 questions is a small sample; the chunk size is one common RAG default (a different size moves every score); and the bundled book is effectively single-column, so these numbers do not exercise layout's column-order fix — the paper's do. Every score is the mean of three staggered window alignments (`CHUNK_OFFSETS`), because a single alignment is luck: on the book the same answer sentence ranks 3rd or 6th on bit-identical text depending only on where the 120-word window starts.

```bash
python eval/retrieval.py           # audit inputs/, write outputs/retrieval.json
python eval/retrieval.py --check   # exit non-zero if a retrieval floor is breached (CI runs this)
```

## How it compares

Numbers only persuade next to a baseline, so `eval/compare.py` puts another extractor's output through the same 18 questions, the same chunking and the same metrics. It is manual only: the other tool is never a project dependency and CI never runs it.

| MLBasicsBook.pdf (288 pp, 10 questions) | tokens | vs raw | answer spans | hit@3 | MRR | wall clock |
|---|---|---|---|---|---|---|
| Raw PyMuPDF stream | 155,055 | — | 10/10 | 90.0% | 0.925 | — |
| **Quantagen** | **127,878** | **−17.5%** | **10/10** | **90.0%** | **0.925** | **1.9 s** |
| pymupdf4llm 2.1 | 193,676 | +24.9% | 9/10 | 90.0% | 0.833 | 100 s |

> **Both tables were measured on the previous build** (5% head/tail trim). Quantagen's own rows are now 131,177 tokens (−15.4%, MRR 0.884) on the book and 3,434 (−59.8%, MRR 0.931) on the paper, and the retrieval columns are now averaged over three window alignments, so the other tools' rows are not directly comparable until `eval/compare.py` is re-run. The numbers below are kept as the record of what was measured, not as a current claim.

| researchpaperOnSustainableDev.pdf (6 pp, 8 questions) | tokens | vs raw | answer spans | hit@3 | MRR | wall clock |
|---|---|---|---|---|---|---|
| Raw PyMuPDF stream | 8,540 | — | 8/8 | 100% | 0.917 | — |
| **Quantagen** | **3,120** | **−63.5%** | **8/8** | **100%** | **0.938** | **0.1 s** |
| pymupdf4llm 2.1 | 7,960 | −6.8% | 8/8 | 100% | 0.854 | 10 s |

On that build, Quantagen's output was **34% smaller than pymupdf4llm's** on the book and **61% smaller** on the paper; it kept every curated answer, where pymupdf4llm lost one (Equation 4.20 — its surrounding sentence survives only 44% of its 5-word shingles), and it ranked better on both documents.

What that comparison does *not* say, in fairness to the alternative:

- pymupdf4llm emits **Markdown** with headings and tables. Some of its extra tokens are that structure, and structure this pipeline does not produce is a real feature — it is roadmap item 2, not a rounding error.
- Its 100 s on the book is **OCR on 7 pages** (RapidOCR). Quantagen only OCRs a document that has no text layer, and the machine that produced these numbers has no tesseract installed, so its run skipped OCR entirely — the runtime gap is not a pure extraction-speed comparison.
- Its output is larger than the raw stream (+24.9%); ours is smaller (−17.5%). Neither is automatically better — it depends on what is fed downstream.
- **markitdown is not measured.** Its install pulls several hundred MB (magika/onnxruntime) and this machine had 113 MB free. Not measured is reported as not measured.
- Both sides are scored on questions I authored, so treat the deltas as directional, not as a leaderboard.

```bash
python eval/compare.py --tool pymupdf4llm                  # needs pymupdf4llm installed locally
python eval/compare.py --from-file "pymupdf4llm=out/{stem}.md"   # or score text you already have
```

## Repository layout

```
quantagen/                  # the installable package
├── cli.py                   # console entry point: quantagen [PATH ...] [--out DIR]
├── pipeline.py              # clean_pipeline(), filter_noise(), token/audit helpers
├── rules/layout.py          # reading order, page furniture, de-hyphenation, paragraph stitching
├── rules/parser.py          # config resolution, truncation, noise stripping, section extraction
├── config.json              # packaged default rules (kept identical to the root copy by a test)
└── __main__.py              # `python -m quantagen`
app.py                       # Streamlit UI (same extraction + pipeline)
bench/                       # open benchmark: corpus manifest, fetch/verify, 5 extractor adapters
bench/corpus.json            # documents by URL + sha256 (never bundled), category targets
bench/README.md              # pre-registered rubric and the rules for adding a document
eval/retention.py            # retention audit: numbers/entities/references/answer spans + stage blame
eval/retrieval.py            # retrieval fidelity: BM25 hit@k / MRR, raw vs post-layout vs cleaned
eval/compare.py              # head-to-head against other extractors (manual, not in CI)
eval/gold.json               # 18 curated answer-bearing passages (quoted from the raw extraction)
config.json                  # the editable rules file for a checkout
pyproject.toml               # packaging, console script, pinned dependencies
requirements.txt             # pinned environment for CI and the numbers in this README
tests/                       # 118 offline pytest tests (13 modules); real-token smoke test included
.github/workflows/           # CI: pytest (Python 3.11 + 3.13) + retention and retrieval floors
inputs/                      # sample PDFs (a Springer textbook + a research paper)
outputs/                     # cleaned .txt, report.json, retention.json, retrieval.json (gitignored)
LICENSE                      # Apache-2.0
```

## Quickstart

```bash
pip install -e ".[app]"      # library + CLI + Streamlit UI; or: pip install -r requirements.txt
```

> `tiktoken` downloads its tokenizer on first use, so the first run needs network access. The retention audit needs neither network nor `tiktoken`.

Run the test suite (offline — token counting is mocked; one opt-in test exercises the real encoder when its cache is available):

```bash
pip install pytest
python -m pytest tests -q   # 118 tests
```

### CLI

```bash
quantagen                                     # every PDF under ./inputs
quantagen report.pdf papers/ --out build      # explicit inputs and output directory
python -m quantagen --version                 # same entry point without the script
```

Scans files and directories recursively for `*.pdf`, writes cleaned text and the audit trail where `--out` points (default `outputs/`), and prints a report per document. Real numbers from the bundled samples:

```
--- Audit Report: MLBasicsBook.pdf ---
Raw Tokens:      155055
Cleaned Tokens:  131177
Efficiency:      15.3997% (23878 tokens removed)
  - layout extraction            7357 tokens
  - structural trim              7447 tokens
  - line filter                  1294 tokens
  - stop-marker truncation       7780 tokens
Sections:        abstract (1421 chars), conclusion (3346 chars)
Loss ledger:     493 spans removed (full trail in report.json)

--- Audit Report: researchpaperOnSustainableDev.pdf ---
Raw Tokens:      8540
Cleaned Tokens:  3434
Efficiency:      59.7892% (5106 tokens removed)
  - layout extraction            1280 tokens
  - structural trim                19 tokens
  - stop-marker truncation       3807 tokens
Sections:        abstract (768 chars), conclusion (960 chars)
Loss ledger:     23 spans removed (full trail in report.json)
```

Note how different document types behave honestly differently: the textbook keeps its body and loses front matter, its index and its glossary headings to detected structure, while the research paper legitimately drops ~40% to its reference section and now keeps its abstract (the old percent trim used to cut the start of it). An earlier version of this tool reported ~53% for *both* documents — that number was wrong (see [Fixed issues](#fixed-issues)).

### Scanned PDFs

```
--- Skipped: scan.pdf ---
No text layer on 2 of 2 page(s): this PDF is scanned images.
OCR needs the tesseract binary on PATH (or TESSERACT_CMD).
```

OCR availability belongs to the machine, not the wheel, so the pipeline checks for it instead of assuming it: PyMuPDF drives the `tesseract` binary, `TESSERACT_CMD` overrides the lookup, and `OCR_DPI` (300) is the sampling density. When OCR runs, the audit prints it and `report.json` carries an `ocr` block naming the engine, page count and the fact that savings were measured against the OCR text rather than an empty text layer.

Verified here with a stubbed engine plus a real no-text-layer PDF: detection, the skip path and the report are covered by tests. The tesseract call itself has **not** been exercised on this machine (no tesseract installed), so treat real OCR output quality as untested until you run it against a scan you know.

### Streamlit UI

```bash
streamlit run app.py
```

Upload one or more PDFs, inspect the cleaned text per tab, and download the results. The UI runs the identical pipeline as the CLI.

### Configuration

All tunables live in [`config.json`](config.json). It resolves as `$QUANTAGEN_CONFIG` → `./config.json` → the copy packaged with the wheel, so a checkout's file wins when you run from the repo root and an installed CLI still works from any directory.

| Key | Purpose |
|---|---|
| `structural_rules.mode` | `detect` (default) removes front matter only where the document shows a table of contents or a title block; `percent` restores the old unconditional top/bottom trim. Detection is capped so it can never eat more than `max_front_matter_pct` of the text. |
| `structural_rules.max_front_matter_pct` | Largest share of the document detection may remove (default `0.15`), with a 200-character floor so a one-page paper's two-line title block still qualifies. |
| `structural_rules.strip_top_n_percent` / `strip_bottom_n_percent` | The percentage trim, now `0` by default and only used when `mode` is `percent`. Boundaries snap to line starts, so trimming never emits a partial word. |
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

1. **Retention is a proxy for quality, not a measurement of it.** Numbers, entities, figure references and 18 curated passages are checked for survival; retrieval fidelity (`eval/retrieval.py`) then checks the passages are still findable, but nothing yet measures whether *answers computed* from the cleaned text match answers from the raw text. Treat retention as a floor, not a certificate.
2. **Reading order is a heuristic, not a layout model.** The XY-cut needs a genuinely empty gutter to split columns, and it separates structural rows before columns. Table- or figure-like regions whose whitespace aligns are therefore read row-wise, and a multi-column page with an irregular gutter can fall back to top-to-bottom order. It fixes the common cases (papers, books); it is not a document-AI model.
3. **Reflow is heuristic.** An end-of-line hyphen is only removed when the next line starts lowercase, so compound words split at a line break stay hyphenated. Paragraph stitching needs a long previous line and a lowercase continuation, so some page breaks remain visible (39 in the bundled book).
4. **Front-matter detection needs an anchor.** A table of contents (three or more dot-leader lines) or a title block followed by a real paragraph is what licenses a cut; a cover page with no TOC and no prose behind it, an epigraph, or a document that begins with a figure keeps its front matter. That is deliberate — the alternative was a percentage, which was measured removing the start of the paper's abstract — but it means detection is a floor, not a guarantee.
5. **Bare numerals are still dropped.** The line filter cannot tell a chart tick label from a stray page number, so figure axis numbers (`1`, `2`, `3` …) are lost: 168 of 6,494 numbers in the book, which is why its retention floor is 97%.
6. **Section extraction is heuristic.** In books with per-chapter "Summary" headings, the extracted "conclusion" is the first such section's text, not the book's conclusion. Fine for papers, imperfect for books.
7. **Detection is English-centric.** Non-Latin *text survives cleaning*, but the stop markers, section markers and noise patterns themselves target English/journal conventions.
8. **Loss ledger entries are previews, not full text.** Each entry carries exact char/token accounting, but records at most a 160-char preview of the removed text — enough to eyeball *what* and *why* per span.
9. **`process_document()` still catches broad exceptions** (it prints the full traceback rather than raising to the caller). The pipeline itself (`clean_pipeline`) raises normally, the CLI exits non-zero when a document fails, and 118 offline tests + CI guard all documented behaviors.
10. **Image-only detection is per document, not per page.** A text PDF with a few scanned pages keeps its text pages and silently drops the scanned ones. Whole-document OCR is implemented; per-page fallback inside the layout pass is not, and it needs OCR text to be woven into reading order rather than appended.

## Fixed issues

For the record — these shipped broken and were fixed after being reproduced against the bundled samples (see git history for the individual commits):

- **The structural trim cut mid-word:** a raw character offset landed inside real content, so the kept text began with a fragment (`…different weights…` became `rent weights…`) and ended mid-number (`162–1`). Both boundaries now snap to line starts, and the audit reports the trim's content cost instead of hiding it.
- **The line filter deleted headings and years:** the old `^[a-zA-Z\s]+\s?\d+$` pattern matched any short text-plus-number line, so `Chapter 1`, `Glossary 253` and standalone years like `2020` were removed from the body. The rule is now limited to bare numerals (`12`, `253`, `Page 7`), because layout removes real page numbers by geometry.
- **Extraction ignored layout:** the raw stream was `"\n".join(page.get_text())`, so two-column pages interleaved their columns, running heads and footers landed *between* body blocks (the journal paper's footer appeared before its first paragraph), and every visual line stayed a hard-wrapped fragment with split words. Extraction now rebuilds reading order, drops margin furniture with attribution, de-hyphenates and emits paragraphs.
- **Noise patterns never ran:** `strip_recurring_noise()` looked up the wrong config key (`noise_patterns` vs `global_noise_patterns`), lacked `re.MULTILINE` for `^…$`-anchored patterns, and loaded the config without UTF-8 (breaking the `©` pattern on Windows). The `draft/confidential` pattern also matched the word "drafts" in prose; it is now anchored to stamp-shaped lines.
- **Stop-marker truncation cut documents mid-prose:** matching was case-insensitive with no line anchoring, so the word "index" inside a sentence truncated the sample book at 51%, silently discarding its final chapters. Truncation now requires a standalone heading-shaped line, and the audit shows truncation's contribution separately.
- **Non-Latin text was deleted wholesale:** the density filter counted only `[a-zA-Z\d\s]` as content, so Japanese/Cyrillic/Arabic lines scored as ~100% symbols. The filter is now Unicode-aware while still dropping symbol-dense layout lines.
- **CLI and UI ran different pipelines:** the UI skipped `filter_noise()`. Both now share one `clean_pipeline()` (and one extraction path).
- **`structural_rules` was dead config** and **extracted sections were computed but discarded**: both are wired up and surfaced in the CLI audit.
- **The percent trim destroyed content on short documents:** `strip_top_n_percent: 0.05` was 5% of the whole text, so on the 6-page paper it cut the title block and the start of the abstract (49 numbers, 51 entities), and on a three-row table it took the first and last rows. Front matter is now detected from a table of contents or a title block, capped, and ledgered with the evidence that licensed the cut; the percentage path remains as an explicit opt-in.

## Roadmap

1. **Model-based answer accuracy** — survival and findability are now both measured (retention audit + BM25 retrieval fidelity). What remains is scoring generated answers against the raw-text answers, which needs an API key and a judge, so it cannot gate CI; it belongs in a separate, clearly labelled harness.
2. **Markdown re-emission** — paragraphs now survive extraction, so headings/lists/tables can be emitted as Markdown, improving downstream RAG retrieval.
3. **Widen front-matter detection** — TOC and title-block detection replaced the percent trim; colophons, epigraphs and figure-first openings still survive. Back matter is handled only where a stop-marker heading exists.
4. **Mixed-document OCR** — image-only PDFs are handled; scanned pages *inside* a text PDF still vanish, because OCR text has to be placed into reading order rather than appended. Per-page fallback is the next robustness step, and it is only worth building against a real tesseract install to measure against.
5. **Independent evaluation corpus** — the 18 questions were written by the same person who wrote the pipeline. Ten to twenty PDFs from varied sources, reported as ranges instead of two curated samples, would make the retention floors evidence rather than self-assessment.

## License

[Apache-2.0](LICENSE)
