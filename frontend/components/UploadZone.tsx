"use client";

import { useCallback, useRef, useState } from "react";
import { ApiError, getDocumentStatus, uploadDocument, uploadUrl, uploadYoutube } from "@/lib/api";
import type { Document } from "@/lib/types";

const ACCEPTED_SUFFIXES = [".pdf", ".csv", ".xlsx"];

function isYouTubeUrl(url: string): boolean {
  try {
    const { hostname } = new URL(url);
    return ["www.youtube.com", "youtube.com", "youtu.be", "m.youtube.com"].includes(hostname);
  } catch {
    return false;
  }
}

export default function UploadZone({ onUploaded }: { onUploaded: (document: Document) => void }) {
  const [state, setState] = useState<"idle" | "uploading" | "processing" | "error">("idle");
  const [error, setError] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const [url, setUrl] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  const pollStatus = useCallback(async (id: string) => {
    const poll = async (): Promise<void> => {
      try {
        const document = await getDocumentStatus(id);
        if (document.status === "ready") { setState("idle"); onUploaded(document); return; }
        if (document.status === "failed") { setState("error"); setError(document.error_message ?? "We couldn't prepare that source."); return; }
        window.setTimeout(poll, 1800);
      } catch {
        setState("error"); setError("We couldn't check the source status. Please refresh your library.");
      }
    };
    poll();
  }, [onUploaded]);

  const handleFile = useCallback(async (file: File) => {
    if (!ACCEPTED_SUFFIXES.some((suffix) => file.name.toLowerCase().endsWith(suffix))) {
      setState("error"); setError("Choose a PDF, CSV, or XLSX file to add it to your library."); return;
    }
    setError(null); setState("uploading");
    try {
      const { document_id } = await uploadDocument(file);
      setState("processing"); pollStatus(document_id);
    } catch (cause) {
      setState("error"); setError(cause instanceof ApiError ? cause.message : "Upload failed. Check that the API is running.");
    }
  }, [pollStatus]);

  async function handleUrl(event: React.FormEvent) {
    event.preventDefault();
    const value = url.trim();
    if (!value) return;
    setError(null); setState("uploading");
    try {
      const fn = isYouTubeUrl(value) ? uploadYoutube : uploadUrl;
      const { document_id } = await fn(value);
      setUrl(""); setState("processing"); pollStatus(document_id);
    } catch (cause) {
      setState("error"); setError(cause instanceof ApiError ? cause.message : "Couldn't add that source.");
    }
  }

  const busy = state === "uploading" || state === "processing";
  return <div>
    <div role="button" tabIndex={busy ? -1 : 0} onKeyDown={(event) => { if ((event.key === "Enter" || event.key === " ") && !busy) inputRef.current?.click(); }} onDragOver={(event) => { event.preventDefault(); setDragOver(true); }} onDragLeave={() => setDragOver(false)} onDrop={(event) => { event.preventDefault(); setDragOver(false); const file = event.dataTransfer.files?.[0]; if (file) handleFile(file); }} onClick={() => !busy && inputRef.current?.click()} className={`upload-zone ${dragOver ? "is-over" : ""}`}>
      <input ref={inputRef} type="file" accept=".pdf,.csv,.xlsx,application/pdf,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" className="hidden" onChange={(event) => { const file = event.target.files?.[0]; if (file) handleFile(file); event.currentTarget.value = ""; }} />
      {busy ? <p className="upload-progress"><i className="spinner"/>{state === "uploading" ? "Adding your source…" : "Preparing it for questions…"}</p> : <><span className="upload-icon"><svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"><path d="M12 16V3m0 0L7 8m5-5 5 5M4 14v5a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-5"/></svg></span><div className="upload-copy"><p className="upload-title">Add a source</p><p className="upload-subtitle">PDF · CSV · XLSX — drop or browse</p></div></>}
    </div>
    {!busy && <form className="url-source" onSubmit={handleUrl}><div className="url-source-heading"><span className="url-source-icon"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.3 2.5 3.5 5.5 3.5 9S14.3 18.5 12 21c-2.3-2.5-3.5-5.5-3.5-9S9.7 5.5 12 3"/></svg></span><span><strong>Add a link</strong><small>Paste an article or YouTube video URL</small></span></div><div className="url-form"><input className="url-input" type="url" value={url} onChange={(event) => setUrl(event.target.value)} placeholder="https://youtube.com/watch?v=… or article URL" aria-label="Webpage or YouTube URL" required/><button className="url-submit" type="submit">Add</button></div></form>}
    {error && <p className="upload-error">{error}</p>}
  </div>;
}

