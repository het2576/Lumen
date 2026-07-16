export type DocumentStatus = "processing" | "ready" | "failed";

export interface Document {
  id: string;
  filename: string;
  uploaded_at: string;
  status: DocumentStatus;
  chunk_count?: number;
  page_count?: number;
  error_message?: string | null;
}

export interface Source {
  chunk_id: string;
  text: string;
  page_number: number | null;
  similarity_score: number;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  sources?: Source[];
  pending?: boolean;
  error?: boolean;
  created_at?: string;
}

export interface ChatResponse {
  conversation_id: string;
  answer: string;
  sources: Source[];
}

export interface ConversationHistory {
  conversation_id: string;
  document_id: string;
  messages: ChatMessage[];
}

export interface Stats {
  total_documents: number;
  total_chunks: number;
  avg_similarity_last_10: number | null;
  total_queries: number;
}
