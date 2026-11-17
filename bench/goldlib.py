"""Gold answer spans: what each document must still be able to answer.

`eval/retrieval.py` measures whether a sentence in *this* repository's samples can
still be found. This module is the same idea applied to the benchmark corpus, with
two differences that make it checkable by someone else:

1. The gold is data, not code. Each span is a verbatim quote from the PDF, so the
   claim "this document says 300,000 steps" is verified against the bytes rather
   than against the author's memory.
2. Every quote is checked back against the page it names. A span whose quote is not
   on its page fails validation, which makes a stale gold file impossible to keep
   quietly.

Two span kinds, because one shape does not fit every category:

- `answer` - a question with a short answer that appears inside the quote (what
  `eval/retrieval.py` already does for the two samples).
- `verbatim` - a passage that must survive intact, with no question. This is what
  the non-Latin category needs: the property under test is that CJK text is not
  dropped as noise, and inventing an English question about Japanese prose would
  only test the curator's vocabulary.

Image-only documents carry `"readable": false` and no spans: the correct result for
a scan without OCR is a documented skip, not text.
"""

import argparse
import json
import os
import re
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from bench import fetch  # noqa: E402  (after the sys.path insert above)

BENCH = os.path.dirname(os.path.abspath(__file__))
GOLD_DIR = os.path.join(BENCH, "gold")
SCHEMA_VERSION = 1
SPANS_PER_DOCUMENT = (8, 10)
# A character floor is Latin-centric: a CJK quote carries several words per
# character, so the same information arrives in far fewer of them. Both numbers
# were picked so that "4.5" fails and a real sentence passes in either script.
MIN_QUOTE = 20
MIN_CJK = 10
_CJK = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uff66-\uff9f]")


class GoldError(Exception):
    """Raised when a gold file disagrees with the corpus or with the document."""


def normalise(text: str) -> str:
    """Collapse every run of whitespace to a single space.

    Line breaks are an artefact of a particular extractor, so a quote is compared
    after both sides are flattened. A non-breaking space and a soft hyphen are not
    whitespace to `str.split`, so they are replaced first.
    """
    return " ".join(text.replace("\u00a0", " ").replace("\u00ad", "").split())


def gold_path(document_id: str) -> str:
    return os.path.join(GOLD_DIR, f"{document_id}.json")


def load(document_id: str):
    path = gold_path(document_id)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def load_all() -> dict:
    if not os.path.isdir(GOLD_DIR):
        return {}
    found = {}
    for name in sorted(os.listdir(GOLD_DIR)):
        if name.endswith(".json"):
            found[name[:-5]] = load(name[:-5])
    return found


def span_count(gold: dict) -> int:
    return len(gold.get("spans") or [])


def _validate_spans(gold: dict) -> None:
    spans = gold["spans"]
    low, high = SPANS_PER_DOCUMENT
    if not low <= len(spans) <= high:
        raise GoldError(f"{gold['id']}: {len(spans)} spans, expected {low}-{high}")

    seen = set()
    for span in spans:
        if not span.get("id"):
            raise GoldError(f"{gold['id']}: a span has no id")
        if span["id"] in seen:
            raise GoldError(f"{gold['id']}: duplicate span id {span['id']}")
        seen.add(span["id"])

        quote = normalise(span.get("quote", ""))
        if len(quote) < MIN_QUOTE and len(_CJK.findall(quote)) < MIN_CJK:
            raise GoldError(
                f"{gold['id']}/{span['id']}: quote is too short to be evidence "
                f"({len(quote)} characters, {len(_CJK.findall(quote))} CJK)")
        if not isinstance(span.get("page_index"), int) or span["page_index"] < 0:
            raise GoldError(f"{gold['id']}/{span['id']}: page_index must be a 0-based integer")

        kind = span.get("kind")
        if kind == "answer":
            answer = normalise(span.get("answer", ""))
            if not answer:
                raise GoldError(f"{gold['id']}/{span['id']}: an answer span needs an answer")
            if answer not in quote:
                raise GoldError(
                    f"{gold['id']}/{span['id']}: answer {answer!r} is not inside its own quote")
            if not span.get("question", "").strip():
                raise GoldError(f"{gold['id']}/{span['id']}: an answer span needs a question")
        elif kind != "verbatim":
            raise GoldError(f"{gold['id']}/{span['id']}: unknown kind {kind!r}")


def validate(gold: dict, document: dict = None, pages=None) -> None:
    """Check one gold file, against the corpus entry and optionally the PDF.

    `pages` is a list of normalised page texts (index 0 is page 1). Passing it turns
    on the strongest check available: the quote must be on the page it claims.
    """
    if gold.get("schema_version") != SCHEMA_VERSION:
        raise GoldError(f"{gold.get('id')}: schema_version is not {SCHEMA_VERSION}")
    document_id = gold.get("id")
    if not document_id or not document_id.replace("-", "").replace(".", "").isalnum():
        raise GoldError(f"{document_id!r}: id must be a safe identifier")

    if document is not None:
        if document_id != document["id"]:
            raise GoldError(f"{document_id}: filename does not match id {document['id']}")
        if gold.get("sha256") != document["sha256"]:
            raise GoldError(
                f"{document_id}: gold was curated against different bytes "
                "(sha256 does not match corpus.json)")
        if gold.get("category") != document["category"]:
            raise GoldError(f"{document_id}: category does not match the corpus")

    if not isinstance(gold.get("readable"), bool):
        raise GoldError(f"{document_id}: readable must be true or false")

    if not gold["readable"]:
        if gold.get("spans"):
            raise GoldError(f"{document_id}: an unreadable document cannot have answer spans")
        if not gold.get("expectation"):
            raise GoldError(f"{document_id}: say what the correct behaviour is instead")
        return

    _validate_spans(gold)

    if pages is None:
        return
    for span in gold["spans"]:
        quote = normalise(span["quote"])
        page_index = span["page_index"]
        if page_index >= len(pages):
            raise GoldError(f"{document_id}/{span['id']}: page {page_index} is past the end")
        if quote not in pages[page_index]:
            raise GoldError(
                f"{document_id}/{span['id']}: quote is not on page {page_index} "
                "(curated against a different file, or edited by hand)")


def document_pages(document: dict):
    """Normalised text per page, or None when the bytes are not on this machine."""
    try:
        import fitz
    except ImportError:  # pragma: no cover - PyMuPDF is a hard dependency
        return None
    path = fetch.source_path(document)
    if fetch.status(document)["state"] not in {"verified", "unpinned"}:
        return None
    with fitz.open(path) as doc:
        return [normalise(page.get_text()) for page in doc]


def check(with_documents: bool = True) -> list:
    """Validate every gold file present. Returns a list of problems, never raises."""
    corpus = fetch.load_corpus()
    by_id = {entry["id"]: entry for entry in corpus["documents"]}
    problems = []

    for document_id, gold in load_all().items():
        document = by_id.get(document_id)
        if document is None:
            problems.append(f"{document_id}: there is no such document in corpus.json")
            continue
        pages = None
        if with_documents:
            try:
                pages = document_pages(document)
            except Exception as exc:  # a broken download is a problem, not a crash
                problems.append(f"{document_id}: could not read the PDF ({exc})")
                continue
        try:
            validate(gold, document, pages)
        except GoldError as exc:
            problems.append(str(exc))
    return problems


def coverage(corpus: dict = None) -> dict:
    corpus = corpus or fetch.load_corpus()
    gold = load_all()
    counts = {}
    for entry in corpus["documents"]:
        block = counts.setdefault(
            entry["category"], {"documents": 0, "with_gold": 0, "spans": 0})
        block["documents"] += 1
        if entry["id"] in gold:
            block["with_gold"] += 1
            block["spans"] += span_count(gold[entry["id"]])
    return counts


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true",
                        help="validate every gold file against the corpus and the PDFs")
    parser.add_argument("--coverage", action="store_true",
                        help="how many documents have gold, per category")
    parser.add_argument("--no-documents", action="store_true",
                        help="structure only: skip the check against the PDFs")
    args = parser.parse_args(argv)

    if args.check or not args.coverage:
        problems = check(with_documents=not args.no_documents)
        if problems:
            for problem in problems:
                print(f"  FAIL  {problem}")
            print(f"{len(problems)} problem(s)")
            return 1
        total = sum(span_count(gold) for gold in load_all().values())
        print(f"gold OK: {len(load_all())} documents, {total} spans")

    if args.coverage:
        corpus = fetch.load_corpus()
        counts = coverage(corpus)
        print(f"{'category':<16}{'docs':>6}{'gold':>6}{'spans':>7}")
        for category, block in counts.items():
            print(f"{category:<16}{block['documents']:>6}{block['with_gold']:>6}"
                  f"{block['spans']:>7}")
        missing = [entry["id"] for entry in corpus["documents"]
                   if entry["id"] not in load_all()]
        if missing:
            print(f"\nno gold yet ({len(missing)}): {', '.join(missing)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
