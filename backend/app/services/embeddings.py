"""Batched, quota-paced embedding calls shared by ingestion and retrieval.

The free Gemini tier enforces a per-minute token ceiling. Pacing against a
rolling budget keeps ingestion predictable instead of letting it stumble into
429 storms, where each rejection costs a multi-second backoff.
"""

import logging
import re
import threading
import time
from collections import deque

import tiktoken

from app.config import (
    EMBED_BATCH_TOKENS,
    EMBED_TPM_BUDGET,
    EMBEDDING_DIM,
    EMBEDDING_MODEL,
)
from app.services.gemini_client import get_genai

logger = logging.getLogger(__name__)

_encoder = tiktoken.get_encoding("cl100k_base")

# gemini-embedding-001 rejects any single input longer than this.
MAX_INPUT_TOKENS = 2048


class _TokenBudget:
    """Rolling 60-second token allowance shared by every embedding caller."""

    def __init__(self, tokens_per_minute: int) -> None:
        self._budget = tokens_per_minute
        self._spent: deque[tuple[float, int]] = deque()
        self._lock = threading.Lock()

    def reserve(self, tokens: int) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                while self._spent and now - self._spent[0][0] >= 60.0:
                    self._spent.popleft()
                used = sum(amount for _, amount in self._spent)
                # An empty window always admits the request, so a batch larger
                # than the whole budget still makes progress instead of hanging.
                if not self._spent or used + tokens <= self._budget:
                    self._spent.append((now, tokens))
                    return
                # Wait only until enough of the oldest reservations age out,
                # rather than for the whole window to drain.
                needed = used + tokens - self._budget
                freed = 0
                wait = 0.0
                for timestamp, amount in self._spent:
                    freed += amount
                    wait = 60.0 - (now - timestamp)
                    if freed >= needed:
                        break
            logger.info("Embedding budget reached — pacing %.1fs", wait)
            time.sleep(max(wait, 0.1))


_budget = _TokenBudget(EMBED_TPM_BUDGET)


def _count(text: str) -> int:
    return len(_encoder.encode(text))


def _truncate(text: str) -> str:
    tokens = _encoder.encode(text)
    if len(tokens) <= MAX_INPUT_TOKENS:
        return text
    return _encoder.decode(tokens[:MAX_INPUT_TOKENS])


def _build_batches(texts: list[str]) -> list[tuple[list[str], int]]:
    batches: list[tuple[list[str], int]] = []
    current: list[str] = []
    current_tokens = 0
    for text in texts:
        tokens = _count(text)
        if current and current_tokens + tokens > EMBED_BATCH_TOKENS:
            batches.append((current, current_tokens))
            current, current_tokens = [], 0
        current.append(text)
        current_tokens += tokens
    if current:
        batches.append((current, current_tokens))
    return batches


def _embed_batch(batch: list[str], tokens: int, task_type: str) -> list[list[float]]:
    genai = get_genai()
    for attempt in range(5):
        _budget.reserve(tokens)
        try:
            result = genai.embed_content(
                model=EMBEDDING_MODEL,
                content=batch,
                task_type=task_type,
                output_dimensionality=EMBEDDING_DIM,
            )
            vectors = result["embedding"]
            # A one-item batch can come back as a bare vector rather than nested.
            return [vectors] if vectors and isinstance(vectors[0], float) else vectors
        except Exception as exc:
            message = str(exc)
            throttled = "429" in message or "quota" in message.lower() or "resourceexhausted" in message.lower()
            if not throttled or attempt == 4:
                raise
            wait = 8.0 + attempt * 6.0
            match = re.search(r"retry in ([\d.]+)s", message)
            if match:
                wait = max(float(match.group(1)) + 1.0, wait)
            logger.warning("Embedding throttled — retrying in %.1fs (attempt %d)", wait, attempt + 1)
            time.sleep(wait)
    raise RuntimeError("Embedding failed after repeated rate limiting.")


def embed_texts(texts: list[str], *, task_type: str) -> list[list[float]]:
    """Embed many texts using as few API round trips as the quota allows."""
    if not texts:
        return []

    prepared = [_truncate(text) for text in texts]
    batches = _build_batches(prepared)
    vectors: list[list[float]] = []

    started = time.monotonic()
    for index, (batch, tokens) in enumerate(batches, start=1):
        vectors.extend(_embed_batch(batch, tokens, task_type))
        if len(batches) > 1:
            logger.info(
                "Embedded batch %d/%d (%d texts, %d tokens) — %d/%d done in %.1fs",
                index, len(batches), len(batch), tokens, len(vectors), len(prepared), time.monotonic() - started,
            )

    if len(vectors) != len(prepared):
        raise RuntimeError(f"Embedding returned {len(vectors)} vectors for {len(prepared)} texts.")
    return vectors
