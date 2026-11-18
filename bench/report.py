"""Render bench/RESULTS.md from the corpus: one command, nothing hand-edited.

    python bench/report.py             # every category at its target
    python bench/report.py --categories tables slides
    python bench/report.py --stamp     # add a timestamp (not reproducible)

The output is deterministic: the same cache and the same code produce a
byte-identical file. That is deliberate -- a report that changes when nobody
changed the measurement cannot be quoted.

Publication rule (pre-registered in bench/README.md): a category appears as a
result only when it holds its target number of documents. Categories below
target are listed with their count and no numbers, rather than averaged into a
smaller table that looks like a finding.
"""

from __future__ import annotations

import argparse
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from bench import fetch, goldlib, metrics  # noqa: E402
from bench.adapters import ADAPTERS  # noqa: E402

BENCH = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT = os.path.join(BENCH, "RESULTS.md")
WIDTHS = {"ratio": 2, "pct": 1, "mrr": 3, "secs": 2}


def _num(value, kind: str) -> str:
    """A metric cell. An absent measurement is an em dash, never a zero."""
    if value is None:
        return "—"
    return f"{value:.{WIDTHS[kind]}f}"


def _mean(values):
    values = [value for value in values if value is not None]
    return sum(values) / len(values) if values else None


def _tools(results: dict, category: str) -> dict:
    """Per-tool records for one category, keyed by adapter name, in order."""
    rows = {}
    for document in results["documents"]:
        if document["category"] != category:
            continue
        for name, record in document["tools"].items():
            rows.setdefault(name, []).append(record)
    return rows


def _status_counts(records, status: str) -> int:
    return sum(1 for record in records if record["status"] == status)


def _category_table(results: dict, category: str, timings: bool = False) -> list:
    coverage = results["categories"][category]
    curated = coverage["gold"]
    lines = [
        f"## {category} — {coverage['documents']}/{coverage['target']} documents"
        f", {curated} with gold",
        "",
    ]
    curated_ids = [document["id"] for document in results["documents"]
                   if document["category"] == category and document["gold"]["spans"]]
    if curated_ids:
        lines += ["Gold spans: " + ", ".join(f"`{name}`" for name in curated_ids)
                  + ". Every other document in this category is measured on cost "
                  "and retention only.", ""]
    elif coverage["gold"]:
        lines += ["Gold for this category is an expectation, not answer spans: "
                  "these documents have no text layer, and the table below is "
                  "the measurement.", ""]
    else:
        lines += ["No gold spans yet: measured on cost and retention only.", ""]
    if category == "scanned":
        lines += _scan_table(results, category)
        return lines

    header = ("| Tool | Version | Docs | Token ratio | Numbers | Entities "
              "| Refs | Spans | hit@3 | MRR")
    rule = "|---|---|---:|---:|---:|---:|---:|---:|---:|---:"
    if timings:
        header += " | s/doc"
        rule += "|---:"
    lines += [header + " |", rule + "|"]
    for name, records in _tools(results, category).items():
        measured = [record for record in records if record["status"] == "ok"]
        if not measured:
            continue
        spans_retained = sum(record["spans"]["retained"] for record in measured)
        spans_checked = sum(record["spans"]["checked"] for record in measured)
        span_cell = (f"{spans_retained}/{spans_checked}" if spans_checked else "—")
        hit = _mean([record["retrieval"]["hit_at_3"] for record in measured
                     if record["retrieval"]])
        mrr = _mean([record["retrieval"]["mrr"] for record in measured
                     if record["retrieval"]])
        row = (
            f"| {name} | {measured[0]['version']} | {len(measured)} "
            f"| {_num(_mean([r['token_ratio'] for r in measured]), 'ratio')} "
            f"| {_num(_mean([r['number_retention'] for r in measured]), 'pct')} "
            f"| {_num(_mean([r['entity_retention'] for r in measured]), 'pct')} "
            f"| {_num(_mean([r['reference_retention'] for r in measured]), 'pct')} "
            f"| {span_cell} "
            f"| {_num(hit, 'pct')} | {_num(mrr, 'mrr')}"
        )
        if timings:
            row += f" | {_num(_mean([r['seconds'] for r in measured]), 'secs')}"
        lines.append(row + " |")
    lines.append("")
    lines += _document_table(results, category, timings)
    return lines


def _document_table(results: dict, category: str, timings: bool = False) -> list:
    """Per-document spread, so an average cannot hide the document it came from."""
    documents = [document for document in results["documents"]
                 if document["category"] == category]
    tools = [name for name, records in _tools(results, category).items()
             if any(record["status"] == "ok" for record in records)]
    if not tools:
        return []

    labels = ["Document", "Pages"]
    for name in tools:
        labels.append(f"{name} ratio")
        if timings:
            labels.append(f"{name} s")
    lines = ["### Per document", "",
             "| " + " | ".join(labels) + " |",
             "|---" * len(labels) + "|"]
    for document in documents:
        values = [f"`{document['id']}`", str(document["pages"])]
        for name in tools:
            record = document["tools"].get(name, {})
            ok = record.get("status") == "ok"
            values.append(_num(record.get("token_ratio") if ok else None, "ratio"))
            if timings:
                values.append(_num(record.get("seconds") if ok else None, "secs"))
        lines.append("| " + " | ".join(values) + " |")
    lines.append("")
    return lines


def _scan_table(results: dict, category: str) -> list:
    """Image-only documents are scored on what the tool did, not on tokens."""
    lines = [
        "No text layer exists in these documents, so there is no token ratio "
        "to report. The column that matters is what the tool did: report that "
        "it could not read the file, return nothing, or invent characters.",
        "",
        "| Tool | Version | Docs | Reported unreadable | Returned no text "
        "| Returned characters |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for name, records in _tools(results, category).items():
        available = [record for record in records
                     if record["status"] in {"ok", "unreadable"}]
        if not available:
            continue
        invented = sum(1 for record in available
                       if record["status"] == "ok" and not record.get("blank"))
        lines.append(
            f"| {name} | {available[0].get('version', 'unknown')} "
            f"| {len(available)} "
            f"| {_status_counts(available, 'unreadable')} "
            f"| {sum(1 for r in available if r['status'] == 'ok' and r.get('blank'))} "
            f"| {invented} |"
        )
    lines.append("")
    return lines


def _loss_log(results: dict, category: str) -> list:
    """Every lost span and every failed adapter, named. Reporting rule 2."""
    lines = []
    for document in results["documents"]:
        if document["category"] != category:
            continue
        for name, record in document["tools"].items():
            if record["status"] == "error":
                lines.append(f"- `{document['id']}` / {name}: crashed — {record['detail']}")
            lost = record.get("spans", {}).get("lost") or []
            if lost:
                lines.append(f"- `{document['id']}` / {name}: lost "
                             f"{', '.join(f'`{span}`' for span in lost)}")
    if not lines:
        return []
    return ["### Losses", "", *lines, ""]


def _inventory(results: dict) -> list:
    """Who ran, who did not, and how to make the missing ones run."""
    measured, missing = [], []
    for name in ADAPTERS:
        records = [record for document in results["documents"]
                   for tool, record in document["tools"].items() if tool == name]
        if any(record["status"] in {"ok", "unreadable"} for record in records):
            measured.append(name)
        else:
            missing.append(name)
    lines = ["## Entrants", "",
             f"Measured here: **{', '.join(measured) or 'none'}**."
             + (" Not measured: " + ", ".join(f"`{name}`" for name in missing) + "."
                if missing else "")]
    for name in missing:
        if ADAPTERS[name].available():
            lines.append(f"- `{name}`: installed, but not run in this report")
        else:
            lines.append(f"- `{name}`: not installed on this machine — "
                         f"`{ADAPTERS[name].install}`")
    lines.append("")
    return lines


def _unpublished(results: dict) -> list:
    below = [f"`{name}` ({block['documents']}/{block['target']})"
             for name, block in results["categories"].items()
             if block["documents"] < block["target"]]
    if not below:
        return []
    return ["## Not yet published", "",
            "Categories below their pre-registered target, scored by nothing "
            "here: " + ", ".join(below) + ".", ""]


def render(results: dict, stamp: str = None, timings: bool = False) -> str:
    """RESULTS.md as text. Byte-identical across runs unless timings are on."""
    lines = [
        "# quantagen-bench results",
        "",
        "Generated by `python bench/report.py` from `bench/corpus.json` and the "
        "hash-verified files in `bench/cache/`. Nothing in this file is edited "
        "by hand; a number that cannot be regenerated is not quoted.",
        "",
        "Token counts use `tiktoken` (gpt-4o). Retrieval is BM25 over "
        "120-word chunks with 30-word overlap, averaged over three window "
        "alignments. Retention is measured against the raw PyMuPDF text of the "
        "same document, so a tool that deliberately drops page furniture or "
        "references scores lower on numbers and entities — that cost is shown "
        "here rather than excused.",
        "",
        "Wall clock is measured but excluded from this file: a timing that "
        "changes between two runs of the same code would make every row "
        "unquotable. Run `python bench/report.py --timings` for the cost table.",
        "",
    ]
    if stamp:
        lines += [f"Generated at {stamp}. This build is not byte-reproducible.",
                  ""]
    if timings:
        lines += ["This build includes timings and is therefore not "
                  "byte-reproducible.", ""]
    lines += _inventory(results)
    for category in results["categories"]:
        lines += _category_table(results, category, timings)
        lines += _loss_log(results, category)
    lines += _unpublished(results)
    return "\n".join(lines).rstrip() + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Score the benchmark corpus and render bench/RESULTS.md.")
    parser.add_argument("--categories", nargs="*", default=None,
                        help="categories to score (default: those at target)")
    parser.add_argument("--adapters", nargs="*", default=list(metrics.ADAPTER_ORDER),
                        help="extractors to run (default: all known)")
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--stamp", action="store_true",
                        help="include a timestamp (breaks byte-reproducibility)")
    parser.add_argument("--timings", action="store_true",
                        help="include wall clock, which varies between runs")
    args = parser.parse_args(argv)

    corpus = fetch.load_corpus()
    categories = args.categories or [
        name for name, block in corpus["coverage"].items()
        if not name.startswith("_") and block["current"] >= block["target"]]
    unknown = [name for name in categories if name not in corpus["coverage"]]
    if unknown:
        parser.error(f"unknown categories: {', '.join(unknown)}")

    def progress(entry):
        print(f"  scoring {entry['id']} ...", flush=True)

    try:
        results = metrics.run(corpus, args.adapters, categories, progress)
    except metrics.ResultsUnavailable as exc:
        print(f"Cannot report: {exc}", file=sys.stderr)
        return 2

    stamp = None
    if args.stamp:
        from datetime import datetime, timezone
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")

    report = render(results, stamp, args.timings)
    with open(args.out, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(report)
    print(f"\nResults written to {args.out} ({len(results['documents'])} "
          f"documents, {len(categories)} categories).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
