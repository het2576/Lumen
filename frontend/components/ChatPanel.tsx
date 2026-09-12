"use client";

import { useEffect, useRef, useState } from "react";
import { ApiError, getConversationMessages, sendChatMessage } from "@/lib/api";
import type { ChatMessage, Document } from "@/lib/types";
import MessageBubble from "./MessageBubble";

const prompts = [
  ["01", "Give me the essential argument"],
  ["02", "What should I pay closest attention to?"],
  ["03", "Pull out the key decisions and risks"],
  ["04", "Explain this as a concise briefing"],
];

function SendGlyph() {
  return <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><path d="m22 2-7 20-4-9-9-4Z"/><path d="M22 2 11 13"/></svg>;
}

interface Props {
  documents: Document[];
  onRemoveDocument: (document: Document) => void;
  onOpenLibrary: () => void;
  resetSignal: number;
}

export default function ChatPanel({ documents, onRemoveDocument, onOpenLibrary, resetSignal }: Props) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [conversationId, setConversationId] = useState<string>();
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [restoring, setRestoring] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const initialResetSignal = useRef(resetSignal);
  const primaryDocument = documents[0];
  const documentIds = documents.map((document) => document.id);
  const sessionKey = documentIds.slice().sort().join(":");

  useEffect(() => {
    let cancelled = false;
    setMessages([]); setConversationId(undefined); setInput("");
    if (!sessionKey) return () => { cancelled = true; };
    const savedConversationId = window.localStorage.getItem(`lumen:conversation:${sessionKey}`);
    if (!savedConversationId) return () => { cancelled = true; };
    setRestoring(true);
    getConversationMessages(savedConversationId)
      .then((history) => {
        if (cancelled) return;
        const restoredIds = history.document_ids ?? [history.document_id];
        if (restoredIds.slice().sort().join(":") !== sessionKey) throw new Error("Conversation belongs to different sources.");
        setConversationId(history.conversation_id);
        setMessages(history.messages.map((message) => ({ ...message, role: message.role === "user" ? "user" : "assistant" })));
      })
      .catch(() => window.localStorage.removeItem(`lumen:conversation:${sessionKey}`))
      .finally(() => !cancelled && setRestoring(false));
    return () => { cancelled = true; };
  }, [sessionKey]);

  useEffect(() => {
    if (initialResetSignal.current === resetSignal) return;
    initialResetSignal.current = resetSignal;
    if (sessionKey) window.localStorage.removeItem(`lumen:conversation:${sessionKey}`);
    setMessages([]); setConversationId(undefined); setInput("");
    inputRef.current?.focus();
  }, [resetSignal, sessionKey]);

  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages]);

  async function handleSend(questionOverride?: string) {
    const question = (questionOverride ?? input).trim();
    if (!question || !primaryDocument || sending) return;
    const userMessage: ChatMessage = { id: crypto.randomUUID(), role: "user", content: question };
    const pendingMessage: ChatMessage = { id: crypto.randomUUID(), role: "assistant", content: "", pending: true };
    setMessages((previous) => [...previous, userMessage, pendingMessage]);
    setInput(""); setSending(true);
    try {
      const response = await sendChatMessage({ question, documentId: primaryDocument.id, documentIds, conversationId });
      setConversationId(response.conversation_id);
      window.localStorage.setItem(`lumen:conversation:${sessionKey}`, response.conversation_id);
      setMessages((previous) => previous.map((message) => message.id === pendingMessage.id
        ? { ...message, content: response.answer, sources: response.sources, verification: response.verification, chart: response.chart, pending: false }
        : message));
    } catch (cause) {
      const message = cause instanceof ApiError ? cause.message : "We couldn't reach your workspace. Check that the API is running, then try again.";
      setMessages((previous) => previous.map((item) => item.id === pendingMessage.id ? { ...item, content: message, pending: false, error: true } : item));
    } finally {
      setSending(false);
      inputRef.current?.focus();
    }
  }

  if (!primaryDocument) {
    return <section className="chat"><div className="empty-chat"><div><span className="empty-orb"><svg width="25" height="25" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="M12 3v18M3 12h18M5.6 5.6c4.4 4.4 8.4 8.4 12.8 12.8M18.4 5.6 5.6 18.4"/></svg></span><h2>A quiet place to think.</h2><p>Select a ready source from your library or add a PDF, CSV, or XLSX to start a grounded conversation.</p></div></div></section>;
  }

  const sourceLabel = documents.length === 1 ? primaryDocument.filename : `${documents.length} active sources`;
  return <section className="chat" aria-label="Document conversation">
    <div className="thread"><div className="thread-inner">
      {restoring ? <div className="conversation-loading"><i className="spinner"/><span>Restoring your conversation…</span></div>
        : messages.length === 0 ? <div className="welcome"><p className="eyebrow">In conversation with your sources</p><h1>Read beyond the <em>surface.</em></h1><p className="welcome-text">Ask for the thread running through {sourceLabel}. Every response is grounded and ready to inspect.</p><div className="prompt-grid">{prompts.map(([number, prompt]) => <button key={number} className="prompt-card" onClick={() => handleSend(prompt)}><span>{number}</span><strong>{prompt}</strong></button>)}</div></div>
          : <div className="messages">{messages.map((message) => <MessageBubble key={message.id} message={message}/>)}</div>}
      <div ref={bottomRef}/>
    </div></div>
    <div className="composer-area"><div className="composer-wrap">
      <div className="active-sources" aria-label="Active sources">{documents.map((document) => <span className="active-source" key={document.id}>{document.filename}<button onClick={() => onRemoveDocument(document)} aria-label={`Remove ${document.filename}`}>×</button></span>)}<button className="active-source-add" onClick={onOpenLibrary}>+ Add source</button></div>
      <div className="composer"><button className="attach-button" aria-label="Open document library" title="Open document library" onClick={onOpenLibrary}><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"><path d="M12 5v14M5 12h14"/></svg></button><textarea ref={inputRef} value={input} rows={1} onChange={(event) => { setInput(event.target.value); event.currentTarget.style.height = "auto"; event.currentTarget.style.height = `${Math.min(event.currentTarget.scrollHeight, 150)}px`; }} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); handleSend(); } }} placeholder="Ask anything about these sources…" disabled={sending} className="composer-input" aria-label="Your question"/><button onClick={() => handleSend()} disabled={sending || !input.trim()} className="send-button" aria-label="Send question"><SendGlyph/></button></div>
      <div className="composer-caption"><span><i className="grounded-dot"/>Grounded in {sourceLabel}</span><span><b className="keycap">Enter</b> to send <b className="keycap">Shift</b> <b className="keycap">Enter</b> for a new line</span></div>
    </div></div>
  </section>;
}
