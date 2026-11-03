"""Deterministic per-case scoring. No LLM involved, so these checks are free and repeatable."""
import re

import tiktoken

from app.services.generation import NO_ANSWER_PHRASE
from evals.spies import Recorder

_encoder = tiktoken.get_encoding("cl100k_base")

_CITATION = re.compile(r"\[(\d+)\]")
_NUMBER = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
# Phrases that signal the answer is declining rather than answering. The exact refusal
# phrase is tracked separately because the code compares against it verbatim (guide F11).
_DECLINE_PATTERNS = (
    "not mentioned", "isn't mentioned", "does not mention", "doesn't mention", "not specified",
    "does not specify", "doesn't specify", "no information", "not provided", "does not provide",
    "doesn't provide", "not included", "not stated", "does not state", "doesn't state",
    "not covered", "does not contain", "doesn't contain", "don't know", "do not know",
    "couldn't find", "could not find", "cannot find", "unable to find", "couldn't match",
    "not available in", "no verified", "there is no", "does not include", "doesn't include",
)
# A long answer that mentions one gap ("a deposit isn't specified for desks") is still an answer.
_SHORT_DECLINE_CHARS = 280


def normalize(text: str) -> str:
    return text.lower().replace("’", "'").replace("‘", "'")


def _matches(answer: str, item: str | list[str]) -> bool:
    alternatives = [item] if isinstance(item, str) else item
    return any(normalize(alternative) in answer for alternative in alternatives)


def numbers_in(text: str) -> list[float]:
    values = []
    for raw in _NUMBER.findall(text):
        try:
            values.append(float(raw.replace(",", "")))
        except ValueError:
            continue
    return values


def retrieval_metrics(case: dict, context: list[dict], key_by_id: dict[str, str]) -> dict:
    """Rank-based retrieval quality, labelled by (document, page) so labels survive re-chunking."""
    expected = case.get("expected_pages")
    if not expected or not context:
        return {}
    relevant = {(doc, page) for doc, pages in expected.items() for page in pages}
    located = [(key_by_id.get(chunk.get("document_id")), chunk.get("page_number")) for chunk in context]
    ranked = [
        location for _, location in sorted(
            zip(context, located), key=lambda pair: pair[0].get("similarity_score", 0.0), reverse=True
        )
    ]
    flags = [location in relevant for location in ranked]
    first = next((index for index, hit in enumerate(flags) if hit), None)
    present = set(located)
    expected_docs = {doc for doc, _ in relevant}
    return {
        "hit@1": any(flags[:1]),
        "hit@3": any(flags[:3]),
        "hit@5": any(flags[:5]),
        "mrr": 0.0 if first is None else 1.0 / (first + 1),
        # Did the model see the relevant pages at all? (Always 1.0 in whole-document mode.)
        "context_recall": len(relevant & present) / len(relevant),
        # Multi-doc: did every expected document get at least one chunk into the context?
        "doc_coverage": len(expected_docs & {doc for doc, _ in present}) / len(expected_docs),
    }


def score_case(case: dict, response, recorder: Recorder, key_by_id: dict[str, str], total_ms: float) -> dict:
    answer = response.answer or ""
    text = normalize(answer)
    strict_refusal = normalize(NO_ANSWER_PHRASE).rstrip(".") in text
    declined = strict_refusal or any(pattern in text for pattern in _DECLINE_PATTERNS)
    observed_route = "structured" if response.verification == "verified" else "text"
    cited = sorted({int(number) for number in _CITATION.findall(answer)})

    checks: dict[str, bool] = {"route": case["expected_route"] in ("any", observed_route)}
    if case["answerable"]:
        checks["no_false_refusal"] = not (strict_refusal or (declined and len(answer) < _SHORT_DECLINE_CHARS))
    else:
        checks["declined"] = declined
    if case.get("must_include"):
        checks["must_include"] = all(_matches(text, item) for item in case["must_include"])
    if case.get("must_not_include"):
        checks["must_not_include"] = not any(_matches(text, item) for item in case["must_not_include"])
    if case.get("expected_number") is not None:
        expected = float(case["expected_number"])
        tolerance = max(abs(expected) * 0.005, 0.01)
        checks["number"] = any(abs(value - expected) <= tolerance for value in numbers_in(answer))
    if case.get("expect_chart"):
        checks["chart"] = response.chart is not None or "```chart" in answer

    tokens = {"input": 0, "output": 0, "cached": 0}
    for entry in recorder.usage:
        for key in tokens:
            tokens[key] += entry[key]
    staged = sum(recorder.timings_ms.get(name, 0.0) for name in ("db", "retrieve", "generate", "structured_query", "route"))

    return {
        "id": case["id"],
        "category": case["category"],
        "question": case["question"],
        "answerable": case["answerable"],
        "answer": answer,
        "verification": response.verification,
        "expected_route": case["expected_route"],
        "observed_route": observed_route,
        "router_triggered": bool(recorder.tabular_detected),
        "strict_refusal": strict_refusal,
        "declined": declined,
        "cited": cited,
        "uncited": observed_route == "text" and case["answerable"] and not declined and not cited,
        "sources": [
            {"doc": key_by_id.get(source.document_id), "page": source.page_number, "score": round(source.similarity_score, 4)}
            for source in response.sources
        ],
        "checks": checks,
        "passed": all(checks.values()),
        "retrieval": retrieval_metrics(case, recorder.context, key_by_id) if observed_route == "text" else {},
        "context_chunks": len(recorder.context),
        "context_tokens": sum(len(_encoder.encode(chunk.get("text", ""))) for chunk in recorder.context),
        "latency_ms": {
            "total": round(total_ms),
            **{name: round(value) for name, value in recorder.timings_ms.items()},
            "other": round(max(total_ms - staged, 0.0)),
            "first_token": round(recorder.first_token_ms if recorder.first_token_ms is not None else total_ms),
        },
        "calls": dict(recorder.calls),
        "tokens": tokens,
        "llm_calls": [dict(entry) for entry in recorder.usage],
        "error": None,
    }


def error_record(case: dict, exc: Exception) -> dict:
    detail = getattr(exc, "detail", None) or str(exc)
    return {
        "id": case["id"], "category": case["category"], "question": case["question"],
        "answerable": case["answerable"], "answer": "", "checks": {}, "passed": False,
        "retrieval": {}, "latency_ms": {}, "tokens": {"input": 0, "output": 0, "cached": 0},
        "error": f"{type(exc).__name__}: {str(detail)[:300]}",
    }
