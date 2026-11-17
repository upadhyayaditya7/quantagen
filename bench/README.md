# quantagen-bench

An open benchmark for **PDF → LLM-ready text**: one corpus, one pre-registered
rubric, every extractor scored the same way. The tool in this repository is one
of the entrants, not the referee.

The point is not to win. It is to make a claim that survives someone else
checking it — the thing that `eval/` cannot do on two documents with eighteen
questions its own author wrote.

## Status

Step 1 of 5. What exists today:

- `corpus.json` — the manifest: category targets and **six hash-verified documents**
  (two local samples, four arXiv papers downloaded and pinned)
- `fetch.py` — download, verify, pin hashes; refuses to score page-swapped inputs
- `adapters/` — one interface, five extractors (quantagen, raw PyMuPDF, pymupdf4llm, markitdown, docling)

Not yet built: `gold/` (answer spans and must-survive facts per document),
`metrics.py`, `report.py` → `RESULTS.md`, and the scheduled CI job that
regenerates results. **No cross-tool numbers exist yet, and none will be quoted
until every category has six documents.**

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
4. Paste the printed hash into `corpus.json` and re-run `python bench/fetch.py --check`.

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
