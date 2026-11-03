"""Ingestion worker: drains the durable ingestion_jobs queue.

The API never ingests inline. It stores the upload in Supabase Storage, inserts a job row and
calls wake(). Worker threads claim jobs one at a time with claim_ingestion_job (FOR UPDATE SKIP
LOCKED), so any number of threads or instances can run without taking the same job.

Surviving restarts: while a job runs, a heartbeat refreshes locked_at. If the process dies (a
deploy, a crash, Render recycling the instance), the heartbeat stops, the lock goes stale after
INGESTION_STALE_SECONDS, and the next worker to poll claims the job again. Each retry first
clears partial chunks from the interrupted attempt, so retries never duplicate content.

Failures: unusable input (ValueError: scanned PDF, private video, …) fails the document
immediately with its message. Anything else (rate limits, network, Gemini overload) is retried
with backoff up to INGESTION_MAX_ATTEMPTS before the document is marked failed.

Runs inside the API process by default (started from main.py). To run it as its own service
instead, set INGESTION_WORKER_ENABLED=false on the API and start `python -m app.worker`.
"""
import logging
import os
import signal
import tempfile
import threading
from pathlib import Path

import httpx
from google.genai import errors as genai_errors

from app import db, tracing
from app.config import (
    INGESTION_MAX_ATTEMPTS,
    INGESTION_POLL_SECONDS,
    INGESTION_STALE_SECONDS,
    INGESTION_WORKER_THREADS,
)
from app.services import gemini_client
from app.services.ingestion import ingest_file_or_raise, ingest_webpage_or_raise, ingest_youtube_or_raise

logger = logging.getLogger(__name__)

_RETRY_DELAYS_SECONDS = (15, 60, 180)


class _Heartbeat:
    """Keeps a running job's lock fresh so other workers know it is still alive."""

    def __init__(self, job_id: str) -> None:
        self._job_id = job_id
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=f"heartbeat-{job_id[:8]}", daemon=True)

    def __enter__(self) -> "_Heartbeat":
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._stop.set()
        self._thread.join(timeout=5)

    def _run(self) -> None:
        interval = max(INGESTION_STALE_SECONDS / 3, 5)
        while not self._stop.wait(interval):
            try:
                db.heartbeat_ingestion_job(self._job_id)
            except Exception:
                logger.warning("Heartbeat failed for job %s", self._job_id, exc_info=True)


def _is_permanent(exc: BaseException) -> bool:
    """Retrying cannot fix these: the input itself is unusable or was refused."""
    if isinstance(exc, ValueError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return 400 <= exc.response.status_code < 500 and exc.response.status_code not in {408, 429}
    if isinstance(exc, genai_errors.APIError):
        return not gemini_client.is_transient(exc)
    return False


def _run_file_job(job: dict) -> None:
    payload = job["payload"]
    storage_path = payload["storage_path"]
    suffix = Path(storage_path).suffix.lower()
    contents = db.download_source_file(storage_path)
    with tempfile.TemporaryDirectory(prefix="lumen-ingest-") as directory:
        local_path = Path(directory) / f"{job['document_id']}{suffix}"
        local_path.write_bytes(contents)
        ingest_file_or_raise(str(local_path), job["document_id"])


def _execute(job: dict) -> None:
    kind = job["kind"]
    if kind == "file":
        _run_file_job(job)
    elif kind == "webpage":
        ingest_webpage_or_raise(job["payload"]["url"], job["document_id"])
    elif kind == "youtube":
        ingest_youtube_or_raise(job["payload"]["url"], job["document_id"])
    else:
        raise ValueError(f"Unknown ingestion job kind: {kind}")


def _remove_source(job: dict) -> None:
    storage_path = (job.get("payload") or {}).get("storage_path")
    if storage_path:
        try:
            db.remove_source_file(storage_path)
        except Exception:
            logger.warning("Could not remove processed upload %s", storage_path, exc_info=True)


def _fail(job: dict, message: str) -> None:
    db.update_document_status(job["document_id"], "failed", error_message=message[:500])
    db.finish_ingestion_job(job["id"], "failed", message[:1000])
    _remove_source(job)


def process_job(job: dict) -> None:
    attempt = job["attempts"]
    trace = tracing.Trace("ingest", metadata={"job_id": job["id"], "document_id": job["document_id"], "kind": job["kind"], "attempt": attempt})
    if attempt > INGESTION_MAX_ATTEMPTS:
        # Reclaimed after its worker died on the final attempt.
        _fail(job, "Processing was interrupted repeatedly. Please try adding this source again.")
        trace.finish(error="gave up after interrupted attempts")
        return
    try:
        with _Heartbeat(job["id"]):
            if attempt > 1:
                db.clear_document_content(job["document_id"])
            with trace.span(job["kind"]):
                _execute(job)
    except Exception as exc:
        if _is_permanent(exc):
            logger.warning("Ingestion job %s failed permanently: %s", job["id"], exc)
            _fail(job, str(exc))
            trace.finish(error=exc)
            return
        message = f"{type(exc).__name__}: {exc}"
        if attempt < INGESTION_MAX_ATTEMPTS:
            delay = _RETRY_DELAYS_SECONDS[min(attempt - 1, len(_RETRY_DELAYS_SECONDS) - 1)]
            logger.warning("Ingestion job %s attempt %d failed (%s); retrying in %ds", job["id"], attempt, message[:200], delay)
            db.retry_ingestion_job(job["id"], message[:1000], delay)
        else:
            logger.exception("Ingestion job %s failed after %d attempts", job["id"], attempt)
            _fail(job, str(exc) or message)
        trace.finish(error=exc)
        return
    db.finish_ingestion_job(job["id"], "done")
    _remove_source(job)
    trace.finish()


class IngestionWorker:
    def __init__(self, threads: int = INGESTION_WORKER_THREADS, poll_seconds: float = INGESTION_POLL_SECONDS) -> None:
        self._threads_wanted = max(1, threads)
        self._poll_seconds = poll_seconds
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        if self._threads:
            return
        self._stop.clear()
        for index in range(self._threads_wanted):
            thread = threading.Thread(target=self._loop, name=f"ingestion-worker-{index}", daemon=True)
            thread.start()
            self._threads.append(thread)
        logger.info("Ingestion worker started (%d thread%s)", self._threads_wanted, "" if self._threads_wanted == 1 else "s")

    def stop(self, timeout: float = 5.0) -> None:
        """Stop claiming new jobs. A job in progress is left to its heartbeat: if the process
        exits before it finishes, another worker reclaims it once the lock goes stale."""
        self._stop.set()
        self._wake.set()
        for thread in self._threads:
            thread.join(timeout=timeout)
        self._threads = []

    def wake(self) -> None:
        """Check the queue now instead of at the next poll."""
        self._wake.set()

    def _loop(self) -> None:
        failures = 0
        while not self._stop.is_set():
            try:
                job = db.claim_ingestion_job(INGESTION_STALE_SECONDS)
                if failures:
                    logger.info("Ingestion queue reachable again")
                failures = 0
            except Exception as exc:
                failures += 1
                if failures == 1:
                    hint = " — run backend/db/schema.sql in the Supabase SQL editor" if "PGRST202" in str(exc) or "PGRST205" in str(exc) else ""
                    logger.error("Could not poll the ingestion queue%s: %s", hint, str(exc)[:300])
                # Back off while the database is unreachable or not migrated, up to a minute.
                self._wake.wait(min(self._poll_seconds * 2 ** min(failures, 4), 60))
                self._wake.clear()
                continue
            if job is None:
                self._wake.wait(self._poll_seconds)
                self._wake.clear()
                continue
            try:
                process_job(job)
            except Exception:
                # Bookkeeping itself failed (e.g. the database blipped). The job's heartbeat
                # has stopped, so it will be reclaimed once its lock goes stale.
                logger.exception("Ingestion job %s could not be recorded", job.get("id"))


WORKER = IngestionWorker()


def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    stopped = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stopped.set())
    WORKER.start()
    stopped.wait()
    WORKER.stop()
    tracing.flush()


if __name__ == "__main__":
    main()
