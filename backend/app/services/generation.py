import re
from typing import Iterator

from app import tracing
from app.config import TOP_K
from app.services import gemini_client

NO_ANSWER_PHRASE = "I don't know based on the provided document."

SYSTEM_INSTRUCTIONS = f"""You are a document Q&A and drafting assistant. Ground every answer in the numbered \
context chunks below — never introduce facts, numbers, or claims that aren't in the context or clearly \
inferable from it.

Rules:
- If the question asks you to explain, summarize, complete, or draft something (e.g. write an email, a reply, \
a message) based on the document, do so using the context — synthesizing and organizing what's there is \
expected, not just quoting it verbatim.
- Broad questions ("what is this about", "key points", "what should I pay attention to", "risks", "a briefing") \
are always answerable: synthesize them from the whole context.
- Cite the chunk number(s) you drew on inline like [1] or [2][3].
- Only when the question asks for specific information the context genuinely lacks, reply with exactly: \
"{NO_ANSWER_PHRASE}"
- Use the prior conversation to understand what's being asked (follow-ups, "that", "the email", etc.) and to \
build on earlier grounded answers in this same conversation — it's part of the same grounded context, not \
outside knowledge.

Charts:
- When the answer involves several comparable numbers from the context (figures, prices, quantities, scores, \
percentages, values over time), or the user asks for a chart, graph, plot, or trend, include one chart block \
right after the relevant paragraph, in exactly this format:

```chart
{{"type":"bar","title":"Chart title","subtitle":"optional","data":[{{"label":"Name","value":123,"formattedValue":"₹123"}}]}}
```

- Use "bar" for comparisons, "line" for values over time, "donut" for shares of a whole.
- Every value must come from the context — never estimate or invent numbers. "value" is a plain JSON number; \
put the original unit or currency symbol in "formattedValue".
- Skip the chart for a single value or a text-only answer. If a chart is requested but the context has no \
suitable numbers, say so plainly instead of drawing one."""


def build_prompt(question: str, chunks: list[dict], chat_history: list[dict]) -> str:
    context_block = "\n\n".join(
        f"[{i + 1}] ({c.get('document_name', 'Source')}, page {c.get('page_number', '?')}): {c['text']}"
        for i, c in enumerate(chunks)
    )

    history_block = ""
    if chat_history:
        turns = "\n".join(f"{m['role']}: {m['content']}" for m in chat_history)
        history_block = f"\n\nPrior conversation:\n{turns}"

    return (
        f"{SYSTEM_INSTRUCTIONS}\n\n"
        f"Context chunks:\n{context_block}"
        f"{history_block}\n\n"
        f"Question: {question}\n\n"
        f"Answer:"
    )


def stream_answer(question: str, chunks: list[dict], chat_history: list[dict],
                  trace: tracing.Observation = tracing.NOOP) -> Iterator[str]:
    """Yield the grounded answer as Gemini writes it. Join the pieces and pass them to
    finalize_answer for citations."""
    if not chunks:
        return
    yield from gemini_client.stream_text(build_prompt(question, chunks, chat_history), parent=trace, name="answer")


def finalize_answer(answer: str, chunks: list[dict]) -> dict:
    answer = answer.strip() or NO_ANSWER_PHRASE

    cited_numbers = {int(n) for n in re.findall(r"\[(\d+)\]", answer)}
    cited_chunk_ids = [
        chunks[n - 1]["chunk_id"] for n in cited_numbers if 0 < n <= len(chunks)
    ]

    if not cited_chunk_ids and answer != NO_ANSWER_PHRASE:
        # Whole-document context can hold dozens of chunks; show only the closest matches.
        closest = sorted(chunks, key=lambda c: c.get("similarity_score", 0.0), reverse=True)[:TOP_K]
        cited_chunk_ids = [c["chunk_id"] for c in closest]

    return {"answer": answer, "cited_chunk_ids": cited_chunk_ids}


def generate_answer(question: str, chunks: list[dict], chat_history: list[dict],
                    trace: tracing.Observation = tracing.NOOP) -> dict:
    """Non-streaming convenience wrapper around the same prompt and citation logic."""
    return finalize_answer("".join(stream_answer(question, chunks, chat_history, trace)), chunks)
