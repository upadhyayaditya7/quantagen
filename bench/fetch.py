"""Fetch and verify the benchmark corpus.

The corpus is referenced, not bundled: each document is a URL (or a path already
in this repo) plus the SHA-256 it must have. That keeps copyrighted PDFs out of
the repo, makes a run reproducible months later, and turns "the numbers moved"
into "the input changed", which is the first thing to rule out.

    python bench/fetch.py --check     validate the manifest, report each state
    python bench/fetch.py             download what is missing, verify hashes
    python bench/fetch.py --record    print hashes to paste into corpus.json

Downloading is deliberately a separate step from scoring: a benchmark run should
never silently pull different bytes than the run it is being compared against.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import ssl
import sys
import tempfile
import time
import urllib.error
import urllib.request

BENCH = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BENCH)
CACHE = os.path.join(BENCH, "cache")
CORPUS = os.path.join(BENCH, "corpus.json")

# An id becomes a filename under bench/cache, so it has to be safe by shape
# rather than by sanitising: anything that cannot be a plain identifier is
# rejected outright.
_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{1,63}$")
_REQUIRED = ("id", "category", "source", "sha256", "why")
_USER_AGENT = "quantagen-bench/0.1 (+https://github.com/upadhyayaditya7/quantagen)"
_TIMEOUT_SECONDS = 60


class CorpusError(ValueError):
    """The manifest is malformed; the message names the document and the field."""


def load_corpus(path: str = CORPUS) -> dict:
    with open(path, encoding="utf-8") as handle:
        corpus = json.load(handle)
    validate(corpus)
    return corpus


def validate(corpus: dict) -> None:
    """Raise CorpusError for anything that would make results incomparable."""
    documents = corpus.get("documents")
    if not isinstance(documents, list) or not documents:
        raise CorpusError("corpus has no documents")

    seen = set()
    for index, entry in enumerate(documents):
        where = f"documents[{index}]"
        for field in _REQUIRED:
            if field not in entry:
                raise CorpusError(f"{where}: missing required field {field!r}")

        document_id = entry["id"]
        if not isinstance(document_id, str) or not _ID.match(document_id):
            raise CorpusError(f"{where}: id {document_id!r} is not a safe identifier")
        if document_id in seen:
            raise CorpusError(f"{where}: duplicate id {document_id!r}")
        seen.add(document_id)

        category = entry["category"]
        if category not in corpus.get("coverage", {}):
            raise CorpusError(f"{where}: category {category!r} is not in coverage")

        source = entry["source"] or {}
        kind = source.get("kind")
        if kind == "url" and not source.get("url"):
            raise CorpusError(f"{where}: url source needs 'url'")
        if kind == "local" and not source.get("path"):
            raise CorpusError(f"{where}: local source needs 'path'")
        if kind not in ("url", "local"):
            raise CorpusError(f"{where}: source.kind must be 'url' or 'local', got {kind!r}")

        digest = entry["sha256"]
        if digest is not None and not re.fullmatch(r"[0-9a-f]{64}", str(digest)):
            raise CorpusError(f"{where}: sha256 must be 64 lowercase hex characters or null")


def sha256_of(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def cache_path(document_id: str) -> str:
    """Where a downloaded document lives. Paths stay inside bench/cache."""
    return os.path.join(CACHE, f"{document_id}.pdf")


def source_path(entry: dict) -> str:
    """The file a document is read from, whether bundled or downloaded."""
    source = entry["source"]
    if source["kind"] == "local":
        return os.path.join(ROOT, source["path"])
    return cache_path(entry["id"])


def status(entry: dict) -> dict:
    """One document's state, without touching the network.

    States: ``verified`` (present and hash matches), ``unpinned`` (present but
    the manifest records no hash yet), ``mismatch`` (present, wrong bytes),
    ``missing`` (not fetched), ``absent`` (a local path that is not there).
    """
    path = source_path(entry)
    expected = entry.get("sha256")
    if not os.path.isfile(path):
        return {"id": entry["id"], "state": "absent" if entry["source"]["kind"] == "local" else "missing",
                "path": path, "detail": ""}
    actual = sha256_of(path)
    if expected is None:
        return {"id": entry["id"], "state": "unpinned", "path": path,
                "detail": f"sha256 {actual} is not pinned in corpus.json"}
    if actual != expected:
        return {"id": entry["id"], "state": "mismatch", "path": path,
                "detail": f"expected {expected}, found {actual}"}
    return {"id": entry["id"], "state": "verified", "path": path, "detail": ""}


def ssl_context():
    """TLS trust store for the download.

    Windows Python ships no default CA bundle (`ssl.get_default_verify_paths()`
    is empty), so urllib fails with CERTIFICATE_VERIFY_FAILED on a stock
    install. certifi is used when it is importable; verification is never
    disabled, because a benchmark that downloads over an unverified channel is
    not reproducible evidence.
    """
    context = ssl.create_default_context()
    try:
        import certifi
    except ImportError:
        return context
    context.load_verify_locations(certifi.where())
    return context


def download(url: str, destination: str) -> None:
    """Fetch `url` into `destination`, atomically, refusing partial files."""
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    handle, temporary = tempfile.mkstemp(dir=os.path.dirname(destination), suffix=".part")
    os.close(handle)
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS,
                                     context=ssl_context()) as response:
            if response.status != 200:
                raise CorpusError(f"{url} returned HTTP {response.status}")
            with open(temporary, "wb") as out:
                for block in iter(lambda: response.read(1 << 20), b""):
                    out.write(block)
        os.replace(temporary, destination)
    except urllib.error.URLError as exc:
        hint = ""
        if "CERTIFICATE_VERIFY_FAILED" in str(exc):
            hint = (" -- this Python has no CA bundle; `pip install certifi` "
                    "(fetch.py uses it automatically when present)")
        raise CorpusError(f"could not download {url}: {exc}{hint}") from exc
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def fetch(entry: dict, allow_download: bool = True) -> dict:
    """Make sure one document is present and matches the manifest."""
    state = status(entry)
    if state["state"] in ("verified", "unpinned"):
        return state
    if state["state"] == "mismatch":
        # Never overwrite silently: a different file for a pinned URL means the
        # upstream changed or the manifest is wrong, and both need a decision.
        raise CorpusError(f"{entry['id']}: {state['detail']}. Delete {state['path']} "
                          "and re-pin deliberately if the upstream document changed.")
    if state["state"] == "absent":
        raise CorpusError(f"{entry['id']}: local file {state['path']} is missing")
    if not allow_download:
        return state

    destination = cache_path(entry["id"])
    time.sleep(1.0)  # arXiv and sec.gov ask for polite request rates
    download(entry["source"]["url"], destination)
    state = status(entry)
    if state["state"] == "mismatch":
        os.unlink(destination)
        raise CorpusError(f"{entry['id']}: downloaded bytes do not match the manifest ({state['detail']})")
    return state


def describe(entry: dict) -> str:
    state = status(entry)
    return f"{entry['id']:<28} {entry['category']:<14} {state['state']:<9} {state['detail']}"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Fetch and verify the benchmark corpus.")
    parser.add_argument("--corpus", default=CORPUS)
    parser.add_argument("--list", action="store_true", help="show the manifest without touching files")
    parser.add_argument("--check", action="store_true", help="validate and report state; no downloads")
    parser.add_argument("--record", action="store_true", help="print hashes for unpinned documents")
    parser.add_argument("--only", action="append", default=[], metavar="ID",
                        help="restrict to these document ids")
    args = parser.parse_args(argv)

    try:
        corpus = load_corpus(args.corpus)
    except (CorpusError, OSError, json.JSONDecodeError) as exc:
        print(f"corpus error: {exc}", file=sys.stderr)
        return 2

    selected = [entry for entry in corpus["documents"]
                if not args.only or entry["id"] in args.only]
    unknown = set(args.only) - {entry["id"] for entry in corpus["documents"]}
    if unknown:
        print(f"corpus error: unknown document id(s): {', '.join(sorted(unknown))}", file=sys.stderr)
        return 2

    if args.list:
        for entry in selected:
            source = entry["source"]
            where = source.get("url") or source.get("path")
            pinned = "pinned" if entry.get("sha256") else "unpinned"
            print(f"{entry['id']:<28} {entry['category']:<14} {pinned:<9} {where}")
        return 0

    if args.check:
        failures = 0
        for entry in selected:
            state = status(entry)
            print(describe(entry))
            failures += state["state"] in ("mismatch", "absent")
        if failures:
            print(f"\n{failures} document(s) cannot be scored: fix the paths above.", file=sys.stderr)
            return 1
        return 0

    if args.record:
        for entry in selected:
            state = status(entry)
            if state["state"] == "verified":
                print(f"{entry['id']}: {entry['sha256']}  (already pinned)")
            elif state["state"] in ("unpinned", "mismatch"):
                print(f"{entry['id']}: {sha256_of(state['path'])}")
            else:
                print(f"{entry['id']}: {state['state']} -- fetch first", file=sys.stderr)
        return 0

    failures = 0
    for entry in selected:
        try:
            state = fetch(entry)
        except CorpusError as exc:
            print(str(exc), file=sys.stderr)
            failures += 1
            continue
        print(describe(entry))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
