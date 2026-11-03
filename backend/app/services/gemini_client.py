"""The one place Lumen talks to Gemini, through the google-genai SDK.

Every model call goes through generate_text, stream_text or embed, so timeouts, retries,
token accounting and tracing are handled once instead of at each call site.
"""
import logging
import threading
from typing import Any, Iterator

import httpx
from google import genai
from google.genai import errors, types

from app import tracing
from app.config import EMBEDDING_DIM, EMBEDDING_MODEL, GEMINI_API_KEY, GENERATION_MODEL, GENERATION_TIMEOUT_SECONDS

logger = logging.getLogger(__name__)

TRANSIENT_STATUS = {408, 429, 500, 502, 503, 504}
# Overloaded-model 503s and brief 429s are common; a few quick retries rescue most of them.
_GENERATION_RETRY = types.HttpRetryOptions(attempts=3, initial_delay=1.0, max_delay=8.0, http_status_codes=sorted(TRANSIENT_STATUS))
# Embeddings pace themselves against the per-minute quota (services/embeddings.py), so
# SDK-level retries would only burn quota with short, blind waits.
_NO_RETRY = types.HttpRetryOptions(attempts=1)

_client: genai.Client | None = None
_client_lock = threading.Lock()


def get_client() -> genai.Client:
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                if not GEMINI_API_KEY:
                    raise RuntimeError(
                        "GEMINI_API_KEY is not set. Copy backend/.env.example to backend/.env and fill it in."
                    )
                _client = genai.Client(
                    api_key=GEMINI_API_KEY,
                    http_options=types.HttpOptions(timeout=int(GENERATION_TIMEOUT_SECONDS * 1000), retry_options=_GENERATION_RETRY),
                )
    return _client


def is_transient(exc: BaseException) -> bool:
    """Rate limits, overload and network blips: worth retrying later, unlike bad input."""
    if isinstance(exc, errors.APIError):
        return exc.code in TRANSIENT_STATUS
    return isinstance(exc, (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError))


def is_rate_limited(exc: BaseException) -> bool:
    return isinstance(exc, errors.APIError) and exc.code == 429


def usage_of(response: Any) -> dict:
    """Token counts in Lumen's shape. Thinking tokens are billed as output, so they count as output."""
    usage = getattr(response, "usage_metadata", None)
    if usage is None:
        return {}
    return {
        "input": usage.prompt_token_count or 0,
        "output": (usage.candidates_token_count or 0) + (usage.thoughts_token_count or 0),
        "cached": usage.cached_content_token_count or 0,
    }


def record_usage(model: str, usage: dict) -> None:
    """Called after every generation call. The eval harness wraps this to count tokens."""
    logger.debug("Gemini %s usage %s", model, usage)


def _config(config: dict | None, timeout: float | None) -> types.GenerateContentConfig | None:
    fields = dict(config or {})
    if timeout is not None:
        fields["http_options"] = types.HttpOptions(timeout=int(timeout * 1000))
    return types.GenerateContentConfig(**fields) if fields else None


def generate(
    contents: Any,
    *,
    model: str = GENERATION_MODEL,
    config: dict | None = None,
    timeout: float | None = None,
    parent: tracing.Observation = tracing.NOOP,
    name: str = "llm",
) -> types.GenerateContentResponse:
    generation = parent.generation(name, model=model, input=contents if isinstance(contents, str) else None, parameters=config)
    try:
        response = get_client().models.generate_content(model=model, contents=contents, config=_config(config, timeout))
    except Exception as exc:
        generation.finish(error=exc)
        raise
    usage = usage_of(response)
    record_usage(model, usage)
    generation.finish(output=response.text or "", usage=usage)
    return response


def generate_text(contents: Any, **options: Any) -> str:
    return generate(contents, **options).text or ""


def stream_text(
    contents: Any,
    *,
    model: str = GENERATION_MODEL,
    config: dict | None = None,
    timeout: float | None = None,
    parent: tracing.Observation = tracing.NOOP,
    name: str = "llm",
) -> Iterator[str]:
    """Yield the answer as it is generated. Usage arrives with the final chunk."""
    generation = parent.generation(name, model=model, input=contents if isinstance(contents, str) else None, parameters=config)
    parts: list[str] = []
    usage: dict = {}
    try:
        for chunk in get_client().models.generate_content_stream(model=model, contents=contents, config=_config(config, timeout)):
            usage = usage_of(chunk) or usage
            text = chunk.text
            if text:
                generation.first_token()
                parts.append(text)
                yield text
    except GeneratorExit:
        # The client went away mid-answer; the tokens generated so far were still billed.
        record_usage(model, usage)
        generation.finish(output="".join(parts), usage=usage, error="client disconnected")
        raise
    except Exception as exc:
        generation.finish(output="".join(parts), usage=usage, error=exc)
        raise
    record_usage(model, usage)
    generation.finish(output="".join(parts), usage=usage)


def embed(texts: list[str], *, task_type: str, model: str = EMBEDDING_MODEL, dimensions: int = EMBEDDING_DIM) -> list[list[float]]:
    result = get_client().models.embed_content(
        model=model,
        contents=texts,
        config=types.EmbedContentConfig(
            task_type=task_type,
            output_dimensionality=dimensions,
            http_options=types.HttpOptions(retry_options=_NO_RETRY),
        ),
    )
    return [list(embedding.values or []) for embedding in result.embeddings or []]
