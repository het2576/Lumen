import ipaddress
import logging
import re
import socket
from pathlib import Path
from urllib.parse import urlparse

import httpx
import pandas as pd
import pdfplumber
import tiktoken
import trafilatura
from google.genai import errors as genai_errors
from google.genai import types as genai_types

from app import db
from app.config import CHUNK_OVERLAP_TOKENS, CHUNK_SIZE_TOKENS
from app.services import gemini_client
from app.services.embeddings import embed_texts
from app.services.spreadsheet import to_number

logger = logging.getLogger(__name__)

_encoder = tiktoken.get_encoding("cl100k_base")
MAX_WEBPAGE_BYTES = 8 * 1024 * 1024


def validate_public_url(url: str) -> str:
    """Accept a normal public http(s) URL and reject private-network targets."""
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Enter a complete public URL beginning with http:// or https://.")
    try:
        addresses = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError("The URL host could not be resolved.") from exc
    for address in addresses:
        if not ipaddress.ip_address(address[4][0]).is_global:
            raise ValueError("Private or local network URLs are not supported.")
    return parsed.geturl()


def _fetch_webpage(url: str) -> str:
    validated_url = validate_public_url(url)
    headers = {"User-Agent": "LumenReader/2.0 (+https://lumen.local)"}
    with httpx.Client(follow_redirects=True, timeout=20, headers=headers) as client:
        with client.stream("GET", validated_url) as response:
            response.raise_for_status()
            validate_public_url(str(response.url))
            if "html" not in response.headers.get("content-type", "").lower():
                raise ValueError("This URL did not return an HTML webpage.")
            chunks, size = [], 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > MAX_WEBPAGE_BYTES:
                    raise ValueError("This webpage is too large to add as a source.")
                chunks.append(chunk)
    html = b"".join(chunks).decode(response.encoding or "utf-8", errors="replace")
    text = trafilatura.extract(html, include_tables=True, include_comments=False, favor_precision=True)
    if not text or len(text.strip()) < 120:
        raise ValueError("Lumen couldn't extract enough readable article text from this page.")
    return text.strip()


def extract_text(file_path: str) -> list[dict]:
    pages = []
    with pdfplumber.open(file_path) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            if text.strip():
                pages.append({"text": text, "page_number": i})
    return pages


def _clean_cell(value: object) -> str:
    if value is None:
        return ""
    return " ".join(str(value).replace("\n", " ").split())


def _dedupe_headers(headers: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    result = []
    for index, value in enumerate(headers):
        base = value.strip() or f"Column {index + 1}"
        seen[base] = seen.get(base, 0) + 1
        result.append(base if seen[base] == 1 else f"{base} ({seen[base]})")
    return result


def _infer_types(rows: list[dict], headers: list[str]) -> dict[str, str]:
    inferred: dict[str, str] = {}
    for header in headers:
        values = [row.get(header) for row in rows if row.get(header) not in (None, "")]
        if not values:
            inferred[header] = "text"
            continue
        numeric = to_number(pd.Series(values)).notna().mean()
        dates = pd.to_datetime(pd.Series(values), errors="coerce", format="mixed").notna().mean()
        inferred[header] = "numeric" if numeric >= 0.8 else "date" if dates >= 0.8 else "text"
    return inferred


def dataframe_to_table(frame: pd.DataFrame, table_name: str, *, page_number: int | None = None, source_kind: str = "spreadsheet") -> dict | None:
    """Turn a DataFrame into JSON-safe rows while preserving its shape and headers."""
    frame = frame.dropna(axis=0, how="all").dropna(axis=1, how="all")
    if frame.empty or len(frame.columns) == 0:
        return None
    frame.columns = _dedupe_headers([_clean_cell(column) for column in frame.columns])
    frame = frame.where(pd.notna(frame), None)
    rows = [{key: _clean_cell(value) for key, value in row.items()} for row in frame.to_dict(orient="records")]
    if not rows:
        return None
    headers = list(frame.columns)
    return {
        "table_name": table_name,
        "page_number": page_number,
        "column_headers": headers,
        "row_data": rows,
        "inferred_types": _infer_types(rows, headers),
        "source_kind": source_kind,
        "verified": True,
    }


def extract_pdf_tables(file_path: str) -> list[dict]:
    tables: list[dict] = []
    with pdfplumber.open(file_path) as pdf:
        for page_number, page in enumerate(pdf.pages, start=1):
            for table_index, raw_table in enumerate(page.extract_tables(), start=1):
                if not raw_table or len(raw_table) < 2:
                    continue
                headers = _dedupe_headers([_clean_cell(value) for value in raw_table[0]])
                # Ignore obvious false positives such as a single-column line of prose.
                if len(headers) < 2:
                    continue
                body = [row[: len(headers)] + [None] * max(0, len(headers) - len(row)) for row in raw_table[1:] if any(_clean_cell(cell) for cell in row)]
                table = dataframe_to_table(
                    pd.DataFrame(body, columns=headers),
                    f"Page {page_number} · Table {table_index}",
                    page_number=page_number,
                    source_kind="pdf_table",
                )
                if table:
                    tables.append(table)
    return tables


def extract_spreadsheet_tables(file_path: str) -> list[dict]:
    path = Path(file_path)
    if path.suffix.lower() == ".csv":
        # dtype=object preserves messy, mixed columns instead of silently coercing them.
        frames = {path.stem: pd.read_csv(file_path, dtype=object, keep_default_na=True)}
    else:
        frames = pd.read_excel(file_path, sheet_name=None, dtype=object)
    tables = []
    for sheet_name, frame in frames.items():
        table = dataframe_to_table(frame, str(sheet_name), source_kind="spreadsheet")
        if table:
            tables.append(table)
    return tables


def chunk_text(pages: list[dict]) -> list[dict]:
    chunks = []
    chunk_index = 0

    for page in pages:
        tokens = _encoder.encode(page["text"])
        start = 0
        while start < len(tokens):
            end = min(start + CHUNK_SIZE_TOKENS, len(tokens))
            chunk_tokens = tokens[start:end]
            chunk_str = _encoder.decode(chunk_tokens).strip()
            if chunk_str:
                chunks.append(
                    {
                        "text": chunk_str,
                        "page_number": page["page_number"],
                        "chunk_index": chunk_index,
                    }
                )
                chunk_index += 1
            if end == len(tokens):
                break
            start = end - CHUNK_OVERLAP_TOKENS

    return chunks


def embed_chunks(chunks: list[dict]) -> list[dict]:
    vectors = embed_texts([chunk["text"] for chunk in chunks], task_type="RETRIEVAL_DOCUMENT")
    return [{**chunk, "embedding": vector} for chunk, vector in zip(chunks, vectors)]


def _read_local(extract, file_path: str):
    """Parsing a local file is deterministic: if it fails once it fails every time, so report
    it as unusable input (ValueError) instead of letting the queue retry it."""
    try:
        return extract(file_path)
    except Exception as exc:
        kind = Path(file_path).suffix.lstrip(".").upper() or "file"
        raise ValueError(f"Lumen couldn't read this {kind}. It may be damaged or not a real {kind} file ({type(exc).__name__}).") from exc


def ingest_file_or_raise(file_path: str, document_id: str) -> None:
    """Index a PDF/CSV/XLSX and mark the document ready. Raises on failure (the queue decides
    whether to retry); ValueError means the input itself is unusable."""
    suffix = Path(file_path).suffix.lower()
    if suffix in {".csv", ".xlsx"}:
        tables = _read_local(extract_spreadsheet_tables, file_path)
        if not tables:
            raise ValueError("No usable rows or columns were found in this spreadsheet.")
        db.insert_document_tables(document_id, tables)
        db.update_document_status(document_id, "ready", page_count=len(tables), chunk_count=0)
        return

    pages = _read_local(extract_text, file_path)
    if not pages:
        raise ValueError("No extractable text found in PDF (it may be scanned/image-only).")

    chunks = chunk_text(pages)
    if not chunks:
        raise ValueError("Text extracted but chunking produced no chunks.")

    embedded_chunks = embed_chunks(chunks)
    db.insert_chunks(document_id, embedded_chunks)
    try:
        db.insert_document_tables(document_id, extract_pdf_tables(file_path))
    except Exception:
        # Table detection is an enhancement; it must not make a readable
        # document unusable when a malformed table confuses the extractor.
        logger.exception("Table extraction failed for document %s", document_id)

    db.update_document_status(
        document_id,
        "ready",
        page_count=len(pages),
        chunk_count=len(embedded_chunks),
    )


def ingest_document(file_path: str, document_id: str) -> None:
    """Synchronous entry point for scripts and evals: never raises, records failure instead."""
    try:
        ingest_file_or_raise(file_path, document_id)
    except Exception as e:
        logger.exception("Ingestion failed for document %s", document_id)
        db.update_document_status(document_id, "failed", error_message=str(e)[:500])


def ingest_webpage_or_raise(url: str, document_id: str) -> None:
    """Ingest a readable webpage through the same chunk and embedding flow as a PDF."""
    text = _fetch_webpage(url)
    chunks = chunk_text([{"text": text, "page_number": 1}])
    if not chunks:
        raise ValueError("The webpage did not contain enough readable text to index.")
    embedded_chunks = embed_chunks(chunks)
    db.insert_chunks(document_id, embedded_chunks)
    db.update_document_status(document_id, "ready", page_count=1, chunk_count=len(embedded_chunks))


def ingest_webpage(url: str, document_id: str) -> None:
    try:
        ingest_webpage_or_raise(url, document_id)
    except Exception as exc:
        logger.exception("Webpage ingestion failed for document %s", document_id)
        db.update_document_status(document_id, "failed", error_message=str(exc)[:500])


def _parse_youtube_id(url: str) -> str:
    """Extract the 11-character video ID from any common YouTube URL format."""
    match = re.search(r"(?:v=|youtu\.be/|/embed/|/shorts/|/live/)([A-Za-z0-9_-]{11})", url)
    if not match:
        raise ValueError("Could not find a valid YouTube video ID in that URL.")
    return match.group(1)


_CAPTION_LANGUAGES = [
    "en", "en-IN", "en-US", "en-GB",
    "hi", "hi-IN",
    "ta", "te", "mr", "bn", "gu", "kn", "ml", "pa", "es", "fr", "de",
]
_TIMESTAMPED_LINE = re.compile(r"^\s*\[?(?:(\d{1,2}):)?(\d{1,2}):(\d{2})\]?\s*[-–:]?\s*(.+)$")
_TRANSCRIBE_PROMPT = (
    "Transcribe the spoken audio of this video verbatim in its original language. "
    "Output only lines formatted as [MM:SS] text (use [H:MM:SS] past the first hour), "
    "one line per sentence or short passage. No headings or commentary."
)


def _fetch_caption_segments(video_id: str) -> list[tuple[float, str]]:
    """Read YouTube's own caption track — fast and free, but YouTube blocks most cloud-provider IPs."""
    from youtube_transcript_api import NoTranscriptFound, YouTubeTranscriptApi  # type: ignore

    transcripts = YouTubeTranscriptApi().list(video_id)
    try:
        transcript = transcripts.find_transcript(_CAPTION_LANGUAGES)
    except NoTranscriptFound:
        transcript = next(iter(transcripts), None)
    if transcript is None:
        raise ValueError("This video has no caption tracks.")
    segments = [(float(snippet.start), snippet.text) for snippet in transcript.fetch()]
    if not segments:
        raise ValueError("The caption track was empty.")
    return segments


def _transcribe_with_gemini(video_id: str) -> list[tuple[float, str]]:
    """Have Gemini transcribe the video. Google fetches the video itself, so this works from
    cloud hosts that YouTube blocks, and for videos that have no captions at all."""
    contents = genai_types.Content(role="user", parts=[
        genai_types.Part(
            file_data=genai_types.FileData(file_uri=f"https://www.youtube.com/watch?v={video_id}"),
            video_metadata=genai_types.VideoMetadata(fps=0.1),
        ),
        genai_types.Part(text=_TRANSCRIBE_PROMPT),
    ])
    try:
        response = gemini_client.generate(
            contents,
            # Only the audio matters for a transcript; sparse low-resolution frames keep a
            # one-hour video near 90k input tokens.
            config={"media_resolution": genai_types.MediaResolution.MEDIA_RESOLUTION_LOW, "temperature": 0, "max_output_tokens": 65536},
            timeout=900,
            name="transcribe",
        )
    except genai_errors.APIError as exc:
        if gemini_client.is_transient(exc):
            raise  # the ingestion queue retries these
        raise ValueError(f"Gemini couldn't read this video ({exc.code}): {str(exc.message or exc)[:240]}") from exc

    text = response.text or ""
    candidate = (response.candidates or [None])[0]
    if candidate is not None and candidate.finish_reason == genai_types.FinishReason.MAX_TOKENS:
        logger.warning("Gemini transcript for %s hit the output limit; indexing the part returned", video_id)

    segments = []
    for line in text.splitlines():
        match = _TIMESTAMPED_LINE.match(line)
        if match:
            hours, minutes, seconds, spoken = match.groups()
            segments.append((int(hours or 0) * 3600 + int(minutes) * 60 + int(seconds), spoken.strip()))
    return segments


def _segments_to_paragraphs(segments: list[tuple[float, str]], block_seconds: float = 120.0) -> list[dict]:
    """Merge timed segments into ~2-minute paragraphs for better retrieval context."""
    paragraphs: list[dict] = []
    current: list[str] = []
    block_start = 0.0
    for start, raw_text in segments:
        text = raw_text.strip().replace("\n", " ")
        if not text:
            continue
        if not current:
            block_start = start
        current.append(text)
        if start - block_start >= block_seconds:
            paragraphs.append({"text": " ".join(current), "page_number": len(paragraphs) + 1})
            current = []
    if current:
        paragraphs.append({"text": " ".join(current), "page_number": len(paragraphs) + 1})
    return paragraphs


def ingest_youtube_or_raise(url: str, document_id: str) -> None:
    """Index a YouTube video from its captions when reachable, otherwise from a Gemini transcription."""
    video_id = _parse_youtube_id(url)
    try:
        segments = _fetch_caption_segments(video_id)
        source = "captions"
    except Exception as caption_error:
        logger.warning(
            "Captions unavailable for %s (%s: %s); transcribing with Gemini",
            video_id, type(caption_error).__name__, str(caption_error)[:160],
        )
        segments = _transcribe_with_gemini(video_id)
        source = "gemini"

    paragraphs = _segments_to_paragraphs(segments)
    if not paragraphs:
        raise ValueError("No speech could be transcribed from this video.")
    chunks = chunk_text(paragraphs)
    if not chunks:
        raise ValueError("The transcript was too short to index.")

    embedded_chunks = embed_chunks(chunks)
    db.insert_chunks(document_id, embedded_chunks)
    db.update_document_status(document_id, "ready", page_count=len(paragraphs), chunk_count=len(embedded_chunks))
    logger.info(
        "YouTube ingestion done: video=%s source=%s paragraphs=%d chunks=%d",
        video_id, source, len(paragraphs), len(embedded_chunks),
    )


def ingest_youtube(url: str, document_id: str) -> None:
    try:
        ingest_youtube_or_raise(url, document_id)
    except Exception as exc:
        logger.exception("YouTube ingestion failed for document %s", document_id)
        db.update_document_status(document_id, "failed", error_message=str(exc)[:500])
