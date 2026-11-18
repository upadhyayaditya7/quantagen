"""Score one corpus document against the pre-registered rubric.

`report.py` turns these records into RESULTS.md; this module only measures.

Two choices bound what the numbers mean, so they are stated here rather than
buried in the report:

* The reference text is the raw PyMuPDF page stream. Retention shares are
  therefore "of what the PDF's text layer contains", which includes running
  heads, page numbers and the reference list. A tool that removes them -- this
  one included -- scores lower on numbers and entities than a tool that keeps
  everything. That is a real cost, shown rather than hidden, and it is why
  answer spans rather than retention carry the quality claim.
* Spans are scored at their own shingle width (see
  `eval.retention.context_coverage`), so a three-word slide label can pass or
  fail on its merits instead of scoring zero by construction.

Nothing here decides what to publish. A category below its target is still
scored; `report.py` is where the pre-registered publication rule lives.
"""

from __future__ import annotations

import time

import fitz

from bench import fetch, goldlib
from bench.adapters import ADAPTERS, ExtractionUnavailable
from eval.retention import (
    entities_in,
    numbers_in,
    references_in,
    span_survives,
)
from eval.retrieval import evaluate
from quantagen.pipeline import get_token_count

# The order RESULTS.md shows. Every adapter is scored with the same interface;
# an unavailable one is reported as unavailable, never skipped silently.
ADAPTER_ORDER = ("quantagen", "raw", "pymupdf4llm", "markitdown", "docling")


class ResultsUnavailable(RuntimeError):
    """The corpus is not ready to be scored (documents missing or unpinned)."""


def reference_text(path: str) -> str:
    """Raw PyMuPDF page stream: the baseline every ratio is measured against."""
    doc = fitz.open(path)
    try:
        return "\n".join(page.get_text() for page in doc)
    finally:
        doc.close()


def _share(kept: int, total: int):
    """Percentage, or None when the document has no such content at all."""
    return round(100.0 * kept / total, 2) if total else None


def span_score(gold: dict, text: str) -> dict:
    """Retained gold spans plus the ids that were lost. Never guesses."""
    if not gold or not gold.get("readable"):
        return {"checked": 0, "retained": 0, "lost": [], "answers": 0, "verbatim": 0}

    numbers = numbers_in(text)
    lost, kept, answers, verbatim = [], 0, 0, 0
    for span in gold.get("spans") or []:
        is_answer = span.get("kind") == "answer"
        answers += bool(is_answer)
        verbatim += not is_answer
        survived = span_survives(span.get("quote", ""), text,
                                 span.get("answer", "") if is_answer else "",
                                 numbers)
        kept += bool(survived)
        if not survived:
            lost.append(span["id"])
    return {"checked": len(gold.get("spans") or []), "retained": kept,
            "lost": lost, "answers": answers, "verbatim": verbatim}


def retrieval_items(gold: dict) -> list:
    """The questions an extractor's text has to keep answerable, in gold order.

    Only `answer` spans carry a question. A `verbatim` span exists because the
    property under test is that the script survives at all; inventing an English
    question about Japanese prose would test the curator, not the extractor.
    """
    return [
        {"question": span.get("question", span["id"]),
         "answer": str(span.get("answer", "")),
         "context": span.get("quote", "")}
        for span in (gold or {}).get("spans") or []
        if span.get("kind") == "answer"
    ]


def score(reference: str, text: str, gold: dict, seconds: float = 0.0) -> dict:
    """Cost and quality of one extractor's output for one document."""
    base_numbers, output_numbers = numbers_in(reference), numbers_in(text)
    base_entities, base_refs = entities_in(reference), references_in(reference)
    kept_numbers = output_numbers & base_numbers

    tokens, reference_tokens = get_token_count(text), get_token_count(reference)
    record = {
        "status": "ok",
        "seconds": round(seconds, 2),
        "tokens": tokens,
        "chars": len(text),
        # A page whose text layer is empty still joins to "\n" between pages,
        # which tokenizes to a token. Without this flag an honest empty result
        # reads as "invented characters" in the scanned table.
        "blank": not text.strip(),
        "token_ratio": (round(tokens / reference_tokens, 4)
                        if reference_tokens else None),
        "number_retention": _share(sum(kept_numbers.values()),
                                   sum(base_numbers.values())),
        "entity_retention": _share(len(base_entities & entities_in(text)),
                                   len(base_entities)),
        "reference_retention": _share(len(base_refs & references_in(text)),
                                      len(base_refs)),
        "spans": span_score(gold, text),
        "retrieval": None,
    }

    items = retrieval_items(gold)
    if items and tokens:
        result = evaluate(items, text)
        record["retrieval"] = {
            "questions": result["questions"],
            "hit_at_3": result["hit_at_3_pct"],
            "mrr": result["mrr"],
        }
    return record


def _adapter_record(adapter, path: str, reference: str, gold: dict) -> dict:
    """Run one extractor. A missing tool or a crash is a result, not an error."""
    if not adapter.available():
        return {"status": "unavailable", "detail": adapter.install,
                "version": adapter.version()}

    started = time.perf_counter()
    try:
        text = adapter.extract(path)
    except ExtractionUnavailable as exc:
        return {"status": "unreadable", "detail": str(exc),
                "version": adapter.version(),
                "seconds": round(time.perf_counter() - started, 2)}
    except Exception as exc:  # a crashed entrant is a row, not a benchmark crash
        return {"status": "error", "detail": f"{type(exc).__name__}: {exc}",
                "version": adapter.version(),
                "seconds": round(time.perf_counter() - started, 2)}

    record = score(reference, text, gold, time.perf_counter() - started)
    record["version"] = adapter.version()
    if gold is not None and not gold.get("readable"):
        record["behaviour"] = _scan_behaviour(record)
    return record


def _scan_behaviour(record: dict) -> str:
    """What an extractor did with a document that has no text layer.

    "Returned no text" and "reported it could not read" are both honest;
    returning characters from an image-only page is the failure this category
    exists to catch, and it must be visible in the table.
    """
    if record.get("blank"):
        return "returned no text"
    return f"returned {record['chars']} characters"


def run_document(entry: dict, adapters=ADAPTER_ORDER, gold=None) -> dict:
    """Score one document across every requested extractor."""
    path = fetch.source_path(entry)
    reference = reference_text(path)
    gold = gold if gold is not None else goldlib.load(entry["id"])
    record = {
        "id": entry["id"],
        "category": entry["category"],
        "pages": entry.get("pages"),
        "reference": {"tokens": get_token_count(reference), "chars": len(reference)},
        "gold": {
            "readable": bool((gold or {}).get("readable", False)),
            "spans": goldlib.span_count(gold or {}),
        },
        "tools": {},
    }
    for name in adapters:
        record["tools"][name] = _adapter_record(ADAPTERS[name], path, reference, gold)
    return record


def run(corpus: dict = None, adapters=ADAPTER_ORDER, categories=None,
        progress=None) -> dict:
    """Score every verified document in the requested categories.

    An unverified document aborts the run instead of quietly shrinking a
    category: a table computed over different bytes than the manifest pins is
    the one thing the corpus exists to prevent.
    """
    corpus = corpus or fetch.load_corpus()
    categories = list(categories or [name for name in corpus["coverage"]
                                     if not name.startswith("_")])

    unverified = [entry["id"] for entry in corpus["documents"]
                  if fetch.status(entry)["state"] != "verified"]
    if unverified:
        raise ResultsUnavailable(
            "these documents are not cached and verified, so the table would "
            f"describe a different corpus: {', '.join(sorted(unverified))} "
            "(run `python bench/fetch.py`)")

    documents = [entry for entry in corpus["documents"]
                 if entry["category"] in categories]
    coverage = {}
    for category in categories:
        docs = [entry for entry in documents if entry["category"] == category]
        coverage[category] = {
            "documents": len(docs),
            "target": corpus["coverage"][category]["target"],
            "gold": sum(1 for entry in docs if goldlib.load(entry["id"])),
        }

    scored = []
    for entry in documents:
        if progress is not None:
            progress(entry)
        scored.append(run_document(entry, adapters))
    return {"categories": coverage, "documents": scored}
