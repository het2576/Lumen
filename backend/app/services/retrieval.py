from app.config import EMBEDDING_DIM, EMBEDDING_MODEL, KEYWORD_WEIGHT, SEMANTIC_WEIGHT, TOP_K
from app.db import get_client
from app.services.gemini_client import get_genai


def embed_query(question: str) -> list[float]:
    genai = get_genai()
    result = genai.embed_content(
        model=EMBEDDING_MODEL,
        content=question,
        task_type="RETRIEVAL_QUERY",
        output_dimensionality=EMBEDDING_DIM,
    )
    return result["embedding"]


def semantic_search(document_id: str, query_embedding: list[float], top_k: int = TOP_K) -> list[dict]:
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


def keyword_search(document_id: str, question: str, top_k: int = TOP_K) -> list[dict]:
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


def hybrid_search(document_id: str, question: str, top_k: int = TOP_K) -> list[dict]:
    query_embedding = embed_query(question)
    semantic_results = semantic_search(document_id, query_embedding, top_k=top_k)
    keyword_results = keyword_search(document_id, question, top_k=top_k)

    merged: dict[str, dict] = {}
    for r in semantic_results:
        merged[r["chunk_id"]] = {**r, "keyword_score": 0.0}
    for r in keyword_results:
        if r["chunk_id"] in merged:
            merged[r["chunk_id"]]["keyword_score"] = r["keyword_score"]
        else:
            merged[r["chunk_id"]] = {**r, "semantic_score": 0.0}

    scored = []
    for chunk in merged.values():
        combined = SEMANTIC_WEIGHT * chunk.get("semantic_score", 0.0) + KEYWORD_WEIGHT * chunk.get(
            "keyword_score", 0.0
        )
        scored.append({**chunk, "similarity_score": combined})

    scored.sort(key=lambda c: c["similarity_score"], reverse=True)
    return scored[:top_k]
