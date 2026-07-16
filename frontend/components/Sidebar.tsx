"use client";

import type { Document } from "@/lib/types";
import UploadZone from "./UploadZone";

interface Props { documents: Document[]; selectedId: string | null; onSelect: (document: Document) => void; onUploaded: (document: Document) => void; loading: boolean; open: boolean; }

function DocumentGlyph() { return <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6M8 13h8M8 17h5"/></svg>; }

export default function Sidebar({ documents, selectedId, onSelect, onUploaded, loading, open }: Props) {
  return <aside className={`sidebar ${open ? "is-open" : ""}`} aria-label="Document library">
    <div className="brand"><span className="brand-mark" aria-hidden="true"/><span className="brand-name">Lumen</span><span className="brand-tag">READER</span></div>
    <div className="sidebar-section"><span className="section-label">Bring in a source</span><UploadZone onUploaded={onUploaded} /></div>
    <div className="library">
      <div className="library-header"><span className="section-label" style={{ margin: 0 }}>Your library</span>{!loading && <span className="library-count">{documents.length}</span>}</div>
      {loading && <p className="empty-library">Opening your library…</p>}
      {!loading && documents.length === 0 && <p className="empty-library">Your first source will live here. Add a PDF to begin a focused conversation.</p>}
      <ul className="document-list">{documents.map((document) => <li key={document.id}><button onClick={() => document.status === "ready" && onSelect(document)} disabled={document.status !== "ready"} className={`document-button ${selectedId === document.id ? "is-selected" : ""}`}><span className="document-icon"><DocumentGlyph/></span><span className="document-copy"><span className="document-name">{document.filename}</span><span className="document-meta">{document.status === "ready" ? `${document.page_count ?? "–"} pages · ready` : document.status}</span></span><span className={`status-dot ${document.status}`} aria-label={document.status}/></button></li>)}</ul>
    </div>
    <div className="sidebar-footer"><span className="privacy-note"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><rect x="3" y="11" width="18" height="10" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>Your source stays in context</span></div>
  </aside>;
}
