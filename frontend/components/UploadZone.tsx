"use client";

import { useCallback, useRef, useState } from "react";
import { ApiError, getDocumentStatus, uploadDocument } from "@/lib/api";
import type { Document } from "@/lib/types";

const ACCEPTED_TYPES: Record<string, string> = {
  "application/pdf": "PDF",
  "text/csv": "CSV",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "XLSX",
  "application/vnd.ms-excel": "XLS",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "DOCX",
  "text/plain": "TXT",
};

const ACCEPT_ATTR = Object.keys(ACCEPTED_TYPES).join(",");

export default function UploadZone({ onUploaded }: { onUploaded: (document: Document) => void }) {
  const [state, setState] = useState<"idle" | "uploading" | "processing" | "error">("idle");
  const [error, setError] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const pollStatus = useCallback(
    async (id: string) => {
      const poll = async (): Promise<void> => {
        const document = await getDocumentStatus(id);
        if (document.status === "ready") {
          setState("idle");
          onUploaded(document);
          return;
        }
        if (document.status === "failed") {
          setState("error");
          setError(document.error_message ?? "We couldn't prepare that document.");
          return;
        }
        window.setTimeout(poll, 1800);
      };
      poll();
    },
    [onUploaded]
  );

  const handleFile = useCallback(
    async (file: File) => {
      if (!ACCEPTED_TYPES[file.type]) {
        setState("error");
        setError("Unsupported file type. Please upload a PDF, CSV, XLSX, DOCX, or TXT file.");
        return;
      }
      setError(null);
      setState("uploading");
      try {
        const { document_id } = await uploadDocument(file);
        setState("processing");
        pollStatus(document_id);
      } catch (cause) {
        setState("error");
        setError(
          cause instanceof ApiError
            ? cause.message
            : "Upload failed. Check that the API is running."
        );
      }
    },
    [pollStatus]
  );

  const busy = state === "uploading" || state === "processing";

  return (
    <div>
      <div
        role="button"
        tabIndex={busy ? -1 : 0}
        onKeyDown={(e) => {
          if ((e.key === "Enter" || e.key === " ") && !busy) inputRef.current?.click();
        }}
        onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragOver(false);
          const file = e.dataTransfer.files?.[0];
          if (file) handleFile(file);
        }}
        onClick={() => !busy && inputRef.current?.click()}
        className={`upload-zone ${dragOver ? "is-over" : ""}`}
      >
        <input
          ref={inputRef}
          type="file"
          accept={ACCEPT_ATTR}
          className="hidden"
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) handleFile(file);
          }}
        />
        {busy ? (
          <p className="upload-progress">
            <i className="spinner" />
            {state === "uploading" ? "Uploading your source…" : "Preparing it for questions…"}
          </p>
        ) : (
          <>
            <span className="upload-icon">
              <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
                <path d="M12 16V3m0 0L7 8m5-5 5 5M4 14v5a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-5" />
              </svg>
            </span>
            <div className="upload-copy">
              <p className="upload-title">Add a source</p>
              <p className="upload-subtitle">PDF · CSV · XLSX · DOCX · TXT — drop or browse</p>
            </div>
          </>
        )}
      </div>
      {error && <p className="upload-error">{error}</p>}
    </div>
  );
}
