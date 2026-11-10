"""Console entry point: `quantagen [PATH ...]`.

Defaults reproduce the pre-packaging behaviour -- every PDF under ./inputs is
processed and the audit trail lands in ./outputs -- but explicit paths and
--out make it usable from any directory, which is what a console script is for.
"""

from __future__ import annotations

import argparse
import os
import sys

from . import __version__
from .pipeline import DOCUMENT_REPORTS, process_document, write_report


def collect(paths: list) -> list:
    """Expand files and directories into a de-duplicated PDF list, in order."""
    pdfs = []
    for path in paths:
        if os.path.isdir(path):
            for root, _dirs, files in os.walk(path):
                pdfs.extend(os.path.join(root, name) for name in sorted(files)
                            if name.lower().endswith(".pdf"))
        elif path.lower().endswith(".pdf"):
            pdfs.append(path)
        else:
            print(f"Skipping {path}: not a PDF or a directory", file=sys.stderr)
    seen, unique = set(), []
    for path in pdfs:
        key = os.path.normcase(os.path.abspath(path))
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="quantagen",
        description="Turn PDFs into token-efficient, LLM-ready text with an "
                    "auditable loss ledger.",
    )
    parser.add_argument("paths", nargs="*", metavar="PATH",
                        help="PDF files or directories to scan (default: ./inputs)")
    parser.add_argument("--out", default="outputs", metavar="DIR",
                        help="directory for cleaned .txt files and the audit trail "
                             "(default: outputs)")
    parser.add_argument("--report", default=None, metavar="PATH",
                        help="audit report path (default: <out>/report.json)")
    parser.add_argument("--version", action="version", version=f"quantagen {__version__}")
    args = parser.parse_args(argv)

    requested = args.paths or ["inputs"]
    missing = [path for path in requested if not os.path.exists(path)]
    for path in missing:
        print(f"Error: '{path}' not found.", file=sys.stderr)
    if not requested and missing:
        return 2

    pdfs = collect([path for path in requested if path not in missing])
    if not pdfs:
        print("No PDFs found. Pass file or directory paths, or put PDFs in ./inputs.",
              file=sys.stderr)
        return 2

    print(f"Quantagen v{__version__}: {len(pdfs)} PDF(s) queued.")
    failures = 0
    for path in pdfs:
        cleaned, _sections = process_document(path, output_folder=args.out)
        if cleaned is None:
            failures += 1

    if DOCUMENT_REPORTS:
        report_path = write_report(args.report or os.path.join(args.out, "report.json"))
        print(f"\nAudit trail written to {report_path} "
              f"({len(DOCUMENT_REPORTS)} document(s)).")
    if failures:
        print(f"\n{failures} of {len(pdfs)} document(s) failed.", file=sys.stderr)
        return 1
    print("\nBatch processing complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
