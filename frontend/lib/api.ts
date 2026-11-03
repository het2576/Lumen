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

export async function uploadUrl(url: string): Promise<{ document_id: string; status: string }> {
  const res = await fetch(`${API_URL}/documents/url`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...(await authHeaders()) },
    body: JSON.stringify({ url }),
  });
  return handle(res);
}

export async function uploadYoutube(url: string): Promise<{ document_id: string; status: string }> {
  const res = await fetch(`${API_URL}/documents/youtube`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...(await authHeaders()) },
    body: JSON.stringify({ url }),
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
  documentIds?: string[];
  conversationId?: string;
}): Promise<ChatResponse> {
  const res = await fetch(`${API_URL}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...(await authHeaders()) },
    body: JSON.stringify({
      document_id: params.documentId,
      document_ids: params.documentIds,
      conversation_id: params.conversationId,
      question: params.question,
    }),
  });
  return handle(res);
}

type StreamEvent =
  | { type: "meta"; conversation_id: string }
  | { type: "delta"; text: string }
  | ({ type: "done" } & ChatResponse)
  | { type: "error"; detail: string };

/**
 * Ask a question and receive the answer as it is written (Server-Sent Events over a POST;
 * EventSource can't send a body or auth header, so the stream is read by hand).
 * onDelta gets each new piece of text; the promise resolves with the final response.
 */
export async function streamChatMessage(
  params: { question: string; documentId: string; documentIds?: string[]; conversationId?: string },
  handlers: { onMeta?: (conversationId: string) => void; onDelta?: (text: string) => void } = {},
): Promise<ChatResponse> {
  const res = await fetch(`${API_URL}/chat/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream", ...(await authHeaders()) },
    body: JSON.stringify({
      document_id: params.documentId,
      document_ids: params.documentIds,
      conversation_id: params.conversationId,
      question: params.question,
    }),
  });
  if (!res.ok || !res.body) return handle(res);

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let final: ChatResponse | undefined;
  for (;;) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value, { stream: !done });
    // Events are separated by a blank line; keep any incomplete tail for the next read.
    const blocks = buffer.split("\n\n");
    buffer = done ? "" : blocks.pop() ?? "";
    for (const block of blocks) {
      const data = block.split("\n").filter((line) => line.startsWith("data:")).map((line) => line.slice(5).trimStart()).join("\n");
      if (!data) continue;
      const event = JSON.parse(data) as StreamEvent;
      if (event.type === "meta") handlers.onMeta?.(event.conversation_id);
      else if (event.type === "delta") handlers.onDelta?.(event.text);
      else if (event.type === "error") throw new ApiError(event.detail);
      else if (event.type === "done") final = event;
    }
    if (done) break;
  }
  if (!final) throw new ApiError("The answer was interrupted. Please try again.");
  return final;
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
