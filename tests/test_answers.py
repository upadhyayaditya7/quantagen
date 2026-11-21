"""The answer harness, tested offline: no key, no network, no PDFs.

The numbers this module produces cost money, so the parts that decide them --
prompt assembly, grading, the cache, the cost arithmetic and the accuracy floor
-- are pinned here against a stub transport. A test that needed an API key
would never run in CI, and an untested harness is a bill with a table attached.
"""

import json

import pytest

from eval import answers


ITEMS = [
    {"id": "steps", "question": "How many steps were the big models trained for?",
     "answer": "300,000"},
    {"id": "hardware", "question": "What hardware were the models trained on?",
     "answer": "8 NVIDIA P100 GPUs"},
]

PRICE = {"input_per_million_usd": 0.15, "output_per_million_usd": 0.6,
         "context_window": 128000}


class Transport:
    """Canned chat-completions responses, and a record of what was sent."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, url, headers, payload, timeout):
        self.calls.append(payload)
        content = self.replies.pop(0) if self.replies else "NOT FOUND"
        return {
            "choices": [{"message": {"content": content}}],
            "usage": {"prompt_tokens": 1000, "completion_tokens": 10},
            "system_fingerprint": "fp_test",
            "model": payload["model"],
        }


def _model(transport, **kwargs):
    return answers.AnswerModel(api_key="test-key", transport=transport, **kwargs)


# ------------------------------------------------------------------ grading
def test_a_number_is_graded_as_a_value_not_a_substring():
    assert answers.grade("300,000", "The big models trained for 300,000 steps.")
    # The substring test the retention audit used to have would pass this.
    assert not answers.grade("300,000", "The big models trained for 30,000 steps.")


def test_a_reply_is_graded_case_insensitively():
    assert answers.grade("8 NVIDIA P100 GPUs", "8 nvidia p100 gpus.")


def test_not_found_is_not_an_answer():
    assert not answers.grade("300,000", answers.NOT_FOUND)


# ------------------------------------------------------------------ the prompt
def test_the_prompt_carries_the_question_and_the_answer_contract():
    messages = answers.messages_for("How many steps?", "the document")
    assert messages[0]["role"] == "system"
    assert answers.NOT_FOUND in messages[0]["content"]
    assert "How many steps?" in messages[1]["content"]
    assert "the document" in messages[1]["content"]


def test_rag_context_is_the_top_k_chunks_and_keeps_the_answer():
    filler = " ".join(f"word{index}" for index in range(400))
    text = filler + " The big models were trained for 300,000 steps in total."
    context = answers.context_for(text, ITEMS[0]["question"], "rag")
    assert "300,000" in context
    # Never more than the rubric's top-k chunks, so the comparison prices the
    # same amount of context for both variants.
    assert len(context.split()) <= answers.TOP_K * 120


def test_an_unknown_prompt_mode_is_an_error_not_a_default():
    with pytest.raises(answers.AnswerError, match="prompt mode"):
        answers.context_for("text", "question", "whole")


# ------------------------------------------------------------------ the model
def test_a_call_without_a_key_fails_before_anything_is_sent():
    transport = Transport("unused")
    model = answers.AnswerModel(transport=transport, api_key=None)
    with pytest.raises(answers.AnswerError, match="OPENAI_API_KEY"):
        model.ask([{"role": "user", "content": "x"}])
    assert transport.calls == []


def test_an_identical_request_is_answered_from_the_cache(tmp_path):
    cache = str(tmp_path / "answers-cache.json")
    transport = Transport("300,000 steps", "8 NVIDIA P100 GPUs")
    first = answers.evaluate("text", ITEMS, _model(transport, cache_path=cache))
    second = answers.evaluate("text", ITEMS, _model(Transport(), cache_path=cache))

    assert len(transport.calls) == 2  # the second run sent nothing
    assert first["correct"] == second["correct"] == 2


def test_a_different_model_is_a_different_request(tmp_path):
    cache = str(tmp_path / "answers-cache.json")
    transport = Transport("300,000 steps", "8 NVIDIA P100 GPUs",
                          "300,000 steps", "8 NVIDIA P100 GPUs")
    answers.evaluate("text", ITEMS, _model(transport, cache_path=cache))
    answers.evaluate("text", ITEMS,
                     _model(transport, cache_path=cache, model="gpt-5-nano"))
    assert len(transport.calls) == 4


def test_a_reply_without_usage_is_measured_but_flagged_as_estimated():
    payload = {"choices": [{"message": {"content": "300,000 steps"}}]}
    record = answers.AnswerModel(api_key="k", transport=lambda *a: payload)._record(payload)
    assert record["estimated_tokens"] is True
    assert record["input_tokens"] > 0


# ------------------------------------------------------------------ the cost
def test_cost_is_price_times_measured_tokens():
    assert answers.cost_usd(1_000_000, 0, PRICE) == pytest.approx(0.15)
    assert answers.cost_usd(0, 1_000_000, PRICE) == pytest.approx(0.6)
    assert answers.cost_usd(10, 10, None) is None


def test_cost_per_thousand_documents_scales_a_per_question_measurement():
    # Two questions cost $0.0002 together and a document has two questions, so
    # 1,000 documents is 1,000x the per-question cost.
    assert answers.per_thousand_documents(0.0002, 2, 2) == pytest.approx(0.2, rel=1e-3)
    assert answers.per_thousand_documents(None, 2, 2) is None
    assert answers.per_thousand_documents(0.1, 0, 2) is None


def test_a_wrong_answer_is_named_and_priced_in_the_summary():
    transport = Transport("300,000 steps", "NOT FOUND")
    summary = answers.summarise(
        answers.evaluate("text", ITEMS, _model(transport)), PRICE)

    assert summary["questions"] == 2 and summary["correct"] == 1
    assert summary["accuracy_pct"] == 50.0
    assert summary["wrong"] == ["hardware"]
    assert summary["not_found"] == 1
    assert summary["cost_usd"] == pytest.approx((2000 * 0.15 + 20 * 0.6) / 1_000_000)


# ------------------------------------------------------------------ the floor
def test_the_floor_flags_a_clean_text_that_loses_answers():
    results = {"variants": {"raw": {"accuracy_pct": 100.0},
                            "clean": {"accuracy_pct": 90.0}}}
    failures = answers.check(results)
    assert failures and "90.0" in failures[0]


def test_one_flipped_answer_is_within_the_tolerance():
    # One of 24 questions is 4.2 points; the floor must not gate on its luck.
    results = {"variants": {"raw": {"accuracy_pct": 100.0},
                            "clean": {"accuracy_pct": 95.8}}}
    assert answers.check(results) == []


# ------------------------------------------------------------------ the data
def test_the_shipped_price_table_prices_the_rubric_model():
    prices = answers.load_prices()
    entry = answers.price_for(prices, answers.DEFAULT_MODEL)
    assert entry is not None
    assert entry["input_per_million_usd"] > 0
    assert entry["context_window"] > 1000
    assert prices["prices_checked"]


def test_a_price_without_a_context_window_is_rejected(tmp_path):
    path = tmp_path / "models.json"
    path.write_text(json.dumps({
        "prices_checked": "2026-11-21",
        "models": {"x": {"input_per_million_usd": 1, "output_per_million_usd": 2}},
    }), encoding="utf-8")
    with pytest.raises(answers.AnswerError, match="context_window"):
        answers.load_prices(str(path))


def test_an_unpriced_model_is_none_rather_than_an_error():
    # Accuracy can still be measured for a model nobody has priced.
    assert answers.price_for(answers.load_prices(), "not-a-model") is None


def test_only_answer_spans_become_questions():
    gold = {"spans": [
        {"id": "a", "kind": "answer", "question": "q", "answer": "1"},
        {"id": "b", "kind": "verbatim", "quote": "a passage with no question"},
    ]}
    assert [item["id"] for item in answers.answer_items(gold)] == ["a"]
    assert answers.answer_items(None) == []


def test_full_mode_skips_a_document_that_does_not_fit_the_window():
    transport = Transport("300,000 steps")
    result = answers.evaluate("word " * 5000, ITEMS, _model(transport),
                              mode="full", context_window=1000)
    assert result["questions"] == 0
    assert "not truncated" in result["skipped"]
    assert transport.calls == []
