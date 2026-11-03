"""Per-request tracing: stage timings, LLM token usage and cost.

Every traced unit of work (a chat request, an ingestion job) is a Trace. When it finishes it
always writes one structured log line, e.g.

    trace {"name": "chat", "ms": 2140, "first_token_ms": 910, "stages": {"retrieve": 420, ...},
           "tokens": {"input": 5210, "output": 180, "cached": 0}, "cost_usd": null, ...}

and, when LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY are set, it also exports the same tree
of spans and generations to Langfuse.

Observations are explicit objects handed down the call chain rather than a context-local
"current span": a streaming response runs each step of its generator in a fresh copy of the
request context, so anything kept in a contextvar between chunks would be lost.
"""
import json
import logging
import threading
import time
import uuid
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from app import config

logger = logging.getLogger("lumen.trace")

_langfuse: Any = None
_langfuse_lock = threading.Lock()
_PREVIEW_CHARS = 4000


def _client():
    """The Langfuse client, created on first use; None when tracing to logs only."""
    global _langfuse
    if not config.LANGFUSE_ENABLED:
        return None
    if _langfuse is None:
        with _langfuse_lock:
            if _langfuse is None:
                try:
                    from langfuse import Langfuse

                    _langfuse = Langfuse()  # reads LANGFUSE_* env vars
                except Exception:
                    logger.exception("Langfuse unavailable; tracing to logs only")
                    _langfuse = False
    return _langfuse or None


def flush() -> None:
    """Send buffered spans before the process exits."""
    client = _client()
    if client:
        try:
            client.flush()
        except Exception:
            logger.exception("Langfuse flush failed")


def preview(value: Any) -> Any:
    """Keep traces small: long prompts are truncated, never dropped silently."""
    if isinstance(value, str) and len(value) > _PREVIEW_CHARS:
        return value[:_PREVIEW_CHARS] + f"… [{len(value) - _PREVIEW_CHARS} more chars]"
    return value


def cost_usd(usage: dict) -> float | None:
    if config.PRICE_INPUT_PER_M is None or config.PRICE_OUTPUT_PER_M is None:
        return None
    cached = usage.get("cached", 0)
    price_cached = config.PRICE_CACHED_PER_M if config.PRICE_CACHED_PER_M is not None else config.PRICE_INPUT_PER_M
    return (
        (usage.get("input", 0) - cached) * config.PRICE_INPUT_PER_M
        + cached * price_cached
        + usage.get("output", 0) * config.PRICE_OUTPUT_PER_M
    ) / 1_000_000


def _error_text(error: Any) -> str | None:
    if error is None:
        return None
    if isinstance(error, BaseException):
        return f"{type(error).__name__}: {str(error)[:300]}"
    return str(error)[:300]


class Observation:
    """A timed step. Children are created with span() and generation()."""

    def __init__(self, trace: "Trace | None", name: str, remote: Any = None) -> None:
        self.trace = trace
        self.name = name
        self._remote = remote
        self._started = time.perf_counter()
        self._ended = False

    @property
    def elapsed_ms(self) -> float:
        return (time.perf_counter() - self._started) * 1000

    def _start_remote(self, name: str, as_type: str, **fields: Any) -> Any:
        if self._remote is None or self.trace is None:
            return None
        try:
            from langfuse import propagate_attributes

            # Set user/session on every observation, not just the root, so Langfuse's
            # per-user and per-session cost views include all of them.
            with propagate_attributes(user_id=self.trace.user_id, session_id=self.trace.session_id, trace_name=self.trace.name):
                return self._remote.start_observation(name=name, as_type=as_type, **{k: v for k, v in fields.items() if v is not None})
        except Exception:
            logger.debug("Could not start Langfuse observation %s", name, exc_info=True)
            return None

    @contextmanager
    def span(self, name: str, **metadata: Any) -> Iterator["Observation"]:
        child = Observation(self.trace, name, self._start_remote(name, "span", metadata=metadata or None))
        try:
            yield child
        except BaseException as exc:
            child.end(error=exc)
            raise
        child.end()

    def generation(self, name: str, *, model: str, input: Any = None, parameters: dict | None = None) -> "Generation":
        remote = self._start_remote(name, "generation", model=model, input=preview(input), model_parameters=parameters)
        return Generation(self.trace, name, remote, model=model)

    def update(self, **metadata: Any) -> None:
        if self._remote is not None and metadata:
            try:
                self._remote.update(metadata=metadata)
            except Exception:
                logger.debug("Langfuse update failed", exc_info=True)

    def end(self, *, output: Any = None, error: Any = None) -> None:
        if self._ended:
            return
        self._ended = True
        if self.trace is not None and self is not self.trace:
            self.trace._add_stage(self.name, self.elapsed_ms)
        if self._remote is not None:
            try:
                fields: dict[str, Any] = {}
                if output is not None:
                    fields["output"] = preview(output)
                if error is not None:
                    fields.update(level="ERROR", status_message=_error_text(error))
                if fields:
                    self._remote.update(**fields)
                self._remote.end()
            except Exception:
                logger.debug("Langfuse end failed", exc_info=True)


class Generation(Observation):
    """One LLM call: records time to first token, token usage and cost."""

    def __init__(self, trace: "Trace | None", name: str, remote: Any, *, model: str) -> None:
        super().__init__(trace, name, remote)
        self.model = model
        self.first_token_ms: float | None = None

    def first_token(self) -> None:
        if self.first_token_ms is not None:
            return
        self.first_token_ms = self.elapsed_ms
        if self.trace is not None:
            self.trace._mark_first_token()
        if self._remote is not None:
            try:
                self._remote.update(completion_start_time=datetime.now(timezone.utc))
            except Exception:
                logger.debug("Langfuse update failed", exc_info=True)

    def finish(self, *, output: Any = None, usage: dict | None = None, error: Any = None) -> None:
        usage = usage or {}
        cost = cost_usd(usage) if usage else None
        if self.trace is not None:
            self.trace._add_usage(usage, cost)
        if self._remote is not None and usage:
            try:
                cached = usage.get("cached", 0)
                details = {"input": usage.get("input", 0) - cached, "output": usage.get("output", 0)}
                if cached:
                    details["input_cached_tokens"] = cached
                fields: dict[str, Any] = {"usage_details": details}
                if cost is not None:
                    fields["cost_details"] = {"total": cost}
                self._remote.update(**fields)
            except Exception:
                logger.debug("Langfuse usage update failed", exc_info=True)
        self.end(output=output, error=error)


class Trace(Observation):
    """The root of one unit of work. Call finish() exactly once when it is done."""

    def __init__(self, name: str, *, user_id: str | None = None, session_id: str | None = None,
                 input: Any = None, metadata: dict | None = None) -> None:
        self.id = uuid.uuid4().hex[:16]
        self.user_id = user_id
        self.session_id = session_id
        self.attributes: dict[str, Any] = dict(metadata or {})
        self.stages: dict[str, float] = defaultdict(float)
        self.tokens = {"input": 0, "output": 0, "cached": 0}
        self.cost: float | None = None
        self.llm_calls = 0
        self.first_token_ms: float | None = None
        self._lock = threading.Lock()
        remote = None
        client = _client()
        if client is not None:
            try:
                from langfuse import propagate_attributes

                with propagate_attributes(user_id=user_id, session_id=session_id, trace_name=name):
                    remote = client.start_observation(name=name, as_type="span", input=preview(input), metadata=metadata or None)
            except Exception:
                logger.debug("Could not start Langfuse trace", exc_info=True)
        super().__init__(self, name, remote)

    def set(self, **attributes: Any) -> None:
        """Record facts about the request (route, chunk count, …) for the log line and trace."""
        self.attributes.update(attributes)
        self.update(**attributes)

    def _add_stage(self, name: str, ms: float) -> None:
        with self._lock:
            self.stages[name] += ms

    def _add_usage(self, usage: dict, cost: float | None) -> None:
        with self._lock:
            self.llm_calls += 1
            for key in self.tokens:
                self.tokens[key] += int(usage.get(key, 0) or 0)
            if cost is not None:
                self.cost = (self.cost or 0.0) + cost

    def _mark_first_token(self) -> None:
        with self._lock:
            if self.first_token_ms is None:
                self.first_token_ms = self.elapsed_ms

    def finish(self, *, output: Any = None, error: Any = None) -> None:
        if self._ended:
            return
        total_ms = self.elapsed_ms
        self.end(output=output, error=error)
        record = {
            "name": self.name,
            "id": self.id,
            "ms": round(total_ms),
            "first_token_ms": round(self.first_token_ms) if self.first_token_ms is not None else None,
            "stages": {name: round(ms) for name, ms in self.stages.items()},
            "llm_calls": self.llm_calls,
            "tokens": self.tokens,
            "cost_usd": round(self.cost, 6) if self.cost is not None else None,
            **self.attributes,
        }
        if error is not None:
            record["error"] = _error_text(error)
        logger.info("trace %s", json.dumps(record, default=str))


class _NoopTrace(Trace):
    """Used when a caller passes no trace: everything works, nothing is recorded."""

    def __init__(self) -> None:
        Observation.__init__(self, None, "noop")
        self.id = ""
        self.user_id = self.session_id = None
        self.attributes = {}
        self.stages = defaultdict(float)
        self.tokens = {"input": 0, "output": 0, "cached": 0}
        self.cost = None
        self.llm_calls = 0
        self.first_token_ms = None
        self._lock = threading.Lock()

    def set(self, **_: Any) -> None:
        return  # shared instance: never accumulate state

    def finish(self, **_: Any) -> None:
        return


NOOP = _NoopTrace()
