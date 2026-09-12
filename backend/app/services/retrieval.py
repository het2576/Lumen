import logging
from app.config import KEYWORD_WEIGHT, SEMANTIC_WEIGHT, TOP_K
from app.db import get_client
from app.services.embeddings import embed_texts

logger = logging.getLogger(__name__)


def embed_query(question: str) -> list[float]:
    return embed_texts([question], task_type="RETRIEVAL_QUERY")[0]


def semantic_search(document_id: str, query_embedding: list[float], top_k: int = TOP_K) -> list[dict]:
    try:
        res = get_client().rpc(
            "match_document_chunks",
            {"p_document_id": document_id, "query_embedding": query_embedding, "match_count": top_k},
        ).execute()
        return [
            {
                "chunk_id": row["id"],
                "text": row["chunk_text"],
                "chunk_index": row["chunk_index"],
                "page_number": row["page_number"],
                "semantic_score": row["similarity"],
            }
            for row in res.data
        ]
    except Exception as exc:
        logger.warning("semantic_search RPC error for %s: %s", document_id, exc)
        return []


def keyword_search(document_id: str, question: str, top_k: int = TOP_K) -> list[dict]:
    try:
        res = get_client().rpc(
            "keyword_search_document_chunks",
            {"p_document_id": document_id, "search_query": question, "match_count": top_k},
        ).execute()
        if not res.data:
            return []
        max_rank = max((row["rank"] for row in res.data), default=0) or 1.0
        return [
            {
                "chunk_id": row["id"],
                "text": row["chunk_text"],
                "chunk_index": row["chunk_index"],
                "page_number": row["page_number"],
                "keyword_score": row["rank"] / max_rank,
            }
            for row in res.data
        ]
    except Exception as exc:
        logger.warning("keyword_search RPC error for %s: %s", document_id, exc)
        return []


def hybrid_search(
    document_id: str,
    question: str,
    top_k: int = TOP_K,
    query_embedding: list[float] | None = None,
) -> list[dict]:
    """Search one document. The caller embeds the question once and shares the
    vector across documents; None means semantic search is unavailable for this
    request and the search degrades to keyword only.
    """
    semantic_results = semantic_search(document_id, query_embedding, top_k=top_k) if query_embedding else []

    keyword_results = keyword_search(document_id, question, top_k=top_k)

    # If neither returned results, try matching by any individual words or return empty
    if not semantic_results and not keyword_results:
        return []

    merged: dict[str, dict] = {}
    for r in semantic_results:
        merged[r["chunk_id"]] = {**r, "keyword_score": 0.0}
    for r in keyword_results:
        if r["chunk_id"] in merged:
            merged[r["chunk_id"]]["keyword_score"] = r["keyword_score"]
        else:
            merged[r["chunk_id"]] = {**r, "semantic_score": 0.0}

    # Weight dynamically if semantic search was unavailable
    sem_weight = SEMANTIC_WEIGHT if semantic_results else 0.0
    kw_weight = KEYWORD_WEIGHT if semantic_results else 1.0

    scored = []
    for chunk in merged.values():
        combined = sem_weight * chunk.get("semantic_score", 0.0) + kw_weight * chunk.get(
            "keyword_score", 0.0
        )
        scored.append({**chunk, "similarity_score": combined})

    scored.sort(key=lambda c: c["similarity_score"], reverse=True)
    return scored[:top_k]
