import { supabase } from "./supabaseClient";
import type { ChatResponse, ConversationHistory, Document, Stats } from "./types";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

class ApiError extends Error {
  constructor(message: string, public status?: number) {
    super(message);
    this.name = "ApiError";
  }
}

async function authHeaders(): Promise<Record<string, string>> {
  const { data } = await supabase.auth.getSession();
  const token = data.session?.access_token;
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function handle<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail ?? detail;
    } catch {
      // ignore non-JSON error bodies
    }
    throw new ApiError(detail, res.status);
  }
  return res.json();
}

export async function uploadDocument(file: File): Promise<{ document_id: string; status: string }> {
  const formData = new FormData();
  formData.append("file", file);
  const res = await fetch(`${API_URL}/documents/upload`, {
    method: "POST",
    headers: await authHeaders(),
    body: formData,
  });
  return handle(res);
}

export async function getDocumentStatus(documentId: string): Promise<Document> {
  const res = await fetch(`${API_URL}/documents/${documentId}/status`, { headers: await authHeaders() });
  return handle(res);
}

export async function listDocuments(): Promise<Document[]> {
  const res = await fetch(`${API_URL}/documents`, { headers: await authHeaders() });
  return handle(res);
}

export async function sendChatMessage(params: {
  question: string;
  documentId: string;
  conversationId?: string;
}): Promise<ChatResponse> {
  const res = await fetch(`${API_URL}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...(await authHeaders()) },
    body: JSON.stringify({
      document_id: params.documentId,
      conversation_id: params.conversationId,
      question: params.question,
    }),
  });
  return handle(res);
}

export async function getConversationMessages(conversationId: string): Promise<ConversationHistory> {
  const res = await fetch(`${API_URL}/chat/${conversationId}/messages`, { headers: await authHeaders() });
  return handle(res);
}

export async function deleteDocument(documentId: string): Promise<void> {
  const res = await fetch(`${API_URL}/documents/${documentId}`, {
    method: "DELETE",
    headers: await authHeaders(),
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail ?? detail;
    } catch {
      // ignore non-JSON error bodies
    }
    throw new ApiError(detail, res.status);
  }
}

export async function getStats(): Promise<Stats> {
  const res = await fetch(`${API_URL}/stats`, { headers: await authHeaders() });
  return handle(res);
}

export { ApiError };
