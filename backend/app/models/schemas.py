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


class SourceOut(BaseModel):
    chunk_id: str
    text: str
    page_number: Optional[int]
    similarity_score: float


class ChatRequest(BaseModel):
    document_id: str
    question: str
    conversation_id: Optional[str] = None


class ChatResponseOut(BaseModel):
    conversation_id: str
    answer: str
    sources: list[SourceOut]


class ConversationMessageOut(BaseModel):
    id: str
    role: str
    content: str
    created_at: str


class ConversationHistoryOut(BaseModel):
    conversation_id: str
    document_id: str
    messages: list[ConversationMessageOut]


class StatsOut(BaseModel):
    total_documents: int
    total_chunks: int
    avg_similarity_last_10: Optional[float]
    total_queries: int
