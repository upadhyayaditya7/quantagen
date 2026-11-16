"""Retrieval fidelity: after cleaning, can a retriever still find the answer?

The retention audit (eval/retention.py) proves content is *present*. It cannot
show it is *findable*: a number can survive in a chunk that no retriever would
rank for the question, and no amount of counting surviving tokens notices that.

This module closes that gap, offline and deterministically. For every curated
question it builds three versions of the same document -- raw extraction,
post-layout, final cleaned -- chunks them identically, ranks the chunks with
BM25, and records where the answer-bearing chunk landed: hit@1, hit@3, hit@5
and MRR.

Chunking is a fixed word window with overlap, mimicking common RAG settings
rather than the pipeline's own paragraphs, so every variant faces the same
retrieval problem. Because a single window alignment is luck (the same answer
sentence ranks 3rd or 6th on bit-identical text depending only on where the
window starts), every variant is scored at several staggered alignments and
the results are averaged.

What this is not: BM25 is a lexical baseline, not a neural retriever. It says
whether the sentence is findable by its own words; it cannot say whether a
dense model would find it. Treat it as a floor, like retention.
"""

from __future__ import annotations

import argparse
import glob
import json
import math
import os
import re
import sys
from collections import Counter
from datetime import datetime, timezone

# Allow `python eval/retrieval.py` from the project root.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import fitz  # noqa: E402  (after the sys.path bootstrap)

from eval.retention import (  # noqa: E402
    CONTEXT_SHINGLE_FLOOR,
    context_coverage,
    extractor_version,
    stage_texts,
)
from quantagen.rules import layout  # noqa: E402

# Retrieval settings. Deliberately boring RAG defaults: ~120-word chunks with a
# 30-word overlap, top-5 cut-off. Changing them changes every number below, so
# they live here as named constants.
CHUNK_WORDS = 120
CHUNK_OVERLAP = 30
TOP_K = 5
# A single window alignment makes the score a coin flip: on the bundled book the
# same answer sentence ranks 3rd or 6th depending only on whether the 120-word
# window starts 0 or 20 words earlier, with the sentence itself bit-identical.
# Every variant is therefore scored at three staggered alignments and averaged,
# so a pipeline change is judged on the text rather than on chunk luck.
CHUNK_OFFSETS = (0, 40, 80)

# CI floors (see --check). Cleaning is only allowed to help retrieval: it
# removes furniture that wastes index space, so a regression here means the
# output became harder to search.
FLOOR_HIT_AT_3 = 80.0  # percent, cleaned text
# On a 10-question set one question slipping a single rank moves MRR by 0.05,
# so a 0.01 tolerance was stricter than the metric's own granularity -- it held
# only until a change shifted one window boundary. The gate therefore asserts
# findability (clean hit@3 may not fall below raw's) and treats 0.05 of MRR as
# noise: below that, no question lost its answer. Anything beyond it is real.
MRR_TOLERANCE = 0.05

_TOKEN_RE = re.compile(r"[a-z]+|\d+(?:\.\d+)?")


def tokenize(text: str) -> list[str]:
    """Lowercase words and numbers. No stemming: see the module docstring."""
    return _TOKEN_RE.findall(text.casefold())


def chunks(text: str, size: int = CHUNK_WORDS, overlap: int = CHUNK_OVERLAP,
           offset: int = 0) -> list[str]:
    """Fixed-size word windows, so every variant is chunked identically.

    `offset` drops leading words before windowing, which staggers the window
    alignment without changing the text; see CHUNK_OFFSETS.
    """
    if overlap >= size:
        raise ValueError("overlap must be smaller than the chunk size")
    words = text.split()[offset:] if offset else text.split()
    if not words:
        return []
    if len(words) <= size:
        return [" ".join(words)]

    stride = size - overlap
    windows = []
    for start in range(0, len(words), stride):
        window = words[start:start + size]
        if len(window) <= overlap:  # trailing sliver already inside the last chunk
            break
        windows.append(" ".join(window))
    return windows


class BM25:
    """Okapi BM25 over a fixed chunk list. Small enough to read in one sitting."""

    def __init__(self, documents: list[str], k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.frequencies = [Counter(tokenize(document)) for document in documents]
        self.lengths = [sum(freq.values()) for freq in self.frequencies]
        self.average_length = (sum(self.lengths) / len(self.lengths)) if self.lengths else 0.0

        document_frequency = Counter()
        for freq in self.frequencies:
            document_frequency.update(freq.keys())
        total = len(documents)
        self.idf = {
            term: math.log(1 + (total - count + 0.5) / (count + 0.5))
            for term, count in document_frequency.items()
        }

    def scores(self, query: str) -> list[float]:
        terms = set(tokenize(query))
        scores = [0.0] * len(self.frequencies)
        for term in terms:
            idf = self.idf.get(term)
            if idf is None:
                continue
            for index, freq in enumerate(self.frequencies):
                tf = freq.get(term)
                if not tf:
                    continue
                norm = 1 - self.b + self.b * self.lengths[index] / (self.average_length or 1)
                scores[index] += idf * tf * (self.k1 + 1) / (tf + self.k1 * norm)
        return scores

    def rank(self, query: str) -> list[int]:
        """Chunk indices, best first. Ties break by index for determinism."""
        scores = self.scores(query)
        return sorted(range(len(scores)), key=lambda index: (-scores[index], index))


def _rank_of_answer(index, chunk_list, question: str, context: str):
    """1-based rank of the first top-K chunk carrying the answer, else None."""
    for position, chunk_index in enumerate(index.rank(question)[:TOP_K], 1):
        if context_coverage(context, chunk_list[chunk_index]) >= CONTEXT_SHINGLE_FLOOR:
            return position
    return None


def evaluate(items: list, text: str, chunk_list: list[str] | None = None) -> dict:
    """Where does each question's answer-bearing chunk land in the ranking?

    Scored at every alignment in CHUNK_OFFSETS and averaged: a question counts
    as a hit@k when any alignment puts the answer in the top k, and its
    reciprocal rank is the mean across alignments (a miss contributing 0).
    """
    if chunk_list is not None:
        alignments = [chunk_list]
    else:
        alignments = [chunks(text, offset=offset) for offset in CHUNK_OFFSETS]
    indexes = [(BM25(chunk_list), chunk_list) for chunk_list in alignments if chunk_list]

    rows = []
    for item in items:
        context = item.get("context", "")
        question = item.get("question", "")
        ranks = [_rank_of_answer(index, chunk_list, question, context)
                 for index, chunk_list in indexes]
        ranks = [rank for rank in ranks if rank]
        rows.append({
            "question": question,
            "answer": item.get("answer", ""),
            "rank": min(ranks) if ranks else None,
            "reciprocal_rank": (sum(1.0 / rank for rank in ranks) / len(indexes)) if indexes else 0.0,
        })

    total = len(rows)
    at = lambda cutoff: round(100.0 * sum(1 for r in rows if r["rank"] and r["rank"] <= cutoff) / total, 3) if total else 100.0
    return {
        "questions": total,
        "chunks": len(alignments[0]) if alignments else 0,
        "words": len(text.split()),
        "alignments": len(alignments),
        "hit_at_1_pct": at(1),
        "hit_at_3_pct": at(3),
        "hit_at_5_pct": at(TOP_K),
        "mrr": round(sum(r["reciprocal_rank"] for r in rows) / total, 3) if total else 1.0,
        "missed": [r["question"] for r in rows if not r["rank"]],
    }


def retrieval_report(raw: str, base: str, clean: str, items: list) -> dict:
    """Retrieval quality of the same questions against three text versions."""
    variants = {
        "raw": evaluate(items, raw),
        "layout": evaluate(items, base),
        "clean": evaluate(items, clean),
    }
    raw_words = variants["raw"]["words"] or 1
    raw_chunks = variants["raw"]["chunks"] or 1
    return {
        "variants": variants,
        "clean_vs_raw": {
            "hit_at_3_delta": round(variants["clean"]["hit_at_3_pct"] - variants["raw"]["hit_at_3_pct"], 3),
            "mrr_delta": round(variants["clean"]["mrr"] - variants["raw"]["mrr"], 3),
            "words_kept_pct": round(100.0 * variants["clean"]["words"] / raw_words, 1),
            "chunks_kept_pct": round(100.0 * variants["clean"]["chunks"] / raw_chunks, 1),
        },
    }


def audit_pdf(path: str, gold: dict | None = None) -> dict:
    """Raw vs post-layout vs cleaned retrieval on one PDF."""
    doc = fitz.open(path)
    raw = "\n".join(page.get_text() for page in doc)
    base = layout.extract_text(doc)
    clean = stage_texts(base)[-1][1]
    name = os.path.basename(path)
    report = {"document": name, "pages": doc.page_count}
    report.update(retrieval_report(raw, base, clean, (gold or {}).get(name, [])))
    return report


# ------------------------------------------------------------------ CLI

def _load_gold(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle).get("documents", {})
    except FileNotFoundError:
        return {}


def _print_report(report: dict) -> None:
    print(f"\n--- Retrieval: {report['document']} ({report['pages']} pages) ---")
    print(f"  {'variant':<8}{'chunks':>7}{'hit@1':>9}{'hit@3':>9}{'hit@5':>9}{'MRR':>8}")
    for name, data in report["variants"].items():
        print(f"  {name:<8}{data['chunks']:>7}{data['hit_at_1_pct']:>8.1f}%"
              f"{data['hit_at_3_pct']:>8.1f}%{data['hit_at_5_pct']:>8.1f}%{data['mrr']:>8.3f}")
    delta = report["clean_vs_raw"]
    raw, clean = report["variants"]["raw"], report["variants"]["clean"]
    print(f"  clean vs raw: hit@3 {delta['hit_at_3_delta']:+.1f} pts, MRR {delta['mrr_delta']:+.3f}"
          f"  |  index {raw['chunks']} -> {clean['chunks']} chunks ({delta['chunks_kept_pct']}%)")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Measure retrieval quality of cleaned text.")
    parser.add_argument("--inputs", default=os.path.join(_ROOT, "inputs"),
                        help="Directory to scan for PDFs.")
    parser.add_argument("--gold", default=os.path.join(_ROOT, "eval", "gold.json"),
                        help="Curated answer-span questions.")
    parser.add_argument("--out", default=os.path.join("outputs", "retrieval.json"))
    parser.add_argument("--check", action="store_true",
                        help="Exit non-zero when retrieval floors are breached.")
    args = parser.parse_args(argv)

    gold = _load_gold(args.gold)
    paths = sorted(glob.glob(os.path.join(args.inputs, "**", "*.pdf"), recursive=True))
    if not paths:
        print(f"No PDFs found under {args.inputs}", file=sys.stderr)
        return 2

    print(f"Extractor: {extractor_version()}  chunks: {CHUNK_WORDS} words / {CHUNK_OVERLAP} overlap")

    reports = []
    for path in paths:
        report = audit_pdf(path, gold)
        reports.append(report)
        _print_report(report)

    if not args.check:
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as handle:
            json.dump({
                "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "extractor": extractor_version(),
                "chunk_words": CHUNK_WORDS,
                "chunk_overlap": CHUNK_OVERLAP,
                "chunk_offsets": list(CHUNK_OFFSETS),
                "documents": reports,
            }, handle, indent=2)
        print(f"\nRetrieval audit written to {args.out} ({len(reports)} document(s)).")

    failures = []
    for report in reports:
        variants = report["variants"]
        if not variants["clean"]["questions"]:
            continue
        clean, raw = variants["clean"], variants["raw"]
        if clean["hit_at_3_pct"] < FLOOR_HIT_AT_3:
            failures.append(f"{report['document']}: clean hit@3 {clean['hit_at_3_pct']}% < {FLOOR_HIT_AT_3}%")
        if clean["hit_at_3_pct"] < raw["hit_at_3_pct"]:
            failures.append(f"{report['document']}: clean hit@3 {clean['hit_at_3_pct']}% is below raw "
                            f"{raw['hit_at_3_pct']}%: cleaning lost an answer raw text found")
        if clean["mrr"] < raw["mrr"] - MRR_TOLERANCE:
            failures.append(f"{report['document']}: clean MRR {clean['mrr']} is below raw "
                            f"{raw['mrr']} (tolerance {MRR_TOLERANCE})")

    if failures:
        print("\nRetrieval floors failed:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
