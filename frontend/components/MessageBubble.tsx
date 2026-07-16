import type { ChatMessage } from "@/lib/types";
import { useState } from "react";
import AnswerContent from "./AnswerContent";
import SourceList from "./SourceList";

function LumenGlyph() { return <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true"><path d="M12 3v18M3 12h18M5.6 5.6l12.8 12.8M18.4 5.6 5.6 18.4"/></svg>; }

export default function MessageBubble({ message }: { message: ChatMessage }) {
  const isUser = message.role === "user";
  const [copied, setCopied] = useState(false);
  async function copyAnswer() { await navigator.clipboard?.writeText(message.content); setCopied(true); window.setTimeout(() => setCopied(false), 1800); }
  return <article className={`message ${isUser ? "user" : ""} ${message.error ? "error" : ""}`}><span className="message-avatar">{isUser ? <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true"><circle cx="12" cy="8" r="3.5"/><path d="M4.5 21a7.5 7.5 0 0 1 15 0"/></svg> : <LumenGlyph/>}</span><div className="message-body"><div className="message-byline"><strong>{isUser ? "You" : "Lumen"}</strong>{!isUser && !message.pending && <span>grounded answer</span>}</div><div className="message-content">{message.pending ? <span className="typing" aria-label="Lumen is thinking"><i/><i/><i/></span> : isUser || message.error ? message.content : <AnswerContent content={message.content}/>}</div>{!isUser && !message.pending && !message.error && <div className="response-actions"><button onClick={copyAnswer} className="response-action" aria-label="Copy answer"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>{copied ? "Copied" : "Copy answer"}</button><span className="response-divider"/><span className="response-status"><i/>Sources checked</span></div>}{!isUser && message.sources && <SourceList sources={message.sources}/>}</div></article>;
}
