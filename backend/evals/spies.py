"""Record what the production /chat code path does, without changing it.

The harness calls the real chat() handler. chat.py looks up its collaborators
(_gather_context, generate_answer, db.*, …) by name at call time, so wrapping those names
lets every case run exactly the code users hit while we record per-stage timings, the
context sent to the model, routing decisions, time to first token and Gemini token usage.
"""
import functools
import threading
import time
from collections import defaultdict

from app import db
from app.routers import chat as chat_module
from app.services import gemini_client

_DB_FUNCTIONS = (
    "get_document", "get_documents", "get_conversation", "get_conversation_document_ids", "create_conversation",
    "get_conversation_history", "get_document_tables", "log_query", "save_turn",
)
_ORIGINAL_FULL_CONTEXT_MAX_CHUNKS = chat_module.FULL_CONTEXT_MAX_CHUNKS
CONTEXT_MODES = {
    "production": _ORIGINAL_FULL_CONTEXT_MAX_CHUNKS,
    "hybrid": -1,  # never send whole documents: always exercise top-k hybrid retrieval
    "whole": 10**9,  # always send whole documents
}


class Recorder:
    """Collects observations for one eval case. Thread-safe: multi-doc search runs in a pool."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stage = threading.local()
        self.active = False
        self.reset()

    def reset(self) -> None:
        self.timings_ms: dict[str, float] = defaultdict(float)
        self.calls: dict[str, int] = defaultdict(int)
        self.context: list[dict] = []
        self.tabular_detected: bool | None = None
        self.structured: dict | None = None
        self.generation: dict | None = None
        self.usage: list[dict] = []
        self.started_at: float | None = None
        self.first_token_ms: float | None = None

    def add_time(self, name: str, elapsed_ms: float) -> None:
        with self._lock:
            self.timings_ms[name] += elapsed_ms
            self.calls[name] += 1

    def add_usage(self, entry: dict) -> None:
        with self._lock:
            self.usage.append(entry)

    @property
    def stage(self) -> str:
        return getattr(self._stage, "name", None) or "other"

    @stage.setter
    def stage(self, name: str | None) -> None:
        self._stage.name = name


RECORDER = Recorder()
_installed = False


def _spy(name: str, fn, on_result=None):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        if not RECORDER.active:
            return fn(*args, **kwargs)
        previous = getattr(RECORDER._stage, "name", None)
        RECORDER.stage = name
        start = time.perf_counter()
        try:
            result = fn(*args, **kwargs)
        finally:
            RECORDER.add_time(name, (time.perf_counter() - start) * 1000)
            RECORDER.stage = previous
        if on_result:
            on_result(result)
        return result

    return wrapper


def _spy_stream(name: str, fn):
    """Time a generator over its whole iteration (not just its creation) and note when the
    first piece arrives, relative to the start of the request."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        if not RECORDER.active:
            yield from fn(*args, **kwargs)
            return
        start = time.perf_counter()
        previous = getattr(RECORDER._stage, "name", None)
        RECORDER.stage = name  # token usage is reported inside the generator's last step
        try:
            for piece in fn(*args, **kwargs):
                if RECORDER.first_token_ms is None and RECORDER.started_at is not None:
                    RECORDER.first_token_ms = (time.perf_counter() - RECORDER.started_at) * 1000
                yield piece
        finally:
            RECORDER.stage = previous
            RECORDER.add_time(name, (time.perf_counter() - start) * 1000)

    return wrapper


def _record_usage(original):
    @functools.wraps(original)
    def record_usage(model, usage):
        if RECORDER.active:
            RECORDER.add_usage({
                "stage": RECORDER.stage,
                "model": model,
                "input": usage.get("input", 0),
                "output": usage.get("output", 0),
                "cached": usage.get("cached", 0),
            })
        return original(model, usage)

    return record_usage


def install() -> None:
    global _installed
    if _installed:
        return
    _installed = True

    def keep_context(result):
        RECORDER.context = result

    def keep_generation(result):
        RECORDER.generation = result

    def keep_structured(result):
        RECORDER.structured = result

    def keep_tabular(result):
        RECORDER.tabular_detected = result

    chat_module._gather_context = _spy("retrieve", chat_module._gather_context, keep_context)
    chat_module.embed_query = _spy("embed_query", chat_module.embed_query)
    chat_module.semantic_search = _spy("search", chat_module.semantic_search)
    chat_module.hybrid_search = _spy("search", chat_module.hybrid_search)
    chat_module.stream_answer = _spy_stream("generate", chat_module.stream_answer)
    chat_module.finalize_answer = _spy("finalize", chat_module.finalize_answer, keep_generation)
    chat_module.execute_structured_query = _spy("structured_query", chat_module.execute_structured_query, keep_structured)
    chat_module.is_tabular_question = _spy("route", chat_module.is_tabular_question, keep_tabular)
    for name in _DB_FUNCTIONS:
        setattr(db, name, _spy("db", getattr(db, name)))
    gemini_client.record_usage = _record_usage(gemini_client.record_usage)


def set_context_mode(mode: str) -> None:
    chat_module.FULL_CONTEXT_MAX_CHUNKS = CONTEXT_MODES[mode]
