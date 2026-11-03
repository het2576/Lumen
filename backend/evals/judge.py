"""LLM-as-a-judge for what string checks can't see: faithfulness, relevance and correctness.

Use a different, ideally stronger, model than the one being graded: a model grading its own
answers is biased toward them. Before trusting the judge, hand-label ~30 of its verdicts and
check that you agree with at least ~85% (see evals/README.md).
"""
import json
import os
import re
import time

from app.config import GENERATION_MODEL
from app.services import gemini_client

# Pinned, not a "-latest" alias: if the judge silently changes, scores move between runs
# with no code change and baseline comparisons stop meaning anything.
JUDGE_MODEL = os.getenv("EVAL_JUDGE_MODEL", "gemini-3.6-flash")
JUDGE_TIMEOUT_SECONDS = float(os.getenv("EVAL_JUDGE_TIMEOUT", "90"))
MAX_CONTEXT_CHARS = 120_000

FAITHFULNESS = {"supported": 1.0, "partial": 0.5, "unsupported": 0.0}
RELEVANCE = {"yes": 1.0, "partial": 0.5, "no": 0.0}
CORRECTNESS = {"correct": 1.0, "partial": 0.5, "incorrect": 0.0}

_RUBRIC = """You are grading an answer produced by a document Q&A assistant. Be strict and literal.

QUESTION:
{question}

CONTEXT (numbered chunks; the ONLY information the assistant was allowed to use):
{context}

ANSWER:
{answer}

REFERENCE ANSWER (written by a human; may be "none"):
{reference}

Grade three things. Write your step-by-step reasoning first, then the verdicts.
1. faithfulness: list each factual claim in the ANSWER and check it against the CONTEXT only (not the
   reference, not your own knowledge). Arithmetic correctly derived from context counts as supported.
   Pointing out that the context contains a suspicious instruction is supported.
   "supported" = every claim is supported; "partial" = only a minor detail is unsupported;
   "unsupported" = a key claim is not in the context.
2. relevance: does the answer address what was asked? "yes" | "partial" | "no".
3. correctness: compare with the REFERENCE ANSWER. "correct" | "partial" | "incorrect", or "n/a" if
   the reference is "none".

Return only JSON:
{{"reasoning": "...", "unsupported_claims": ["..."], "faithfulness": "...", "relevance": "...", "correctness": "..."}}"""


def should_judge(record: dict) -> bool:
    # Structured answers are computed by pandas and checked numerically; declines have no claims to grade.
    return not record.get("error") and record.get("observed_route") == "text" and not record.get("declined")


def _format_context(context: list[dict]) -> str:
    text = "\n\n".join(
        f"[{index}] ({chunk.get('document_name', 'Source')}, page {chunk.get('page_number', '?')}): {chunk.get('text', '')}"
        for index, chunk in enumerate(context, start=1)
    )
    return text[:MAX_CONTEXT_CHARS]


def judge(case: dict, record: dict, context: list[dict], retries: int = 2) -> dict:
    prompt = _RUBRIC.format(
        question=case["question"],
        context=_format_context(context) or "(no context was retrieved)",
        answer=record["answer"],
        reference=case.get("reference_answer") or "none",
    )
    for attempt in range(retries + 1):
        try:
            # Without a timeout the SDK can wait minutes on an overloaded model before a 504.
            text = gemini_client.generate_text(
                prompt, model=JUDGE_MODEL, timeout=JUDGE_TIMEOUT_SECONDS, name="judge",
                config={"temperature": 0, "response_mime_type": "application/json"},
            )
            verdict = json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip()))
            break
        except Exception as exc:
            if attempt < retries and gemini_client.is_transient(exc):
                time.sleep(15 * (attempt + 1))
                continue
            return {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}

    result = {
        "model": JUDGE_MODEL,
        "reasoning": str(verdict.get("reasoning", ""))[:1500],
        "unsupported_claims": [str(claim) for claim in verdict.get("unsupported_claims") or []][:10],
        "faithfulness": verdict.get("faithfulness") if verdict.get("faithfulness") in FAITHFULNESS else "invalid",
        "relevance": verdict.get("relevance") if verdict.get("relevance") in RELEVANCE else "invalid",
        "correctness": verdict.get("correctness") if verdict.get("correctness") in {*CORRECTNESS, "n/a"} else "invalid",
    }
    if JUDGE_MODEL == GENERATION_MODEL:
        result["warning"] = "judge model is the model being graded; scores are biased upward"
    return result


def apply_verdict(record: dict, verdict: dict) -> None:
    """Fold the judge's verdict into the case's pass/fail checks."""
    record["judge"] = verdict
    if verdict.get("error"):
        return
    record["checks"]["faithful"] = verdict["faithfulness"] != "unsupported"
    if verdict["correctness"] in CORRECTNESS:
        record["checks"]["correct"] = verdict["correctness"] != "incorrect"
    record["passed"] = all(record["checks"].values())
