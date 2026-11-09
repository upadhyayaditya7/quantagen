"""Head-to-head comparison against other PDF extractors (manual, never in CI).

Retention and retrieval are measured on *our* output, which cannot show whether
the pipeline is worth its complexity. This puts candidate text from other tools
through the same 18 questions, the same chunking and the same metrics, so the
comparison is one set of questions and one set of rules.

Two ways in:

    python eval/compare.py --tool pymupdf4llm          # extract live
    python eval/compare.py --from-file "pymupdf4llm={stem}.pymupdf4llm.md"

`--from-file` exists so a comparison can be reproduced on a machine where the
other tool is not installed (some of them pull hundreds of MB of OCR stack).
Nothing here is a project dependency, so CI never needs it.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time
from datetime import datetime, timezone

# Allow `python eval/compare.py` from the project root.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import fitz  # noqa: E402  (after the sys.path bootstrap)

import main  # noqa: E402
from eval.retention import answer_span_recall, extractor_version, stage_texts  # noqa: E402
from eval.retrieval import evaluate  # noqa: E402
from rules import layout  # noqa: E402

TOOLS = {
    "pymupdf4llm": lambda path: __import__("pymupdf4llm").to_markdown(path),
}


def extract_with(tool: str, path: str) -> str:
    """Run an external extractor, or explain precisely why it cannot run."""
    loader = TOOLS.get(tool)
    if loader is None:
        raise ValueError(f"unknown tool {tool!r}; supported: {', '.join(sorted(TOOLS))}")
    try:
        return loader(path)
    except ImportError as exc:
        raise RuntimeError(
            f"{tool} is not installed; run `pip install {tool}` in a throwaway "
            "environment (it is deliberately not a project dependency)"
        ) from exc


def metrics(reference: str, text: str, items: list) -> dict:
    """One variant's cost and quality, measured against the raw document."""
    spans = answer_span_recall(reference, text, items)
    retrieval = evaluate(items, text)
    return {
        "tokens": main.get_token_count(text),
        "chars": len(text),
        "answer_spans_retained": spans["retained"],
        "answer_spans_checked": spans["checked"],
        "hit_at_3_pct": retrieval["hit_at_3_pct"],
        "mrr": retrieval["mrr"],
    }


def compare(reference: str, variants: dict, items: list) -> dict:
    """Score every variant against the raw document and against each other."""
    baseline = metrics(reference, reference, items)
    scored = {}
    for name, text in variants.items():
        result = metrics(reference, text, items)
        result["tokens_vs_reference_pct"] = round(
            100.0 * (result["tokens"] - baseline["tokens"]) / (baseline["tokens"] or 1), 1)
        result["answer_spans_delta"] = result["answer_spans_retained"] - baseline["answer_spans_retained"]
        result["mrr_delta"] = round(result["mrr"] - baseline["mrr"], 3)
        scored[name] = result
    return {"reference": baseline, "variants": scored}


def _load_gold(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle).get("documents", {})
    except FileNotFoundError:
        return {}


def _from_file(pattern: str, stem: str) -> str | None:
    path = pattern.replace("{stem}", stem)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    return None


def main_cli(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Compare Quantagen against other PDF extractors.")
    parser.add_argument("--inputs", default=os.path.join(_ROOT, "inputs"))
    parser.add_argument("--gold", default=os.path.join(_ROOT, "eval", "gold.json"))
    parser.add_argument("--tool", action="append", default=[], choices=sorted(TOOLS),
                        help="External extractor to run (needs it installed).")
    parser.add_argument("--from-file", action="append", default=[],
                        help="NAME=PATH text already extracted by another tool; "
                             "{stem} is replaced with the PDF's file stem.")
    parser.add_argument("--out", default=os.path.join("outputs", "comparison.json"))
    args = parser.parse_args(argv)

    gold = _load_gold(args.gold)
    paths = sorted(glob.glob(os.path.join(args.inputs, "**", "*.pdf"), recursive=True))
    if not paths:
        print(f"No PDFs found under {args.inputs}", file=sys.stderr)
        return 2

    from_files = []
    for entry in args.from_file:
        name, _, pattern = entry.partition("=")
        if not pattern:
            parser.error(f"--from-file expects NAME=PATH, got {entry!r}")
        from_files.append((name, pattern))

    print(f"Extractor: {extractor_version()}")

    reports = []
    for path in paths:
        stem = os.path.splitext(os.path.basename(path))[0]
        items = gold.get(os.path.basename(path), [])

        started = time.perf_counter()
        doc = fitz.open(path)
        reference = "\n".join(page.get_text() for page in doc)
        base = layout.extract_text(doc)
        clean = stage_texts(base)[-1][1]
        ours_seconds = round(time.perf_counter() - started, 2)

        variants = {"quantagen": clean}
        timings = {}
        for tool in args.tool:
            started = time.perf_counter()
            variants[tool] = extract_with(tool, path)
            timings[tool] = round(time.perf_counter() - started, 1)
        for name, pattern in from_files:
            text = _from_file(pattern, stem)
            if text is not None:
                variants[name] = text

        result = compare(reference, variants, items)
        result["document"] = os.path.basename(path)
        result["pages"] = doc.page_count
        result["questions"] = len(items)
        result["seconds"] = {"quantagen": ours_seconds, **timings}
        reports.append(result)

        print(f"\n--- Compare: {result['document']} ({result['pages']} pages, {len(items)} questions) ---")
        print(f"  {'variant':<12}{'tokens':>9}{'vs raw':>9}  {'spans':<7}{'hit@3':>7}{'MRR':>7}{'secs':>7}")
        for name, data in [("raw", result["reference"])] + list(result["variants"].items()):
            delta = f"{data.get('tokens_vs_reference_pct', 0.0):+.1f}%" if name != "raw" else "-"
            seconds = result["seconds"].get(name)
            print(f"  {name:<12}{data['tokens']:>9}{delta:>9}  "
                  f"{data['answer_spans_retained']}/{data['answer_spans_checked']:<6}"
                  f"{data['hit_at_3_pct']:>6.1f}%{data['mrr']:>7.3f}"
                  f"{(seconds if seconds is not None else '-'):>7}")

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump({
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "extractor": extractor_version(),
            "documents": reports,
        }, handle, indent=2)
    print(f"\nComparison written to {args.out} ({len(reports)} document(s)).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
