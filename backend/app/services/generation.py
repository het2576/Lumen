import re

from app.config import GENERATION_MODEL
from app.services.gemini_client import get_genai

NO_ANSWER_PHRASE = "I don't know based on the provided document."

SYSTEM_INSTRUCTIONS = f"""You are a document Q&A and drafting assistant. Ground every answer in the numbered \
context chunks below — never introduce facts, numbers, or claims that aren't in the context or clearly \
inferable from it.

Rules:
- If the question asks you to explain, summarize, complete, or draft something (e.g. write an email, a reply, \
a message) based on the document, do so using the context — synthesizing and organizing what's there is \
expected, not just quoting it verbatim.
- Cite the chunk number(s) you drew on inline like [1] or [2][3].
- If the context truly doesn't contain or support what's being asked, reply with exactly: "{NO_ANSWER_PHRASE}"
- Use the prior conversation to understand what's being asked (follow-ups, "that", "the email", etc.) and to \
build on earlier grounded answers in this same conversation — it's part of the same grounded context, not \
outside knowledge."""


def build_prompt(question: str, chunks: list[dict], chat_history: list[dict]) -> str:
    context_block = "\n\n".join(
        f"[{i + 1}] (page {c.get('page_number', '?')}): {c['text']}" for i, c in enumerate(chunks)
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


def generate_answer(question: str, chunks: list[dict], chat_history: list[dict]) -> dict:
    if not chunks:
        return {"answer": NO_ANSWER_PHRASE, "cited_chunk_ids": []}

    genai = get_genai()
    model = genai.GenerativeModel(GENERATION_MODEL)
    prompt = build_prompt(question, chunks, chat_history)

    response = model.generate_content(prompt)
    answer = (response.text or "").strip() or NO_ANSWER_PHRASE

    cited_numbers = {int(n) for n in re.findall(r"\[(\d+)\]", answer)}
    cited_chunk_ids = [
        chunks[n - 1]["chunk_id"] for n in cited_numbers if 0 < n <= len(chunks)
    ]

    if not cited_chunk_ids and answer != NO_ANSWER_PHRASE:
        cited_chunk_ids = [c["chunk_id"] for c in chunks]

    return {"answer": answer, "cited_chunk_ids": cited_chunk_ids}
