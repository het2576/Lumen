from typing import Optional

from pydantic import BaseModel


class DocumentOut(BaseModel):
    id: str
    filename: str
    uploaded_at: str
    status: str
    page_count: Optional[int] = None
    chunk_count: Optional[int] = None
    error_message: Optional[str] = None


class UploadResponse(BaseModel):
    document_id: str
    status: str


class UrlIngestRequest(BaseModel):
    url: str


class SourceOut(BaseModel):
    chunk_id: str
    text: str
    page_number: Optional[int]
    similarity_score: float
    document_id: Optional[str] = None
    document_name: Optional[str] = None
    table_name: Optional[str] = None
    verification: Optional[str] = None


class ChatRequest(BaseModel):
    document_id: str
    document_ids: Optional[list[str]] = None
    question: str
    conversation_id: Optional[str] = None


class ChatResponseOut(BaseModel):
    conversation_id: str
    answer: str
    sources: list[SourceOut]
    verification: Optional[str] = None
    chart: Optional[dict] = None


class ConversationMessageOut(BaseModel):
    id: str
    role: str
    content: str
    created_at: str


class ConversationHistoryOut(BaseModel):
    conversation_id: str
    document_id: str
    document_ids: list[str] = []
    messages: list[ConversationMessageOut]


class StatsOut(BaseModel):
    total_documents: int
    total_chunks: int
    avg_similarity_last_10: Optional[float]
    total_queries: int
