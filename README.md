# Quantagen

**A small PDF → LLM-ready text pipeline with a token-savings audit report.**

Quantagen extracts text from research PDFs, filters out layout noise (headers, footers, page numbers, boilerplate), truncates reference/appendix sections, and reports how many tokens you saved. It ships as both a CLI batch runner and a Streamlit web UI.

> **Status: early-stage / experimental.** This is a working proof-of-concept (~200 lines of application code across `main.py`, `app.py`, and `rules/parser.py`), not a production tool. Several advertised config options are currently inert, and the headline "~53% token savings" on the bundled sample book is mostly *content loss* caused by a truncation bug — see [Known limitations](#known-limitations-verified) before relying on the output. Every issue listed there was reproduced against the bundled sample PDFs.

---

## What it does

```
PDF (PyMuPDF)
   │  raw text, per page
   ▼
filter_noise()                  ← line-level heuristic filter (main.py)
   │  drops page numbers, symbol-dense lines, blank lines
   ▼
truncate_at_stop_markers()      ← cut everything from REFERENCES/APPENDIX/INDEX… onward (rules/parser.py)
   │
   ▼
strip_recurring_noise()         ← config-driven regex cleanup (currently a no-op, see below)
   │
   ├──► cleaned .txt written to outputs/
   ├──► token counts (tiktoken, gpt-4o encoding) + % reduction audit report
   └──► abstract/conclusion section extraction (computed, not yet surfaced anywhere)
```

## Features

- **Recursive batch CLI** — walks `inputs/` for PDFs, processes each, prints a per-document audit report.
- **Streamlit UI** (`app.py`) — multi-file upload, one tab per document with raw/cleaned token metrics, a 600px preview pane, per-file `.txt` download, and a batch summary table.
- **Token accounting** — raw vs. cleaned token counts via `tiktoken`, plus a reduction percentage, so savings are measured rather than guessed.
- **Config-driven rules** (`config.json`) — noise regexes, stop markers, and abstract/conclusion section markers live outside the code.
- **Section extraction** — pulls abstract and conclusion text out of the cleaned document.

## Repository layout

```
quantagen/
├── main.py            # CLI entrypoint, filter_noise(), token/audit helpers
├── app.py             # Streamlit UI
├── rules/parser.py    # config-driven truncation, noise stripping, section extraction
├── config.json        # noise patterns, stop markers, section markers
├── inputs/            # sample PDFs (a Springer textbook + a research paper)
├── outputs/           # cleaned .txt files (gitignored; sample outputs committed)
└── LICENSE            # Apache-2.0
```

## Quickstart

There is **no `requirements.txt` yet**. Install the dependencies manually:

```bash
pip install PyMuPDF tiktoken streamlit pandas
```

> `tiktoken` downloads its tokenizer on first use, so the first run needs network access.

### CLI

```bash
python main.py
```

Recursively scans `inputs/` for `*.pdf`, writes cleaned files to `outputs/`, and prints an audit report per document:

```
--- Audit Report: MLBasicsBook.pdf ---
Raw Tokens:      155055
Cleaned Tokens:  72978
Efficiency:      52.9341% (82077 tokens removed)
```

### Streamlit UI

```bash
streamlit run app.py
```

Upload one or more PDFs, inspect the cleaned text per tab, and download the results.

**Note:** the CLI and UI run slightly different pipelines (the UI skips `filter_noise()`), so identical inputs can produce different outputs. See [Known limitations](#cli--ui-pipeline-divergence).

### Configuration

All tunables live in [`config.json`](config.json):

| Key | Purpose |
|---|---|
| `global_noise_patterns` | Regexes for recurring boilerplate (ISSN lines, © lines, `Page N of M`, "CONFIDENTIAL"…) |
| `stop_markers` | Truncate the document at the first line starting with any of these (`REFERENCES`, `APPENDIX`, `INDEX`…) |
| `section_markers` | Headings used to extract `abstract` and `conclusion` |
| `structural_rules` | Percentage of the top/bottom of the document to strip |

`rules/parser.py` loads `config.json` **relative to the current working directory**, so always run from the repository root.

---

## Known limitations (verified)

These are real, reproducible issues in the current code — listed honestly so nobody is surprised by the output.

### 1. The noise patterns never run (config key mismatch)

[`rules/parser.py:10`](rules/parser.py) reads `config.get("noise_patterns", [])`, but [`config.json`](config.json) defines the key as **`global_noise_patterns`**. The lookup returns an empty list, so `strip_recurring_noise()` is a **no-op** — none of the seven curated boilerplate regexes (ISSN, copyright, "CONFIDENTIAL", `Page N of M`…) are ever applied. Verified: text containing `ISSN 1234-5678` passes through unchanged.

### 2. `structural_rules` is dead config

`strip_top_n_percent` / `strip_bottom_n_percent` are never referenced anywhere in the codebase. The natural fix — trimming the first/last 5% where journal headers and confetti live — is unimplemented.

### 3. Stop-marker truncation is case-insensitive and over-eager

`truncate_at_stop_markers()` matches any line starting with a marker, case-insensitively. On the bundled `MLBasicsBook.pdf` it matches the ordinary word **"index"** in the middle of an exercise sentence ("…for any given / index r."), truncating the book **51% of the way through**. The Neural Networks chapters, Bibliography, and everything after are silently discarded, and the output ends mid-sentence.

**This is why the sample book reports ~53% "efficiency": of ~280k characters removed, ~266k is truncation loss, not noise.** On the bundled research paper the same mechanism is *correct* (the references section genuinely starts at ~50%), which is why both samples coincidentally report ~52%.

### 4. Non-Latin text is deleted entirely

`filter_noise()` drops any line where >50% of characters are non-alphanumeric — but its character class is `[a-zA-Z\d\s]`, which doesn't cover non-ASCII letters. A line of Japanese, Chinese, Cyrillic, or Arabic text scores ~100% "symbols" and is **removed wholesale**, despite the recent "multi-language evaluation" commit. Verified with a Japanese sentence: it does not survive filtering. Symbol-dense lines (tables, heavy notation) can be lost the same way.

### 5. CLI / UI pipeline divergence

The CLI runs `sieve_text(filter_noise(raw))`; the UI runs `sieve_text(raw)` only — `filter_noise` is imported in [`app.py`](app.py) but never called. The UI therefore shows numbers and downloads that don't match what the CLI produces.

### 6. Committed sample outputs are stale

The current CLI writes `outputs/cleaned_inputs_<name>.txt` (the parent directory name becomes a "category" prefix), but the committed samples are named `cleaned_<name>.txt` — they were generated by an older revision and don't correspond to what today's code emits.

### 7. Features that exist but go nowhere

- `extract_sections()` runs on every document in the CLI, but the result is returned and discarded — never printed, never saved.
- Every line of the input's blank-line structure is removed, so paragraph breaks in the cleaned output are flattened.

### 8. Missing engineering hygiene

- No `requirements.txt` / lockfile (see [Quickstart](#quickstart)).
- No tests, no CI, no linting.
- Broad `except Exception` in `process_document()` swallows failures into a single print line.
- `outputs/` is gitignored yet sample outputs are committed, so the two drift.

---

## Roadmap

If you want to take this to a real tool, the highest-leverage fixes are:

1. **Fix the config key mismatch** (`noise_patterns` → `global_noise_patterns`) so the existing rules actually fire — one-line fix, immediate correctness win.
2. **Make stop-marker matching stricter** — require ALL-CAPS headings (e.g. `^\s*[A-Z][A-Z \-]{2,}$`) and/or only allow truncation after a position threshold, so prose like "index r." can't cut a document in half.
3. **Unify the CLI and UI pipelines** behind one `process_document(text)` function.
4. **Make the density filter Unicode-aware** (`\w` with `re.UNICODE`, or script-aware thresholds) so non-English documents survive.
5. **Wire up `structural_rules`** (top/bottom % trim) and **surface `extract_sections()`** in both outputs.
6. **Add `requirements.txt`, a test suite with fixture PDFs, and a token-savings regression check.**

## License

[Apache-2.0](LICENSE)
