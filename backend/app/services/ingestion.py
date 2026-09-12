import logging
import ipaddress
import socket
from pathlib import Path
from urllib.parse import urlparse

import httpx
import pandas as pd
import pdfplumber
import tiktoken
import trafilatura

from app import db
from app.config import CHUNK_OVERLAP_TOKENS, CHUNK_SIZE_TOKENS
from app.services.embeddings import embed_texts

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
        numeric = pd.to_numeric(pd.Series(values), errors="coerce").notna().mean()
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


def ingest_document(file_path: str, document_id: str) -> None:
    try:
        suffix = Path(file_path).suffix.lower()
        if suffix in {".csv", ".xlsx"}:
            tables = extract_spreadsheet_tables(file_path)
            if not tables:
                raise ValueError("No usable rows or columns were found in this spreadsheet.")
            db.insert_document_tables(document_id, tables)
            db.update_document_status(document_id, "ready", page_count=len(tables), chunk_count=0)
            return

        pages = extract_text(file_path)
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
    except Exception as e:
        logger.exception("Ingestion failed for document %s", document_id)
        db.update_document_status(document_id, "failed", error_message=str(e)[:500])


def ingest_webpage(url: str, document_id: str) -> None:
    """Ingest a readable webpage through the same chunk and embedding flow as a PDF."""
    try:
        text = _fetch_webpage(url)
        chunks = chunk_text([{"text": text, "page_number": 1}])
        if not chunks:
            raise ValueError("The webpage did not contain enough readable text to index.")
        embedded_chunks = embed_chunks(chunks)
        db.insert_chunks(document_id, embedded_chunks)
        db.update_document_status(document_id, "ready", page_count=1, chunk_count=len(embedded_chunks))
    except Exception as exc:
        logger.exception("Webpage ingestion failed for document %s", document_id)
        db.update_document_status(document_id, "failed", error_message=str(exc)[:500])


def _parse_youtube_id(url: str) -> str:
    """Extract the 11-character video ID from any common YouTube URL format."""
    import re
    patterns = [
        r"(?:v=|youtu\.be/|/embed/|/shorts/)([A-Za-z0-9_-]{11})",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    raise ValueError("Could not find a valid YouTube video ID in that URL.")


def ingest_youtube(url: str, document_id: str) -> None:
    """Fetch a YouTube transcript, merge it into paragraphs, then chunk and embed.
    Supports English, Hindi, and any available caption tracks or auto-generated captions.
    """
    try:
        from youtube_transcript_api import (  # type: ignore
            YouTubeTranscriptApi,
            TranscriptsDisabled,
            NoTranscriptFound,
            VideoUnavailable,
            VideoUnplayable,
            CouldNotRetrieveTranscript,
        )

        video_id = _parse_youtube_id(url)
        ytt = YouTubeTranscriptApi()

        # Preferred languages in priority order — covers English, Hindi, and regional languages.
        PREFERRED_LANGS = [
            "en", "en-IN", "en-US", "en-GB",
            "hi", "hi-IN",
            "ta", "te", "mr", "bn", "gu", "kn", "ml", "pa", "es", "fr", "de",
        ]

        segments = None
        used_lang = "unknown"

        try:
            tx_list = ytt.list(video_id)
            transcript = None

            # 1. Try finding transcript in preferred languages (manual or auto-generated)
            try:
                transcript = tx_list.find_transcript(PREFERRED_LANGS)
            except Exception:
                pass

            # 2. Try finding manually created transcript
            if transcript is None:
                try:
                    transcript = tx_list.find_manually_created_transcript(PREFERRED_LANGS)
                except Exception:
                    pass

            # 3. Try finding auto-generated transcript
            if transcript is None:
                try:
                    transcript = tx_list.find_generated_transcript(PREFERRED_LANGS)
                except Exception:
                    pass

            # 4. Fallback: pick the first available transcript of ANY language
            if transcript is None:
                for t in tx_list:
                    transcript = t
                    break

            if transcript is None:
                raise ValueError("No transcript is available for this video.")

            used_lang = getattr(transcript, "language_code", getattr(transcript, "language", "unknown"))
            logger.info(
                "Fetching YouTube transcript: video=%s lang=%s generated=%s",
                video_id,
                used_lang,
                getattr(transcript, "is_generated", "?"),
            )
            segments = transcript.fetch()

        except TranscriptsDisabled:
            raise ValueError("This video has transcripts disabled. Only videos with captions can be added.")
        except NoTranscriptFound:
            raise ValueError("No transcript was found for this video.")
        except (VideoUnavailable, VideoUnplayable):
            raise ValueError("This video is unavailable or private.")
        except CouldNotRetrieveTranscript as e:
            raise ValueError(f"Could not retrieve transcript: {e}")
        except ValueError:
            raise
        except Exception as fetch_err:
            logger.warning("Listing transcripts failed (%s), trying direct fetch shortcut", fetch_err)
            try:
                segments = ytt.fetch(video_id, languages=PREFERRED_LANGS)
                used_lang = "auto"
            except Exception as fallback_err:
                raise ValueError(
                    f"Could not fetch transcript for this video ({fallback_err}). "
                    "The video may be age-restricted, region-locked, or have no captions."
                ) from fallback_err

        if not segments:
            raise ValueError("The transcript was empty.")

        # Merge segments into ~120-second paragraph blocks for better retrieval context and quota efficiency.
        paragraphs: list[dict] = []
        current_text: list[str] = []
        current_start = 0.0
        block_duration = 120.0

        for seg in segments:
            if isinstance(seg, dict):
                raw_text = seg.get("text", "")
                start = float(seg.get("start", 0))
            else:
                raw_text = getattr(seg, "text", "")
                start = float(getattr(seg, "start", 0))

            text = raw_text.strip().replace("\n", " ")
            if not text:
                continue
            if not current_text:
                current_start = start
            current_text.append(text)
            if start - current_start >= block_duration:
                paragraphs.append({"text": " ".join(current_text), "page_number": len(paragraphs) + 1})
                current_text = []

        if current_text:
            paragraphs.append({"text": " ".join(current_text), "page_number": len(paragraphs) + 1})

        if not paragraphs:
            raise ValueError("The transcript was empty or could not be parsed.")

        chunks = chunk_text(paragraphs)
        if not chunks:
            raise ValueError("The transcript was too short to index.")

        embedded_chunks = embed_chunks(chunks)
        db.insert_chunks(document_id, embedded_chunks)
        db.update_document_status(
            document_id, "ready", page_count=len(paragraphs), chunk_count=len(embedded_chunks)
        )
        logger.info(
            "YouTube ingestion done: video=%s lang=%s paragraphs=%d chunks=%d",
            video_id, used_lang, len(paragraphs), len(embedded_chunks),
        )
    except Exception as exc:
        logger.exception("YouTube ingestion failed for document %s", document_id)
        db.update_document_status(document_id, "failed", error_message=str(exc)[:500])

