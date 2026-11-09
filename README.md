# Quantagen

[![CI](https://github.com/upadhyayaditya7/quantagen/actions/workflows/ci.yml/badge.svg)](https://github.com/upadhyayaditya7/quantagen/actions/workflows/ci.yml)

**A small PDF → LLM-ready text pipeline with a token-savings audit report, a full loss ledger, and a content-retention audit.**

Quantagen extracts text from research PDFs, rebuilds it in reading order, removes layout noise (running heads, footers, page numbers, boilerplate), truncates reference/appendix sections, and reports *both* halves of the claim: how many tokens it saved **and** how much of the document's numbers, entities and figure references survived. It ships as a CLI batch runner and a Streamlit web UI, and both run the **same** pipeline.

> **Status: early-stage / experimental.** This is a working proof-of-concept (~1,300 lines of application code), not a production tool. Its savings and retention numbers are honest and reproducible, but retention is a *proxy* metric — there is no answer-accuracy evaluation yet. See [Known limitations](#known-limitations).

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
   │  drops stray page numbers, symbol-dense lines; keeps non-Latin text
   ▼
truncate_at_stop_markers()      ← cuts at standalone REFERENCES / APPENDIX / INDEX headings
   │                               (rules/parser.py; prose like "index r." can never fire it)
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

Both `main.py` (CLI) and `app.py` (UI) call the same `layout.extract_text()` + `clean_pipeline()`, so their outputs are byte-identical for the same input.

## Features

- **Layout-aware extraction** — PyMuPDF's per-page text is not reading order, so the raw stream is rebuilt: a recursive XY-cut reads two-column pages column by column and lifts running heads/footers out of the body, blocks keep their bounding boxes for margin analysis, hard-wrapped lines are rejoined, end-of-line hyphenation is repaired (`distri-` + `bution` → `distribution`), and paragraphs interrupted by a page break are stitched back together. On the bundled 288-page book this took the output from 9,018 hard-wrapped lines to 1,962 paragraphs, cutting mid-sentence line breaks from 3,692 to 39.
- **Page-furniture removal with attribution** — repeating margin lines (journal names, `Published By:` footers, running titles) and page numbers are dropped from the top/bottom bands using document frequency, not hand-written regexes, and each drop is ledgered with the page count that justified it. A line that is the *document title* once but a *running head* on later pages is kept where it is content and dropped where it is furniture.
- **Retention audit** (`eval/retention.py`) — the pipeline is lossy, so it proves what it kept: numbers, capitalised entities and figure/table/citation references are extracted before and after cleaning, every loss is attributed to the stage that caused it, and hand-curated answer-bearing passages (18 across the two samples) are checked for survival. Deliberate scope decisions (front/back-matter trim, reference truncation) are reported with their measured cost instead of being silently excused. CI fails the build if retention drops below its floors.
- **Retrieval-fidelity benchmark** (`eval/retrieval.py`) — survival is not the same as findable, so the same 18 curated questions are BM25-ranked over raw, post-layout and cleaned text (hit@1/3/5, MRR). Cleaning measures retrieval-neutral on both samples — the book's MRR is unchanged, the paper's improves — while the index shrinks 12.5% and 45%. CI fails if cleaning ever makes a document harder to search.
- **Recursive batch CLI** — walks `inputs/` for PDFs, processes each, prints a per-document audit report.
- **Streamlit UI** (`app.py`) — multi-file upload, one tab per document with raw/cleaned token metrics, a 600px preview pane, per-file `.txt` download, and a batch summary table.
- **Token accounting with stage breakdown** — raw vs. cleaned token counts via `tiktoken`, the reduction percentage, and *which stage* removed how many tokens. Savings are attributable, not just asserted.
- **Loss ledger (`outputs/report.json`)** — every removed span is recorded with the stage that removed it, a human-readable reason, a ≤160-char preview, and its char/token counts. Cleaning is auditable span-by-span, not just summarized.
- **Unicode-aware filtering** — letters from any script (Japanese, Cyrillic, Arabic, …) count as content; only genuinely symbol-dense lines are dropped.
- **Heading-safe truncation** — stop markers must start their own line and be heading-shaped, so a sentence containing the word "index" cannot truncate a document mid-prose.
- **Config-driven rules** (`config.json`) — noise regexes, stop markers, layout thresholds and section markers live outside the code. `config.json` is located relative to the package, so you can run from any directory.

## Measured effect of layout-aware extraction

Same two PDFs, naive per-page extraction versus the layout-aware path (both then run the identical cleaning pipeline, same tokenizer):

| | MLBasicsBook.pdf (288 pp) | researchpaperOnSustainableDev.pdf (6 pp) |
|---|---|---|
| Cleaned tokens | 130,281 → **127,878** | 3,762 → **3,120** |
| Reduction vs. raw | 15.98% → **17.53%** | 55.95% → **63.47%** |
| End-of-line hyphen splits | 239 → **1** | 0 → **0** |
| Paragraphs split mid-sentence | 3,692 → **39** | 195 → **0** |
| Page furniture occurrences | 0 → **0** | 11 → **0** |
| Output units | 9,018 lines → **1,962 paragraphs** | 334 lines → **50 paragraphs** |

The token win is modest on the book (furniture was already largely caught by regexes there), but the *readability* win is large: the cleaned output is now paragraphs, not fractured column interleavings. On the journal paper the win is direct — 1,280 tokens of repeating journal footers and running titles are gone at the extraction level, before any regex runs.

## What cleaning costs: the retention audit

Token savings say nothing about what was thrown away, so `eval/retention.py` measures the other side. Definitions (all deterministic and offline — no labels, no model calls, no tokenizer):

- **base** — text after layout extraction, before any cleaning stage.
- **scope** — what the documented scope decisions removed (front/back-matter percent trim, reference truncation). Reported with counts and examples, never silently excused.
- **gold** — `base` minus `scope`: the content the pipeline promised to keep.
- **retention** — retained / gold. This is the number worth quoting next to the token reduction.

Numbers are compared as a multiset (a value appearing twice must survive twice), entities and figure/table/citation references as sets. An answer-bearing passage counts as retained only when its answer survives **and** at least 60% of the sentence's 5-word shingles survive, so a lucky matching number elsewhere cannot pass as a kept answer.

The figures below were measured with **PyMuPDF 1.27.2.3**, which `requirements.txt` pins. Extraction is the one dependency that moves the denominator: PyMuPDF 2.x splits figure text into more lines, which takes the book's number retention to 94.5% without a single line of pipeline code changing (entities, references and all 18 answer passages still survive). So the pin is load-bearing — upgrading the extractor means re-measuring these numbers and the CI floors, not just bumping a version. The audit prints the build it used and records it in `outputs/retention.json`.

| | MLBasicsBook.pdf | researchpaperOnSustainableDev.pdf |
|---|---|---|
| Numbers retained | 6,058 / 6,226 → **97.30%** | 64 / 64 → **100.00%** |
| Entities retained | 496 / 496 → **100.00%** | 191 / 191 → **100.00%** |
| Figure/table/citation refs | 113 / 113 → **100.00%** | 1 / 1 → **100.00%** |
| Curated answer passages | 10 / 10 → **100%** | 8 / 8 → **100%** |
| Removed by design (trim) | 1,298 numbers, 665 entities | 49 numbers, 51 entities |
| Removed by design (references) | — | 262 numbers, 437 entities |

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
| Chunks indexed | 1,085 | 1,085 | 949 |
| hit@1 / hit@3 / hit@5 | 90.0% / 90.0% / 100% | 80.0% / 100.0% / 100% | 90.0% / 90.0% / 100% |
| MRR | 0.925 | 0.883 | 0.925 |

| researchpaperOnSustainableDev.pdf | raw | post-layout | cleaned |
|---|---|---|---|
| Chunks indexed | 49 | 47 | 27 |
| hit@1 / hit@3 / hit@5 | 87.5% / 100% / 100% | 75.0% / 100% / 100% | 87.5% / 100% / 100% |
| MRR | 0.917 | 0.854 | 0.938 |

Cleaning is retrieval-neutral, not retrieval-damaging: the book keeps an identical MRR (0.925) and the paper improves slightly (0.917 → 0.938), while the index shrinks by 12.5% and 45% respectively. That is the claim worth making — every removed token was index space that bought nothing. One book question lands at rank 4 in both raw and cleaned text; the retention section above is where the costs of cleaning are disclosed. CI fails if cleaned hit@3 falls below 80% or cleaned MRR falls more than 0.01 below raw.

Honest limits: BM25 is a lexical baseline, not a neural retriever, so this measures findability by the question's own words; 18 questions is a small sample; the chunk size is one common RAG default (a different size moves every score); and the bundled book is effectively single-column, so these numbers do not exercise layout's column-order fix — the paper's do.

```bash
python eval/retrieval.py           # audit inputs/, write outputs/retrieval.json
python eval/retrieval.py --check   # exit non-zero if a retrieval floor is breached (CI runs this)
```

## Repository layout

```
quantagen/
├── main.py            # CLI entrypoint, clean_pipeline(), filter_noise(), token/audit helpers
├── app.py             # Streamlit UI (uses the same extraction + pipeline)
├── rules/layout.py    # reading order, page furniture, de-hyphenation, paragraph stitching
├── rules/parser.py    # config-driven truncation, noise stripping, section extraction
├── eval/retention.py  # retention audit: numbers/entities/references/answer spans + stage blame
├── eval/retrieval.py  # retrieval fidelity: BM25 hit@k / MRR, raw vs post-layout vs cleaned
├── eval/gold.json     # 18 curated answer-bearing passages (quoted from the raw extraction)
├── config.json        # structural rules, layout thresholds, noise patterns, stop markers
├── tests/             # 90 offline pytest tests (9 modules); real-token smoke test included
├── .github/workflows/ # CI: pytest (Python 3.11 + 3.13) + retention and retrieval floors
├── inputs/            # sample PDFs (a Springer textbook + a research paper)
├── outputs/           # cleaned .txt, report.json, retention.json, retrieval.json (gitignored)
├── requirements.txt   # dependencies
└── LICENSE            # Apache-2.0
```

## Quickstart

```bash
pip install -r requirements.txt
```

> `tiktoken` downloads its tokenizer on first use, so the first run needs network access. The retention audit needs neither network nor `tiktoken`.

Run the test suite (offline — token counting is mocked; one opt-in test exercises the real encoder when its cache is available):

```bash
pip install pytest
python -m pytest tests -q   # 80 tests
```

### CLI

```bash
python main.py
```

Recursively scans `inputs/` for `*.pdf`, writes cleaned files to `outputs/`, and prints an audit report per document. Real numbers from the bundled samples:

```
--- Audit Report: MLBasicsBook.pdf ---
Raw Tokens:      155055
Cleaned Tokens:  127878
Efficiency:      17.5273% (27177 tokens removed)
  - layout extraction            7357 tokens
  - structural trim             18526 tokens
  - line filter                  1294 tokens
Sections:        abstract (1421 chars), conclusion (3346 chars)
Loss ledger:     493 spans removed (full trail in report.json)

--- Audit Report: researchpaperOnSustainableDev.pdf ---
Raw Tokens:      8540
Cleaned Tokens:  3120
Efficiency:      63.4660% (5420 tokens removed)
  - layout extraction            1280 tokens
  - structural trim               820 tokens
  - stop-marker truncation       3320 tokens
Sections:        conclusion (960 chars)
Loss ledger:     24 spans removed (full trail in report.json)
```

Note how different document types behave honestly differently: the textbook keeps most of its body (its savings come from front matter and layout noise), while the research paper legitimately drops ~40% to its reference section. An earlier version of this tool reported ~53% for *both* documents — that number was wrong (see [Fixed issues](#fixed-issues)).

### Streamlit UI

```bash
streamlit run app.py
```

Upload one or more PDFs, inspect the cleaned text per tab, and download the results. The UI runs the identical pipeline as the CLI.

### Configuration

All tunables live in [`config.json`](config.json):

| Key | Purpose |
|---|---|
| `structural_rules` | Fraction of the top/bottom of the document to strip (0–1). Set to `0` to disable. Boundaries snap to line starts, so trimming never emits a partial word. |
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
4. **`structural_rules` is still a blunt instrument.** Trimming 5% off each end is right for documents with cover pages/colophons and wrong for documents without them: on the 6-page paper it also removes the title block and the start of the abstract (measured: 49 numbers and 51 entities). Margin-furniture detection now handles running heads and footers, but tables of contents and colophons are still the percent trim's job.
5. **Bare numerals are still dropped.** The line filter cannot tell a chart tick label from a stray page number, so figure axis numbers (`1`, `2`, `3` …) are lost: 168 of 6,226 numbers in the book, which is why its retention floor is 97%.
6. **Section extraction is heuristic.** In books with per-chapter "Summary" headings, the extracted "conclusion" is the first such section's text, not the book's conclusion. Fine for papers, imperfect for books.
7. **Detection is English-centric.** Non-Latin *text survives cleaning*, but the stop markers, section markers and noise patterns themselves target English/journal conventions.
8. **Loss ledger entries are previews, not full text.** Each entry carries exact char/token accounting, but records at most a 160-char preview of the removed text — enough to eyeball *what* and *why* per span.
9. **`process_document()` still catches broad exceptions** (it prints the full traceback). The pipeline itself (`clean_pipeline`) raises normally, and 90 offline tests + CI guard all documented behaviors.

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

## Roadmap

1. **Model-based answer accuracy** — survival and findability are now both measured (retention audit + BM25 retrieval fidelity). What remains is scoring generated answers against the raw-text answers, which needs an API key and a judge, so it cannot gate CI; it belongs in a separate, clearly labelled harness.
2. **Markdown re-emission** — paragraphs now survive extraction, so headings/lists/tables can be emitted as Markdown, improving downstream RAG retrieval.
3. **Content-aware front/back-matter detection** — TOC and colophon detection to retire the blunt `structural_rules` percent trim, whose measured cost is now visible (49 numbers and 51 entities on a 6-page paper).
4. **Packaging** — `pyproject.toml`, `pip install quantagen`, a proper `quantagen` CLI entry point, and fixture PDFs in the test suite.

## License

[Apache-2.0](LICENSE)
