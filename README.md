# Quantagen

**A small PDF → LLM-ready text pipeline with a token-savings audit report.**

Quantagen extracts text from research PDFs, filters out layout noise (headers, footers, page numbers, boilerplate), truncates reference/appendix sections, and reports how many tokens you saved — with a per-stage breakdown of where the savings come from. It ships as both a CLI batch runner and a Streamlit web UI, and both run the **same** pipeline.

> **Status: early-stage / experimental.** This is a working proof-of-concept (~250 lines of application code), not a production tool. The numbers it reports are honest and reproducible, but there is no automated fidelity evaluation yet — see [Known limitations](#known-limitations).

---

## What it does

```
PDF (PyMuPDF)
   │  raw text, per page
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
   └──► abstract/conclusion extraction (printed in the CLI audit)
```

Both `main.py` (CLI) and `app.py` (UI) call the same `clean_pipeline()`, so their outputs are byte-identical for the same input.

## Features

- **Recursive batch CLI** — walks `inputs/` for PDFs, processes each, prints a per-document audit report.
- **Streamlit UI** (`app.py`) — multi-file upload, one tab per document with raw/cleaned token metrics, a 600px preview pane, per-file `.txt` download, and a batch summary table.
- **Token accounting with stage breakdown** — raw vs. cleaned token counts via `tiktoken`, the reduction percentage, and *which stage* removed how many tokens (structural trim / line filter / truncation / patterns). Savings are attributable, not just asserted.
- **Unicode-aware filtering** — letters from any script (Japanese, Cyrillic, Arabic, …) count as content; only genuinely symbol-dense lines are dropped.
- **Heading-safe truncation** — stop markers must start their own line and be heading-shaped, so a sentence containing the word "index" cannot truncate a document mid-prose.
- **Config-driven rules** (`config.json`) — noise regexes, stop markers, and abstract/conclusion section markers live outside the code. `config.json` is located relative to the package, so you can run from any directory.

## Repository layout

```
quantagen/
├── main.py            # CLI entrypoint, clean_pipeline(), filter_noise(), token/audit helpers
├── app.py             # Streamlit UI (uses the same clean_pipeline())
├── rules/parser.py    # config-driven truncation, noise stripping, section extraction
├── config.json        # structural rules, noise patterns, stop markers, section markers
├── inputs/            # sample PDFs (a Springer textbook + a research paper)
├── outputs/           # cleaned .txt files (gitignored; generated locally)
├── requirements.txt   # dependencies
└── LICENSE            # Apache-2.0
```

## Quickstart

```bash
pip install -r requirements.txt
```

> `tiktoken` downloads its tokenizer on first use, so the first run needs network access.

### CLI

```bash
python main.py
```

Recursively scans `inputs/` for `*.pdf`, writes cleaned files to `outputs/`, and prints an audit report per document. Real numbers from the bundled samples:

```
--- Audit Report: MLBasicsBook.pdf ---
Raw Tokens:      155055
Cleaned Tokens:  130111
Efficiency:      16.0872% (24944 tokens removed)
  - structural trim             17588 tokens
  - line filter                  7356 tokens
Sections:        abstract (1421 chars), conclusion (3352 chars)

--- Audit Report: researchpaperOnSustainableDev.pdf ---
Raw Tokens:      8540
Cleaned Tokens:  3758
Efficiency:      55.9953% (4782 tokens removed)
  - structural trim               864 tokens
  - line filter                   268 tokens
  - stop-marker truncation       3650 tokens
Sections:        conclusion (168 chars)
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
| `global_noise_patterns` | Regexes for recurring boilerplate (ISSN lines, © lines, `Page N of M`, stamp lines like `DRAFT` / `CONFIDENTIAL`) |
| `stop_markers` | Truncate at the first **standalone heading line** matching one of these (`REFERENCES`, `APPENDIX`, `INDEX`…). Must start the line; only a colon or section letter/number may follow. |
| `section_markers` | Headings used to extract `abstract` and `conclusion` |

---

## Known limitations

1. **No automated fidelity evaluation.** The pipeline removes what its heuristics consider noise, but nothing yet *measures* whether answers computed from the cleaned text match answers from the raw text. Until a QA-retention or precision/recall harness exists, treat the output as unvalidated.
2. **`structural_rules` is a blunt instrument.** Trimming 5% off each end is right for documents with cover pages/colophons and wrong for documents without them. It is off-by-default-able (set to `0`) but not content-aware.
3. **Section extraction is heuristic.** In books with per-chapter "Summary" headings, the extracted "conclusion" is the first such section's text, not the book's conclusion. Fine for papers, imperfect for books.
4. **Detection is English-centric.** Non-Latin *text now survives cleaning* (fixed — see below), but the stop markers, section markers, and noise patterns themselves target English/journal conventions.
5. **Paragraph structure is flattened** — blank lines are removed during line filtering.
6. **No tests or CI yet.** The behaviors described here are verified manually against the bundled samples; nothing guards regressions automatically. `process_document()` still catches broad exceptions (it now prints the traceback).

## Fixed issues

For the record — these shipped broken and were fixed after being reproduced against the bundled samples (see git history for the individual commits):

- **Noise patterns never ran:** `strip_recurring_noise()` looked up the wrong config key (`noise_patterns` vs `global_noise_patterns`), lacked `re.MULTILINE` for `^…$`-anchored patterns, and loaded the config without UTF-8 (breaking the `©` pattern on Windows). The `draft/confidential` pattern also matched the word "drafts" in prose; it is now anchored to stamp-shaped lines.
- **Stop-marker truncation cut documents mid-prose:** matching was case-insensitive with no line anchoring, so the word "index" inside a sentence truncated the sample book at 51%, silently discarding its final chapters. Truncation now requires a standalone heading-shaped line, and the audit shows truncation's contribution separately.
- **Non-Latin text was deleted wholesale:** the density filter counted only `[a-zA-Z\d\s]` as content, so Japanese/Cyrillic/Arabic lines scored as ~100% symbols. The filter is now Unicode-aware while still dropping symbol-dense layout lines.
- **CLI and UI ran different pipelines:** the UI skipped `filter_noise()`. Both now share one `clean_pipeline()`.
- **`structural_rules` was dead config** and **extracted sections were computed but discarded**: both are wired up and surfaced in the CLI audit.

## Roadmap

1. **Fidelity evaluation harness** — hand-labeled noise lines for precision/recall of the filter, and/or QA retention (answer questions against raw vs. cleaned text) to report "token reduction @ ≥95% answer accuracy".
2. **Loss ledger** — log every removed span with a reason and token count (`report.json`) instead of destructive deletion, making the cleaning auditable and reversible.
3. **Corpus-adaptive boilerplate detection** — learn recurring header/footer lines from document frequency across a corpus instead of hand-written regexes.
4. **Markdown re-emission** — structure headings/lists/tables before counting, cutting tokens further and improving downstream RAG retrieval.
5. **Packaging** — `pyproject.toml`, `pip install quantagen`, a proper `quantagen` CLI entry point, and a pytest suite with fixture PDFs.

## License

[Apache-2.0](LICENSE)
