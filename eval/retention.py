"""Retention audit: what does the cleaning pipeline actually keep?

Quantagen is lossy by design, so "16.59% fewer tokens" is not on its own
evidence that the output is still usable. This module measures the other half
of the claim: of the content that carries information, how much survived?

Everything here is deterministic and offline. No hand labels, no model calls,
no tokenizer. The same measurement therefore runs on a 6-page paper and a
288-page book, on any machine, in seconds.

Definitions
-----------
base
    Text after layout extraction (reading order, furniture removal, reflow) and
    before any cleaning stage. Page numbers and running heads are already gone,
    so they never enter the denominator: this measures the *cleaning* stages.
scope
    What the documented scope stages (front/back-matter trim, reference
    truncation) removed. Reported with counts and examples, never subtracted
    from the base silently: dropping a paper's references is intentional, so
    it must not read as "damage", and it must not disappear either.
gold
    ``base`` minus ``scope``. This is the content the pipeline promised to
    keep.
retained
    gold items still present in the final cleaned text.
retention
    retained / gold -- the number worth quoting, next to token reduction.

Three content classes, deliberately different in kind:

* numbers    -- what answers are usually made of (measurements, years,
                percentages). A multiset: a value appearing twice must survive
                twice.
* entities   -- capitalised names and acronyms (institutions, methods,
                places). A set, so repeated mentions do not dominate.
* references -- figure/table/equation/citation markers, the pointers that make
                a document navigable. A set.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timezone

# Allow `python eval/retention.py` from the project root: the script directory
# is sys.path[0], which would hide the package modules it audits.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import fitz  # noqa: E402  (after the sys.path bootstrap)

from bench.rubric import RUBRIC  # noqa: E402


def extractor_version() -> str:
    """Which PDF engine produced these numbers.

    Extraction is the one dependency that changes the denominator: a different
    MuPDF build splits figure text into different lines, so retention moves
    without a single line of pipeline code changing. Recording the build makes
    that visible instead of mysterious.
    """
    try:
        import pymupdf
        return f"PyMuPDF {pymupdf.__version__}"
    except Exception:
        return getattr(fitz, "__doc__", "PyMuPDF unknown").strip()

from quantagen.pipeline import filter_noise, trim_structural_edges  # noqa: E402
from quantagen.rules import layout  # noqa: E402
from quantagen.rules.parser import strip_recurring_noise, truncate_at_stop_markers  # noqa: E402

# Same order as clean_pipeline(). A test asserts this chain reproduces the
# pipeline byte-for-byte, so the audit can never drift from the real code.
STAGE_CHAIN = (
    ("structural trim", trim_structural_edges),
    ("line filter", filter_noise),
    ("stop-marker truncation", truncate_at_stop_markers),
    ("recurring-noise patterns", strip_recurring_noise),
)

# Stages whose losses are a documented scope decision rather than damage:
# front/back-matter trim and reference-section truncation. Their cost is still
# counted and reported (in `scope_removed`), it just does not enter the
# denominator of the retention percentage.
SCOPE_STAGES = frozenset({"structural trim", "stop-marker truncation"})

# Minimum share of an answer-bearing sentence's shingles that must survive for
# the sentence to count as retained, and the shingle width. Both are read from
# bench/rubric.json so the audit and the scorer cannot disagree about it.
CONTEXT_SHINGLE_FLOOR = RUBRIC["retention"]["context_shingle_floor"]
SHINGLE_SIZE = RUBRIC["retention"]["shingle_size"]

# CI floors (see --check). Losing a measurement is the failure mode this
# project is most exposed to, so numbers are gated tightly. The book sits at
# 97.3% because the line filter still drops bare numerals that are chart tick
# labels ("1 1 1" / "2 3 4 5" inside a figure), which no regex can tell apart
# from a page number; that residual is documented in the README. Entities and
# references must be effectively perfect.
FLOOR_NUMBER_RETENTION = RUBRIC["floors"]["number_retention"]
FLOOR_ENTITY_RETENTION = RUBRIC["floors"]["entity_retention"]
FLOOR_REFERENCE_RETENTION = RUBRIC["floors"]["reference_retention"]

# 12, 3.5, 1,234.56, 2019 -- commas stripped so "1,234" and "1234" match.
_NUMBER_RE = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?(?![\w])")
_ENTITY_RE = re.compile(r"\b[A-Z][A-Za-z]{2,}\b|\b[A-Z]{2,}\b")
_REF_RE = re.compile(
    r"\b(?:Figure|Fig\.?|Table|Equation|Eq\.?)\s*\d+[a-z]?\b|\[\d{1,3}\]"
)

_STOPWORDS = frozenset(
    """
    The This That These Those There Their They Then Than Thus Therefore However
    Although Because While When Where Which What With Without Within From Into
    About Above After Against Among Around Before Below Between During Each
    Every For Has Have Had Her His Its Itself More Most Much Must Not Only
    Other Over Same Some Such Also Both But Can Could Did Does Doing Down
    Further Here How Just Nor Now Off Out Own She Should So Too Under Until
    Very Was Were Who Whom Why Will Would You Your And Are All Any As At Be
    Been Being By Do If In Is It No Of On Or Our Per See Use Used Using Via New
    One Two Three Four Five Six Seven Eight Nine Ten First Second Third Last
    Note Yet Since Both Each Thus Hence Within Across Towards Toward Just
    """.split()
)

# Document furniture handled by other stages; counting it as an "entity" would
# only add noise to the metric.
_NON_ENTITY = frozenset(
    """Page Chapter Section References Appendix Abstract Keywords Introduction
    Conclusion Summary Source Data Result Results Method Methods""".split()
)


# ------------------------------------------------------------------ extractors

def _normalize_number(token: str) -> str:
    return token.replace(",", "").rstrip(".")


def numbers_in(text: str) -> Counter:
    """Numeric values in `text` as a multiset, normalised for comparison."""
    counts = Counter()
    for raw in _NUMBER_RE.findall(text):
        value = _normalize_number(raw)
        if value:
            counts[value] += 1
    return counts


def entities_in(text: str) -> set:
    """Capitalised names and acronyms as a set (repeats are not information)."""
    return {
        token for token in _ENTITY_RE.findall(text)
        if token not in _STOPWORDS and token not in _NON_ENTITY
    }


def references_in(text: str) -> set:
    """Figure/table/equation and citation markers as a set."""
    return {re.sub(r"\s+", " ", ref.strip()) for ref in _REF_RE.findall(text)}


# ------------------------------------------------------------------ stage walk

def stage_texts(base: str) -> list:
    """Run the real cleaning stages in order: ``[(stage, text_after), ...]``.

    Index 0 is ``("input", base)``. No token counting, so this stays fast.
    """
    texts = [("input", base)]
    text = base
    for name, fn in STAGE_CHAIN:
        text = fn(text)
        texts.append((name, text))
    return texts


def _examples(items, limit: int = 8) -> list:
    if isinstance(items, Counter):
        return [value for value, _ in items.most_common(limit)]
    return sorted(items)[:limit]


def _retention_report(base_items, stage_items: list, measure) -> dict:
    """Retention for one content class.

    ``stage_items`` is ``[(stage, items_after_stage), ...]`` starting at
    ``"input"``. Losses are split into scope decisions (reported, not counted
    against retention) and unintended ones (line filter, regex patterns),
    which is what the CI floor guards.
    """
    prev = base_items
    scope = {}
    unintended = {}

    for name, items in stage_items:
        lost = prev - items
        if lost and measure(lost):
            bucket = scope if name in SCOPE_STAGES else unintended
            bucket.setdefault(name, lost)
        prev = items

    final = prev
    gold = base_items
    for lost in scope.values():
        gold = gold - lost

    gold_total = measure(gold)
    retained = measure(gold & final)
    return {
        "base": measure(base_items),
        "gold": gold_total,
        "retained": retained,
        "retention_pct": round(100.0 * retained / gold_total, 3) if gold_total else 100.0,
        "scope_removed": {
            name: {"count": measure(lost), "examples": _examples(lost)}
            for name, lost in scope.items()
        },
        "scope_removed_total": sum(measure(lost) for lost in scope.values()),
        "unintended_losses": {
            name: {"count": measure(lost), "examples": _examples(lost)}
            for name, lost in unintended.items()
        },
        "lost_examples": _examples(gold - final),
    }


# --------------------------------------------------------------- answer spans

# Scripts written without spaces are the reason this is not `[a-z0-9]`. An
# ASCII-only tokeniser does not merely lower a non-Latin score: it deletes every
# character of a Japanese or Chinese quote, so five of the benchmark's non-Latin
# spans scored exactly 0.0 for every extractor, including the raw text they were
# curated from. A metric that cannot see a language is not a conservative metric.
_CJK_RANGES = "\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uff66-\uff9f"
_CJK_CHAR = re.compile(f"[{_CJK_RANGES}]")
_TOKEN_RUN = re.compile(
    rf"\d+(?:\.\d+)?|[{_CJK_RANGES}]+|[^\W_{_CJK_RANGES}\d]+", re.UNICODE)


def script_tokens(text: str) -> list:
    """Words for spaced scripts, one token per character for CJK.

    Chinese and Japanese carry no word delimiters, so whitespace splitting
    yields one token for a whole sentence and shingle overlap becomes
    all-or-nothing. Per-character tokens give the metric a usable resolution
    there; a real word segmenter would be better and is not available offline.
    """
    tokens = []
    for run in _TOKEN_RUN.finditer(text.casefold()):
        chunk = run.group(0)
        if _CJK_CHAR.match(chunk[0]):
            tokens.extend(chunk)
            continue
        tokens.append(chunk)
    return tokens


def _width(text: str) -> int:
    """Shingle width a passage can support: five tokens, or its own length."""
    return min(SHINGLE_SIZE, len(script_tokens(text)))


def _shingles(text: str, size: int = SHINGLE_SIZE) -> set:
    """Overlapping token windows, shrunk to fit texts shorter than `size`.

    A quote under five tokens cannot produce a five-token window, and the
    previous version compared its single unpadded token against five-token
    windows in the document. A legitimate three-word span ("2-layer Neural
    Net") therefore scored 0.0 against byte-identical text, whatever any
    extractor did -- a failure of the metric, not of the text.
    """
    tokens = script_tokens(text)
    if not tokens:
        return set()
    width = max(1, min(size, len(tokens)))
    return {" ".join(tokens[i:i + width]) for i in range(len(tokens) - width + 1)}


def _overlap(context: str, haystack: set, width: int) -> float:
    wanted = _shingles(context, width)
    if not wanted:
        return 1.0
    return round(len(wanted & haystack) / len(wanted), 3)


def context_coverage(context: str, text: str) -> float:
    """Share of `context`'s shingles that still appear in `text`.

    Token-shingle overlap rather than substring matching, so reflowed line
    breaks and changed punctuation do not count as lost content while a
    genuinely deleted sentence scores near zero. Both sides are measured at the
    same window width, which is the quote's own length when it is short.
    """
    width = _width(context)
    if not width:
        return 1.0
    return _overlap(context, _shingles(text, width), width)


_NUMERIC_ANSWER_RE = re.compile(r"^[\d.,]+$")


def _answer_present(answer: str, final: str, final_numbers: Counter) -> bool:
    """Is `answer` present as a value, rather than as a coincidental substring?

    Numeric answers are checked against the same normalised multiset the audit
    uses, so "0.5" can no longer be found inside "10.53" and "3.1" inside
    "3.14". Text answers need word boundaries, so "TCP" does not match inside
    "TCPIP".
    """
    value = answer.strip()
    if not value:
        return False
    if _NUMERIC_ANSWER_RE.match(value):
        return bool(final_numbers.get(_normalize_number(value)))
    if _CJK_CHAR.search(value):
        # Word boundaries are useless inside CJK prose: the neighbouring
        # characters are word characters too, so a boundary-anchored search
        # can never match a substring of a CJK sentence.
        return value.casefold() in final.casefold()
    return re.search(r"(?<!\w)" + re.escape(value) + r"(?!\w)", final) is not None


def span_survives(context: str, text: str, answer: str = "",
                  final_numbers: Counter | None = None) -> bool:
    """One curated passage: enough context kept, and its answer still present.

    The same rule `answer_span_recall` applies, exposed per span so a caller
    that knows the span ids can report *which* passage was lost. A `verbatim`
    span passes no answer and is judged on context alone.
    """
    if context_coverage(context, text) < CONTEXT_SHINGLE_FLOOR:
        return False
    if not str(answer or "").strip():
        return True
    return _answer_present(str(answer), text,
                           final_numbers if final_numbers is not None
                           else numbers_in(text))


def answer_span_recall(base: str, final: str, items: list) -> dict:
    """How many hand-curated question-bearing passages survived cleaning.

    Each item is ``{"question", "answer", "context"}``. It counts as retained
    when the answer string is still present *and* at least
    ``CONTEXT_SHINGLE_FLOOR`` of the sentence's 5-word shingles survive, so a
    lucky bare number in an unrelated line cannot pass as a retained answer.
    """
    final_numbers = numbers_in(final)
    # One shingle set per window width actually needed. Rebuilding the document
    # for every item is what made short quotes fail before; keeping one set per
    # width keeps the audit linear in the document and correct for both.
    haystacks = {}
    for item in items:
        width = _width(item.get("context", ""))
        if width:
            haystacks.setdefault(width, _shingles(final, width))
    results = []

    for item in items:
        context = item.get("context", "")
        answer = str(item.get("answer", ""))
        width = _width(context)
        coverage = _overlap(context, haystacks[width], width) if width else 1.0
        present = _answer_present(answer, final, final_numbers)
        results.append({
            "question": item.get("question", ""),
            "answer": answer,
            "retained": bool(present and coverage >= CONTEXT_SHINGLE_FLOOR),
            "context_coverage": round(coverage, 3),
        })

    kept = sum(1 for item in results if item["retained"])
    return {
        "checked": len(results),
        "retained": kept,
        "retention_pct": round(100.0 * kept / len(results), 3) if results else 100.0,
        "lost": [item for item in results if not item["retained"]],
    }


# ------------------------------------------------------------------ per document

def audit_text(base: str, raw: str | None = None, gold_items: list | None = None) -> dict:
    """Audit one document's cleaning stages. `base` is post-layout extraction."""
    stages = stage_texts(base)

    def series(extractor):
        return [(name, extractor(text)) for name, text in stages]

    report = {
        "characters": {"raw": len(raw) if raw else None, "base": len(base),
                       "clean": len(stages[-1][1])},
        "numbers": _retention_report(
            numbers_in(base), series(numbers_in), lambda items: sum(items.values())),
        "entities": _retention_report(
            entities_in(base), series(entities_in), len),
        "references": _retention_report(
            references_in(base), series(references_in), len),
    }

    if raw is not None:
        # Layout extraction's own effect, reported separately: page numbers and
        # running heads are supposed to disappear here.
        raw_numbers = numbers_in(raw)
        layout_lost = raw_numbers - numbers_in(base)
        report["layout_extraction"] = {
            "numbers_removed": sum(layout_lost.values()),
            "examples": _examples(layout_lost),
        }

    if gold_items:
        report["answer_spans"] = answer_span_recall(base, stages[-1][1], gold_items)

    return report


def audit_pdf(path: str, gold: dict | None = None) -> dict:
    """Audit a PDF end to end: layout extraction then the cleaning stages."""
    doc = fitz.open(path)
    raw = "\n".join(page.get_text() for page in doc)
    base = layout.extract_text(doc)
    report = audit_text(base, raw=raw, gold_items=(gold or {}).get(os.path.basename(path)))
    report["document"] = os.path.basename(path)
    report["pages"] = doc.page_count
    return report


# ------------------------------------------------------------------ CLI

def _load_gold(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle).get("documents", {})
    except FileNotFoundError:
        return {}


def _print_report(report: dict) -> None:
    name = report["document"]
    numbers = report["numbers"]
    entities = report["entities"]
    refs = report["references"]
    print(f"\n--- Retention Audit: {name} ({report['pages']} pages) ---")
    print(f"Numbers:    {numbers['retained']:>6}/{numbers['gold']:<6} "
          f"kept  {numbers['retention_pct']:>7.3f}%")
    print(f"Entities:   {entities['retained']:>6}/{entities['gold']:<6} "
          f"kept  {entities['retention_pct']:>7.3f}%")
    print(f"References: {refs['retained']:>6}/{refs['gold']:<6} "
          f"kept  {refs['retention_pct']:>7.3f}%")
    if report.get("answer_spans"):
        spans = report["answer_spans"]
        print(f"Answer spans: {spans['retained']}/{spans['checked']} passages "
              f"survived ({spans['retention_pct']:.1f}%)")
    for stage, data in numbers["unintended_losses"].items():
        print(f"  ! {stage}: {data['count']} numbers lost, e.g. {data['examples'][:5]}")
    for stage, data in entities["unintended_losses"].items():
        print(f"  ! {stage}: {data['count']} entities lost, e.g. {data['examples'][:5]}")
    for stage, data in numbers["scope_removed"].items():
        entity_count = report["entities"]["scope_removed"].get(stage, {}).get("count", 0)
        print(f"  scope: {stage} removed {data['count']} numbers and {entity_count} "
              f"entities, e.g. {data['examples'][:4]}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Measure content retention of the cleaning pipeline.")
    parser.add_argument("--inputs", default=os.path.join(_ROOT, "inputs"),
                        help="Directory to scan for PDFs.")
    parser.add_argument("--gold", default=os.path.join(_ROOT, "eval", "gold.json"),
                        help="Curated answer-span questions.")
    parser.add_argument("--out", default=os.path.join("outputs", "retention.json"))
    parser.add_argument("--check", action="store_true",
                        help="Exit non-zero when retention falls below the CI floors.")
    args = parser.parse_args(argv)

    gold = _load_gold(args.gold)
    paths = sorted(glob.glob(os.path.join(args.inputs, "**", "*.pdf"), recursive=True))
    if not paths:
        print(f"No PDFs found under {args.inputs}", file=sys.stderr)
        return 2

    print(f"Extractor: {extractor_version()}")

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
                "stage_chain": [name for name, _ in STAGE_CHAIN],
                "documents": reports,
            }, handle, indent=2)
        print(f"\nRetention audit written to {args.out} ({len(reports)} document(s)).")

    failures = []
    for report in reports:
        numbers = report["numbers"]
        entities = report["entities"]
        references = report["references"]
        for label, data, floor in (
            ("number", numbers, FLOOR_NUMBER_RETENTION),
            ("entity", entities, FLOOR_ENTITY_RETENTION),
            ("reference", references, FLOOR_REFERENCE_RETENTION),
        ):
            if data["retention_pct"] < floor:
                failures.append(f"{report['document']}: {label} retention "
                                f"{data['retention_pct']}% < {floor}%")
        spans = report.get("answer_spans")
        if spans and spans["retention_pct"] < 100.0:
            failures.append(f"{report['document']}: answer spans "
                            f"{spans['retained']}/{spans['checked']} retained")

    if failures:
        print("\nRetention floors failed:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
