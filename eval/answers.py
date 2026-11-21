"""Answer accuracy: can a model answer from this text, and what does it cost?

Retention proves content survived, and `eval/retrieval.py` proves a BM25 index
can still find it. Neither is the claim the project actually makes. The claim
is that an LLM reading this output answers the same questions as well as an LLM
reading the raw extraction, for less money -- and until this module existed,
that sentence had never been measured here.

Design decisions, each one chosen so a reader can check the result:

* **The questions are not written for this harness.** They are the `answer`
  spans in `bench/gold/`, every quote verified against the page it names by
  `bench/goldlib.py`. A wrong answer therefore points at a quotation someone
  else can re-read, not at a prompt the author tuned until a score appeared.
* **Grading is mechanical.** The gold value has to appear in the reply,
  compared as a value rather than as a substring ("300,000" does not match
  "30,000"), using the same check the retention audit applies to the document.
  A model judge was rejected on purpose: it would be a second model with its
  own preferences scoring the first, and it would need its own benchmark.
* **Two prompt modes, because "LLM-ready" means two different things.** `rag`
  shows the model the top-k chunks a retriever would actually hand it, and
  works on any document size. `full` shows it the whole text, which is what
  pasting a document into a chat does. A document that does not fit the model's
  context window is *skipped and reported* in `full` mode, never truncated: a
  truncated document would score as the extractor's fault.
* **Every request is cached** by a hash of (model, messages, temperature, max
  output tokens) and every reply keeps the provider's `system_fingerprint`.
  Rerunning costs nothing and returns the same numbers, and a provider changing
  the model underneath the result is visible instead of looking like a pipeline
  regression.
* **Prices are data** (`eval/models.json`) with the date they were read, and
  cost per 1,000 documents is defined from measured per-question cost. A cost
  claim built on a number typed into the source is a claim nobody can audit.

What this is not: a leaderboard. Three documents carry answer spans today, so
the result is a sample with a wide interval, and it needs a paid API key, so it
is manual and never runs in CI. Both facts belong next to the number, not in a
footnote.

    export OPENAI_API_KEY=...
    python eval/answers.py --dry-run            # prompts + projected cost, no calls
    python eval/answers.py                      # measure, write outputs/answers.json
    python eval/answers.py --check               # fail if cleaning loses answers
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

# Allow `python eval/answers.py` from the project root.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from bench import fetch, goldlib, rubric  # noqa: E402
from bench.adapters import ADAPTERS  # noqa: E402
from bench.metrics import reference_text  # noqa: E402
from eval.retention import answer_in, numbers_in  # noqa: E402
from eval.retrieval import BM25, TOP_K, chunks  # noqa: E402
from quantagen.pipeline import get_token_count  # noqa: E402

PRICES = os.path.join(_ROOT, "eval", "models.json")
CACHE_VERSION = 1
_TIMEOUT_SECONDS = 120
_MAX_ATTEMPTS = 3
_RETRY_STATUS = frozenset({429, 500, 502, 503, 504})

# Every measurement parameter is read from bench/rubric.json, like the rest of
# the repository: the model, its temperature and output budget, the prompt
# shape and the accuracy floor all decide the number, so none is typed here.
PROMPT_MODE = rubric.RUBRIC["answers"]["prompt_mode"]
DEFAULT_MODEL = rubric.RUBRIC["answers"]["model"]
DEFAULT_BASE_URL = rubric.RUBRIC["answers"]["base_url"]
TEMPERATURE = rubric.RUBRIC["answers"]["temperature"]
MAX_OUTPUT_TOKENS = rubric.RUBRIC["answers"]["max_output_tokens"]
SEED = rubric.RUBRIC["answers"]["seed"]
# Percentage points of accuracy the cleaned text may give up before the run is
# a regression. One question on 24 moves the score by 4.2 points, so a smaller
# tolerance would gate on a single answer's luck rather than on the text.
ACCURACY_TOLERANCE = rubric.RUBRIC["answers"]["accuracy_tolerance"]

SYSTEM_PROMPT = (
    "You answer one question from the supplied document text. Reply with the "
    "answer only: the shortest phrase that answers it, copied from the document. "
    "Do not explain. If the document does not contain the answer, reply exactly: "
    "NOT FOUND"
)
NOT_FOUND = "NOT FOUND"


class AnswerError(RuntimeError):
    """The measurement cannot be made; the message says what is missing."""


# ------------------------------------------------------------------ the price table

def load_prices(path: str = PRICES) -> dict:
    """Read the dated price table, refusing an entry that cannot be multiplied.

    A price is not a configuration detail: it is half of the cost claim. A
    missing or nonsensical entry is an error here rather than a `None` that
    reaches the report as a blank column.
    """
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        raise AnswerError(f"{path} is missing: the cost column is measured from it") from None
    except json.JSONDecodeError as exc:
        raise AnswerError(f"{path}: not valid JSON ({exc})") from exc

    models = data.get("models")
    if not isinstance(models, dict) or not models:
        raise AnswerError(f"{path}: 'models' must be a non-empty object")
    if not data.get("prices_checked"):
        raise AnswerError(f"{path}: 'prices_checked' must date the prices")

    for name, entry in models.items():
        for key in ("input_per_million_usd", "output_per_million_usd"):
            value = entry.get(key)
            if (not isinstance(value, (int, float)) or isinstance(value, bool)
                    or value < 0):
                raise AnswerError(f"{path}: {name}.{key} must be a non-negative number")
        window = entry.get("context_window")
        if not isinstance(window, int) or isinstance(window, bool) or window < 1:
            raise AnswerError(f"{path}: {name}.context_window must be a positive integer")
    return data


def price_for(prices: dict, model: str) -> dict | None:
    """The price entry for `model`, or None so an unpriced model can still run."""
    return (prices.get("models") or {}).get(model)


def cost_usd(input_tokens: int, output_tokens: int, price: dict | None) -> float | None:
    """Exact cost of one exchange from measured tokens and published prices."""
    if price is None:
        return None
    return (input_tokens * price["input_per_million_usd"]
            + output_tokens * price["output_per_million_usd"]) / 1_000_000


# ------------------------------------------------------------------ the prompt

def answer_items(gold: dict | None) -> list:
    """The `answer` spans of one gold file, in document order.

    `verbatim` spans carry no question: the property they test is that the
    script survives at all, and inventing a question about Japanese prose would
    measure the curator's vocabulary instead.
    """
    return [
        {"id": span["id"], "question": span["question"], "answer": str(span["answer"])}
        for span in (gold or {}).get("spans") or []
        if span.get("kind") == "answer"
    ]


def context_for(text: str, question: str, mode: str = PROMPT_MODE) -> str:
    """The slice of `text` the model is shown for one question.

    `rag` is the top-k BM25 chunks -- what a retriever would actually hand the
    model -- so the score includes whether the answer is reachable at all.
    `full` is the whole document, which is the other way these files are used.
    """
    if mode == "full":
        return text
    if mode != "rag":
        raise AnswerError(f"unknown prompt mode {mode!r}; expected 'rag' or 'full'")

    chunk_list = chunks(text)
    if not chunk_list:
        return ""
    index = BM25(chunk_list)
    return "\n\n".join(chunk_list[position]
                       for position in index.rank(question)[:TOP_K])


def messages_for(question: str, context: str) -> list:
    """The chat messages for one question. Built here so a test can read them."""
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Document:\n{context}\n\nQuestion: {question}"},
    ]


def grade(answer: str, response: str) -> bool:
    """Is the gold value in the reply, compared as a value and case-insensitively?

    Grading is lenient about case because a reply is prose, not a byte range;
    it is strict about values because "30,000" is a different answer from
    "300,000" and a substring test would accept the wrong one.
    """
    text = (response or "").casefold()
    return answer_in(str(answer).casefold(), text, numbers_in(text))


# ------------------------------------------------------------------ the endpoint

def _post_json(url: str, headers: dict, payload: dict, timeout: int) -> dict:
    """One HTTPS POST, verified. `fetch.ssl_context()` supplies the CA bundle."""
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={**headers, "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout,
                                context=fetch.ssl_context()) as response:
        return json.loads(response.read().decode("utf-8"))


class AnswerModel:
    """One chat-completions endpoint, with a cache and no hidden retries.

    `transport` is injectable so the whole harness can be tested offline: the
    tests pass a function that returns canned JSON and count the calls, which is
    the only way to show the cache and the cost arithmetic are real without
    spending money on every run.
    """

    def __init__(self, model: str = DEFAULT_MODEL, base_url: str = DEFAULT_BASE_URL,
                 api_key: str | None = None, transport=None,
                 temperature: float = TEMPERATURE,
                 max_output_tokens: int = MAX_OUTPUT_TOKENS, seed: int = SEED,
                 cache_path: str | None = None):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.temperature = temperature
        self.max_output_tokens = max_output_tokens
        self.seed = seed
        self.cache_path = cache_path
        self.transport = transport or _post_json
        self.requests = 0
        self.cache = self._load_cache()

    # -- cache
    def _load_cache(self) -> dict:
        if not self.cache_path or not os.path.exists(self.cache_path):
            return {}
        try:
            with open(self.cache_path, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return {}
        if data.get("cache_version") != CACHE_VERSION:
            return {}
        return data.get("records") or {}

    def _save_cache(self) -> None:
        if not self.cache_path:
            return
        os.makedirs(os.path.dirname(self.cache_path) or ".", exist_ok=True)
        with open(self.cache_path, "w", encoding="utf-8") as handle:
            json.dump({"cache_version": CACHE_VERSION, "records": self.cache},
                      handle, indent=2, sort_keys=True)

    def _key(self, messages: list) -> str:
        """Identity of one request: everything that can change the reply."""
        material = json.dumps({
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_output_tokens": self.max_output_tokens,
            "seed": self.seed,
        }, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    # -- request
    def ask(self, messages: list) -> dict:
        """Reply text and token usage, from the cache when this exact request ran."""
        key = self._key(messages)
        if key in self.cache:
            return self.cache[key]

        if not self.api_key:
            raise AnswerError("no API key: set OPENAI_API_KEY (or --api-key-env) "
                              "before a run that actually calls the model")

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_output_tokens,
            "seed": self.seed,
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}
        url = f"{self.base_url}/chat/completions"

        response = None
        for attempt in range(_MAX_ATTEMPTS):
            self.requests += 1
            try:
                response = self.transport(url, headers, payload, _TIMEOUT_SECONDS)
                break
            except urllib.error.HTTPError as exc:
                if exc.code not in _RETRY_STATUS or attempt == _MAX_ATTEMPTS - 1:
                    detail = exc.read().decode("utf-8", "replace")[:200] if exc.fp else ""
                    raise AnswerError(f"{self.model}: HTTP {exc.code} {detail}") from exc
                time.sleep(2 ** attempt)

        record = self._record(response)
        self.cache[key] = record
        self._save_cache()
        return record

    def _record(self, response: dict) -> dict:
        """Normalise one API response into the fields the harness reasons about."""
        try:
            choice = response["choices"][0]
            text = (choice.get("message") or {}).get("content") or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise AnswerError(
                f"{self.model}: response has no choices ({str(response)[:400]})") from exc

        usage = response.get("usage") or {}
        input_tokens = usage.get("prompt_tokens")
        output_tokens = usage.get("completion_tokens")
        estimated = False
        if not isinstance(input_tokens, int) or not isinstance(output_tokens, int):
            # A provider that omits usage can still be measured; it just cannot
            # be priced from measured tokens, and the record says so.
            input_tokens = get_token_count(json.dumps(choice, ensure_ascii=False))
            output_tokens = get_token_count(text)
            estimated = True

        return {
            "text": text.strip(),
            "input_tokens": int(input_tokens),
            "output_tokens": int(output_tokens),
            "estimated_tokens": estimated,
            "system_fingerprint": response.get("system_fingerprint"),
            "model": response.get("model", self.model),
        }


# ------------------------------------------------------------------ scoring

def evaluate(text: str, items: list, model: AnswerModel,
             mode: str = PROMPT_MODE,
             context_window: int | None = None) -> dict:
    """Ask one model every curated question about one text variant.

    Returns a summary plus the per-question rows, so a lost answer can be named
    instead of only counted. A document that cannot fit the context window in
    `full` mode is skipped with the reason -- never silently truncated.
    """
    if mode == "full" and context_window:
        size = get_token_count(text)
        if size + MAX_OUTPUT_TOKENS > context_window:
            return {
                "questions": 0, "correct": 0, "accuracy_pct": None,
                "input_tokens": 0, "output_tokens": 0,
                "skipped": (f"{size} tokens do not fit the {context_window}-token "
                            "context window; not truncated"),
                "rows": [],
            }

    rows = []
    fingerprints = set()
    for item in items:
        question = item["question"]
        reply = model.ask(messages_for(question, context_for(text, question, mode)))
        if reply.get("system_fingerprint"):
            fingerprints.add(reply["system_fingerprint"])
        rows.append({
            "id": item["id"],
            "question": question,
            "answer": item["answer"],
            "response": reply["text"][:200],
            "correct": grade(item["answer"], reply["text"]),
            "not_found": reply["text"].strip().upper().startswith(NOT_FOUND),
            "input_tokens": reply["input_tokens"],
            "output_tokens": reply["output_tokens"],
        })

    total = len(rows)
    correct = sum(1 for row in rows if row["correct"])
    return {
        "questions": total,
        "correct": correct,
        "accuracy_pct": round(100.0 * correct / total, 1) if total else None,
        "input_tokens": sum(row["input_tokens"] for row in rows),
        "output_tokens": sum(row["output_tokens"] for row in rows),
        "skipped": None,
        "fingerprints": sorted(fingerprints),
        "rows": rows,
    }


def summarise(summary: dict, price: dict | None) -> dict:
    """Cost of one variant, including the per-document figure worth quoting."""
    cost = cost_usd(summary["input_tokens"], summary["output_tokens"], price)
    result = {
        "questions": summary["questions"],
        "correct": summary["correct"],
        "accuracy_pct": summary["accuracy_pct"],
        "input_tokens": summary["input_tokens"],
        "output_tokens": summary["output_tokens"],
        "cost_usd": round(cost, 6) if cost is not None else None,
        "wrong": [row["id"] for row in summary["rows"] if not row["correct"]],
        "not_found": sum(1 for row in summary["rows"] if row["not_found"]),
        "skipped": summary.get("skipped"),
        "fingerprints": summary.get("fingerprints") or [],
    }
    return result


def per_thousand_documents(cost: float | None, questions: int,
                           questions_per_document: float) -> float | None:
    """What answering one question per document would cost for 1,000 documents.

    Defined rather than implied: the corpus has a different number of questions
    per document, so the figure is measured per question and scaled by the mean
    question count of the documents actually measured.
    """
    if cost is None or not questions or not questions_per_document:
        return None
    return round(cost / questions * questions_per_document * 1000, 2)


# ------------------------------------------------------------------ the run

def skip_reason(entry: dict, gold: dict | None) -> str | None:
    """Why a document cannot join the measurement, or None when it can."""
    if gold is None:
        return "no gold file: nothing verified to ask"
    if not gold.get("readable"):
        return "unreadable by design (image-only): no answer spans"
    if not answer_items(gold):
        return "gold carries verbatim spans only: no questions to ask"
    state = fetch.status(entry)["state"]
    if state != "verified":
        return f"document is {state}, not verified (run `python bench/fetch.py`)"
    return None


def run(model: AnswerModel, prices: dict, mode: str = PROMPT_MODE,
        document_ids=None, progress=None) -> dict:
    """Measure both text variants on every document that has answer gold."""
    corpus = fetch.load_corpus()
    wanted = set(document_ids or [])
    unknown = wanted - {entry["id"] for entry in corpus["documents"]}
    if unknown:
        raise AnswerError(f"unknown document id(s): {', '.join(sorted(unknown))}")

    price = price_for(prices, model.model)
    context_window = (price or {}).get("context_window")

    documents, skipped = [], []
    for entry in corpus["documents"]:
        if wanted and entry["id"] not in wanted:
            continue
        gold = goldlib.load(entry["id"])
        reason = skip_reason(entry, gold)
        if reason:
            if gold is not None or wanted:
                skipped.append({"id": entry["id"], "reason": reason})
            continue
        if progress:
            progress(entry)

        items = answer_items(gold)
        clean = ADAPTERS["quantagen"].extract(fetch.source_path(entry))
        raw = reference_text(fetch.source_path(entry))
        documents.append({
            "id": entry["id"],
            "category": entry["category"],
            "questions": len(items),
            "raw": summarise(evaluate(raw, items, model, mode, context_window), price),
            "clean": summarise(evaluate(clean, items, model, mode, context_window), price),
        })

    if not documents:
        raise AnswerError("no documents with answer gold are available; "
                          "run `python bench/fetch.py` and check bench/gold/")

    questions = sum(document["questions"] for document in documents)
    per_document = questions / len(documents)
    variants = {}
    for name in ("raw", "clean"):
        total_cost = sum(document[name]["cost_usd"] or 0.0
                         for document in documents)
        priced = all(document[name]["cost_usd"] is not None for document in documents)
        correct = sum(document[name]["correct"] for document in documents)
        questions_measured = sum(document[name]["questions"] for document in documents)
        variants[name] = {
            "questions": questions_measured,
            "correct": correct,
            "accuracy_pct": (round(100.0 * correct / questions_measured, 1)
                             if questions_measured else None),
            "input_tokens": sum(document[name]["input_tokens"] for document in documents),
            "output_tokens": sum(document[name]["output_tokens"] for document in documents),
            "cost_usd": round(total_cost, 6) if priced else None,
            "cost_per_1k_documents_usd": per_thousand_documents(
                total_cost if priced else None, questions_measured, per_document),
            "wrong": [f"{document['id']}/{span}"
                      for document in documents for span in document[name]["wrong"]],
            "not_found": sum(document[name]["not_found"] for document in documents),
        }

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model": {
            "name": model.model,
            "base_url": model.base_url,
            "temperature": model.temperature,
            "max_output_tokens": model.max_output_tokens,
            "seed": model.seed,
            "requests": model.requests,
            "system_fingerprints": sorted({
                fingerprint
                for document in documents
                for name in ("raw", "clean")
                for fingerprint in document[name]["fingerprints"]
            }),
        },
        "mode": mode,
        "prices": {"source": "eval/models.json", "checked": prices["prices_checked"],
                   "input_per_million_usd": (price or {}).get("input_per_million_usd"),
                   "output_per_million_usd": (price or {}).get("output_per_million_usd")},
        "questions_per_document": round(per_document, 2),
        "variants": variants,
        "documents": documents,
        "skipped": skipped,
        "rubric": rubric.snapshot(),
    }


def check(results: dict) -> list:
    """Failures to report, in the same shape the other eval --check modes use."""
    raw, clean = results["variants"]["raw"], results["variants"]["clean"]
    if raw["accuracy_pct"] is None or clean["accuracy_pct"] is None:
        return ["no questions were measured"]
    failures = []
    drop = round(raw["accuracy_pct"] - clean["accuracy_pct"], 1)
    if drop > ACCURACY_TOLERANCE:
        failures.append(
            f"cleaning loses answers: raw {raw['accuracy_pct']}% vs clean "
            f"{clean['accuracy_pct']}% is a {drop}-point drop (tolerance "
            f"{ACCURACY_TOLERANCE})")
    return failures


# ------------------------------------------------------------------ CLI

def _print_results(results: dict) -> None:
    price = results["prices"]
    model = results["model"]
    print(f"\n--- Answers: {model['name']}, {results['mode']} prompts, "
          f"{results['questions_per_document']} questions/document ---")
    if price["input_per_million_usd"] is None:
        print("  (no price for this model in eval/models.json: cost is not reported)")
    else:
        print(f"  priced at ${price['input_per_million_usd']}/1M in, "
              f"${price['output_per_million_usd']}/1M out "
              f"(checked {price['checked']})")
    print(f"  {'variant':<8}{'questions':>10}{'correct':>9}{'accuracy':>10}"
          f"{'in tok':>9}{'out tok':>9}{'cost':>10}{'$/1k docs':>11}")
    for name in ("raw", "clean"):
        data = results["variants"][name]
        cost = "-" if data["cost_usd"] is None else f"${data['cost_usd']:.4f}"
        per = "-" if data["cost_per_1k_documents_usd"] is None \
            else f"${data['cost_per_1k_documents_usd']:,.2f}"
        print(f"  {name:<8}{data['questions']:>10}{data['correct']:>9}"
              f"{(data['accuracy_pct'] or 0):>9.1f}%{data['input_tokens']:>9}"
              f"{data['output_tokens']:>9}{cost:>10}{per:>11}")

    raw, clean = results["variants"]["raw"], results["variants"]["clean"]
    lost = sorted(set(raw["wrong"]) & set(clean["wrong"]))
    if lost:
        print(f"  wrong in both variants ({len(lost)}): {', '.join(lost)}")
    regressed = sorted(set(raw["wrong"]) - set(clean["wrong"]))
    if regressed:
        print(f"  answered from raw but not from clean: {', '.join(regressed)}")
    gained = sorted(set(clean["wrong"]) - set(raw["wrong"]))
    if gained:
        print(f"  answered from clean but not from raw: {', '.join(gained)}")
    if clean["not_found"]:
        print(f"  {clean['not_found']} reply/replies said NOT FOUND")
    for skip in results["skipped"]:
        print(f"  skipped {skip['id']}: {skip['reason']}")


def _dry_run(model: AnswerModel, mode: str, document_ids=None) -> int:
    """Build every prompt and price it, without sending anything.

    The point is to know what a run costs before it is paid for, and to prove
    offline that the prompts assemble: a harness whose first execution is a
    billed one is a harness nobody can review.
    """
    corpus = fetch.load_corpus()
    wanted = set(document_ids or [])
    price = price_for(load_prices(), model.model)
    if price is None:
        print(f"no price for {model.model} in eval/models.json: the cost of this "
              "run cannot be projected", file=sys.stderr)
        return 2

    total_questions = total_input = total_context = 0
    for entry in corpus["documents"]:
        if wanted and entry["id"] not in wanted:
            continue
        gold = goldlib.load(entry["id"])
        if skip_reason(entry, gold):
            continue
        items = answer_items(gold)
        clean = ADAPTERS["quantagen"].extract(fetch.source_path(entry))
        raw = reference_text(fetch.source_path(entry))
        for name, text in (("raw", raw), ("clean", clean)):
            size = get_token_count(text)
            if mode == "full" and price.get("context_window") \
                    and size + MAX_OUTPUT_TOKENS > price["context_window"]:
                print(f"  {entry['id']:<24}{name:<8} skipped: {size} tokens "
                      f"exceed the {price['context_window']}-token window")
                continue
            contexts = [context_for(text, item["question"], mode) for item in items]
            estimate = sum(get_token_count(context) for context in contexts)
            # Counted with the rubric's tiktoken model, and the system prompt and
            # question are small next to the context they are charged with.
            total_input += estimate + len(contexts) * 40
            total_context += estimate
            total_questions += len(contexts)
            print(f"  {entry['id']:<24}{name:<8} {len(contexts):>3} questions  "
                  f"{estimate:>7} context tokens  {size:>8} document tokens")

    projected = cost_usd(total_input, total_questions * MAX_OUTPUT_TOKENS, price)
    print(f"\n{total_questions} requests, {total_input} input tokens "
          f"({total_context} as context), at most {total_questions * MAX_OUTPUT_TOKENS} "
          f"output tokens")
    print(f"projected worst case: ${projected:.4f} at "
          f"${price['input_per_million_usd']}/1M in, "
          f"${price['output_per_million_usd']}/1M out")
    print("nothing was sent: --dry-run assembles prompts only")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Measure how well a model answers curated questions from raw vs cleaned text.")
    parser.add_argument("--model", default=DEFAULT_MODEL,
                        help="Model id, priced in eval/models.json.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL,
                        help="OpenAI-compatible endpoint, without /chat/completions.")
    parser.add_argument("--api-key-env", default="OPENAI_API_KEY",
                        help="Environment variable holding the API key.")
    parser.add_argument("--mode", choices=("rag", "full"), default=PROMPT_MODE,
                        help="'rag' shows the top-k retrieved chunks, 'full' the whole document.")
    parser.add_argument("--documents", action="append", default=[], metavar="ID",
                        help="Restrict the run to these document ids.")
    parser.add_argument("--out", default=os.path.join("outputs", "answers.json"))
    parser.add_argument("--cache", default=os.path.join("outputs", "answers-cache.json"),
                        help="Replies are cached here; reruns cost nothing.")
    parser.add_argument("--refresh", action="store_true",
                        help="Ignore the cache and pay for the questions again.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Assemble and price every prompt; send nothing.")
    parser.add_argument("--check", action="store_true",
                        help="Exit non-zero when cleaning loses answers.")
    args = parser.parse_args(argv)

    try:
        prices = load_prices()
    except AnswerError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    model = AnswerModel(
        model=args.model,
        base_url=args.base_url,
        api_key=None if args.dry_run else os.environ.get(args.api_key_env),
        cache_path=None if args.refresh else args.cache,
    )

    try:
        if args.dry_run:
            return _dry_run(model, args.mode, args.documents)

        if not model.api_key:
            print(f"{args.api_key_env} is not set: set it, or run --dry-run to "
                  "project the cost without calling the model", file=sys.stderr)
            return 2

        results = run(model, prices, args.mode, args.documents)
    except AnswerError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    _print_results(results)

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(results, handle, indent=2, ensure_ascii=False)
    print(f"\nAnswer results written to {args.out} "
          f"({len(results['documents'])} document(s), {model.requests} request(s)).")

    failures = check(results) if args.check else []
    if failures:
        print("\nAnswer-accuracy floors failed:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
