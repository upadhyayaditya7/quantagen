# quantagen-bench

An open benchmark for **PDF → LLM-ready text**: one corpus, one pre-registered
rubric, every extractor scored the same way. The tool in this repository is one
of the entrants, not the referee.

The point is not to win. It is to make a claim that survives someone else
checking it — the thing that `eval/` cannot do on two documents with eighteen
questions its own author wrote.

## Status

Step 2 of 5. What exists today:

- `corpus.json` — the manifest: **30 hash-verified documents** across 7 categories,
  four of them at target (6 documents)
- `fetch.py` — download, verify, pin hashes; refuses to score page-swapped inputs
- `adapters/` — one interface, five extractors (quantagen, raw PyMuPDF, pymupdf4llm, markitdown, docling)

Not yet built: `gold/` (answer spans and must-survive facts per document),
`metrics.py`, `report.py` → `RESULTS.md`, and the scheduled CI job that
regenerates results. **No cross-tool numbers exist yet, and none will be quoted
until every category has six documents.**

## Coverage

| Category | Documents | Target | Stresses |
|---|---:|---:|---|
| single-column | 4 | 6 | plain reading order; the baseline every other category is measured against |
| two-column | 1 | 6 | XY-cut column separation, running heads between columns |
| textbook | 1 | 6 | TOC, index, glossary, figure captions |
| **tables** | **6** | 6 | six-column financial statements, note references shared by every row |
| **slides** | **6** | 6 | sparse pages, one line per bullet, footers that are not reading order |
| **non-latin** | **6** | 6 | no inter-word spaces, CJK punctuation, Latin runs inside CJK prose |
| **scanned** | **6** | 6 | no text layer at all: detect it, say so, do not invent text |

The first run downloads about 87 MB into `bench/cache/` (gitignored). Tables and
slides dominate; the scanned fixtures are small on purpose.

Two things about this corpus that are deliberately not hidden:

- **The scanned documents are fixtures, not photographs of paper.** Clean, noisy,
  skewed, and multi-page image-only PDFs, all verified to extract exactly zero
  characters by `tests/test_bench.py`. That is the property the category exists to
  test. A real scanner's paper texture and JPEG artefacts are a gap, and a fixture
  that quietly grew a text layer would fail the test rather than flatter the score.
- **The non-Latin set is 4 Chinese articles from one journal plus 2 Japanese
  articles.** It exercises the unicode filter and the English-only stop markers,
  but it is not script diversity: Korean, Arabic, Cyrillic and right-to-left
  layouts are missing, and that is the next gap to close.

Sourcing was empirical, not optimistic: eight candidate URLs were dropped after
failing to deliver a PDF (403 from two hosts, 404 from two guessed paths, 502 and
an HTML error page from J-STAGE, a TLS handshake failure, and one host that never
answered). Only bytes that arrived, hashed and pinned are in the manifest — a URL
in `corpus.json` is a claim someone already verified.

## Pre-registered rubric

Written on 2026-11-17, before any adapter score exists. Changing a metric after
seeing results invalidates the benchmark, so changes are recorded here with a
date and a reason instead of being applied quietly.

| Metric | Definition | Why it is here |
|---|---|---|
| Token ratio | `tokens(extractor) / tokens(raw PyMuPDF)`, `tiktoken` gpt-4o | The headline every tool advertises |
| Number retention | multiset of numeric values kept / gold, from `eval/retention.py` | Answers are made of numbers |
| Entity retention | capitalised names and acronyms, set semantics | Names carry the subject |
| Reference retention | `Figure 3`, `Table 2`, `[12]` markers, set semantics | Navigation survives or does not |
| Answer spans | 8–10 curated passages per document, answer present **and** ≥60% of 5-word shingles | Survival with context |
| hit@3 / MRR | BM25, 120-word chunks, 30 overlap, **averaged over three alignments** | Findability, not just presence |
| Wall clock | seconds per document, one warm run | Some tools pay 50× for their quality |
| Failure taxonomy | per category: what was lost (tables, equations, columns, scans) | The part a number hides |

Reporting rules, also pre-registered:

1. **Per-category tables**, never a single grand score. A tool that wins text and loses tables is not "better".
2. **Every loss is reported**, including the author's own. If Docling wins a category, the card says so.
3. **Versions pinned** in every result row; an unpinned score is not a score.
4. **Ten to twenty documents before publication claims.** Two samples is a demo; ranges are evidence.

## corpus.json

```jsonc
{
  "id": "arxiv-attention",              // safe identifier: [a-z0-9._-], becomes the cache filename
  "category": "single-column",          // must exist under "coverage"
  "source": { "kind": "url", "url": "https://arxiv.org/pdf/1706.03762" },
  //          or { "kind": "local", "path": "inputs/MLBasicsBook.pdf" }
  "sha256": null,                       // 64 lowercase hex, or null until --record pins it
  "pages": null,
  "redistributable": true,
  "license": "arXiv (see the paper's own license)",
  "why": "dense single-column body with many figures and equations"
}
```

`why` is not decoration: it names the layout feature the document is there to
stress, so a failure can be traced to a category instead of to a mystery PDF.

### Adding a document

1. Pick a category that is short of its target in `coverage`.
2. Add an entry with `sha256: null`.
3. `python bench/fetch.py --only <id>` then `python bench/fetch.py --only <id> --record`.
4. Paste the printed hash into `corpus.json`, set `pages`, bump `current`, and
   re-run `python bench/fetch.py --check`.

The tests check the parts that go stale on their own: `current` must equal the
number of entries in that category, a cached document's page count must match its
`pages`, every `scanned` fixture must extract zero characters, and every
`non-latin` document must contain CJK.

**Never commit a copyrighted PDF.** Springer textbooks and journal papers stay
behind a URL or in `inputs/` as an existing development sample; the manifest
records the hash and the licence so nobody redistributes one by accident.

## Commands

> On a stock Windows Python, `pip install certifi` is required for TLS:
> `ssl.get_default_verify_paths()` is empty there, so `fetch.py` loads certifi's
> bundle when it is importable. Verification is never disabled.

```bash
python bench/fetch.py --list                  # manifest, no files touched
python bench/fetch.py --check                 # validate and report state (offline)
python bench/fetch.py                         # download what is missing, verify hashes
python bench/fetch.py --only arxiv-bert       # one document
python bench/fetch.py --record                # hashes to paste after a first fetch
```

States: `verified` (present, hash matches), `unpinned` (present, no hash yet),
`mismatch` (wrong bytes — refuses to overwrite), `missing` (not fetched),
`absent` (a local path that no longer exists).

Downloads land in `bench/cache/`, which is gitignored. Fetching is a separate
step from scoring on purpose: a run must never silently compare against
different bytes than the run before it.

## Adapters

```python
from bench.adapters import ADAPTERS, MissingTool, availability

availability()                       # {'docling': False, 'quantagen': True, ...}
ADAPTERS["quantagen"].extract("inputs/MLBasicsBook.pdf")
ADAPTERS["docling"].version()        # so a score can be tied to a build
```

Three rules the interface enforces:

- **Lazy imports.** A machine without Docling still runs the benchmark; the
  adapter reports `available() == False` instead of breaking startup.
- **`MissingTool` carries the install command.** No silent zero scores.
- **`ExtractionUnavailable` is a result, not a crash.** An image-only PDF with no
  OCR is recorded as "could not read", which is different from "read it and
  produced nothing".
