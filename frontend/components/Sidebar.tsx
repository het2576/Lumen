"use client";

import { useState } from "react";
import { ApiError, deleteDocument } from "@/lib/api";
import type { Document } from "@/lib/types";
import UploadZone from "./UploadZone";

interface Props {
  documents: Document[];
  selectedId: string | null;
  selectedIds?: string[];
  onSelect: (document: Document) => void;
  onMultiSelect?: (ids: string[]) => void;
  onUploaded: (document: Document) => void;
  onDeleted: (documentId: string) => void;
  loading: boolean;
  open: boolean;
  collapsed?: boolean;
  onToggleCollapse?: () => void;
  userEmail: string | null;
  onSignOut: () => void;
}

function DocumentGlyph() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true">
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
      <path d="M14 2v6h6M8 13h8M8 17h5" />
    </svg>
  );
}

function TrashGlyph() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true">
      <path d="M4 7h16M9 7V4h6v3M6 7l1 13a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-13" />
      <path d="M10 11v6M14 11v6" />
    </svg>
  );
}

function CollapseGlyph({ collapsed }: { collapsed: boolean }) {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"
      style={{ transform: collapsed ? "rotate(180deg)" : "rotate(0deg)", transition: "transform 0.2s ease" }}>
      <path d="M15 18l-6-6 6-6" />
    </svg>
  );
}

export default function Sidebar({
  documents,
  selectedId,
  selectedIds = [],
  onSelect,
  onMultiSelect,
  onUploaded,
  onDeleted,
  loading,
  open,
  collapsed = false,
  onToggleCollapse,
  userEmail,
  onSignOut,
}: Props) {
  const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [multiSelectMode, setMultiSelectMode] = useState(false);
  const [localSelectedIds, setLocalSelectedIds] = useState<string[]>([]);

  const activeSelectedIds = selectedIds.length > 0 ? selectedIds : localSelectedIds;

  async function handleDelete(document: Document) {
    setDeletingId(document.id);
    setDeleteError(null);
    try {
      await deleteDocument(document.id);
      onDeleted(document.id);
      setPendingDeleteId(null);
    } catch (cause) {
      setDeleteError(cause instanceof ApiError ? cause.message : "Couldn't delete this source.");
    } finally {
      setDeletingId(null);
    }
  }

  function toggleMultiSelect(docId: string) {
    const next = activeSelectedIds.includes(docId)
      ? activeSelectedIds.filter((id) => id !== docId)
      : [...activeSelectedIds, docId];
    setLocalSelectedIds(next);
    onMultiSelect?.(next);
  }

  function handleDocumentClick(document: Document) {
    if (document.status !== "ready") return;
    if (multiSelectMode) {
      toggleMultiSelect(document.id);
    } else {
      onSelect(document);
    }
  }

  return (
    <aside className={`sidebar ${open ? "is-open" : ""} ${collapsed ? "is-collapsed" : ""}`} aria-label="Document library">
      {/* Brand + collapse button */}
      <div className="brand">
        <span className="brand-mark" aria-hidden="true" />
        {!collapsed && <span className="brand-name">Lumen</span>}
        {!collapsed && <span className="brand-tag">READER</span>}
        {onToggleCollapse && (
          <button
            className="sidebar-collapse-btn"
            onClick={onToggleCollapse}
            title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          >
            <CollapseGlyph collapsed={collapsed} />
          </button>
        )}
      </div>

      {!collapsed && (
        <>
          {/* Upload zone */}
          <div className="sidebar-section">
            <span className="section-label">Bring in a source</span>
            <UploadZone onUploaded={onUploaded} />
          </div>

          {/* Library */}
          <div className="library">
            <div className="library-header">
              <span className="section-label" style={{ margin: 0 }}>Your library</span>
              <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                {!loading && <span className="library-count">{documents.length}</span>}
                {documents.length > 1 && (
                  <button
                    className={`multi-select-btn ${multiSelectMode ? "active" : ""}`}
                    onClick={() => {
                      setMultiSelectMode((v) => !v);
                      setLocalSelectedIds([]);
                      onMultiSelect?.([]);
                    }}
                    title="Multi-select documents"
                  >
                    {multiSelectMode ? "Done" : "Select"}
                  </button>
                )}
              </div>
            </div>

            {/* Multi-select banner */}
            {multiSelectMode && activeSelectedIds.length > 0 && (
              <div className="multi-select-banner">
                <span>{activeSelectedIds.length} selected</span>
                <button
                  className="multi-select-clear"
                  onClick={() => { setLocalSelectedIds([]); onMultiSelect?.([]); }}
                >
                  Clear
                </button>
              </div>
            )}

            {loading && <p className="empty-library">Opening your library…</p>}
            {!loading && documents.length === 0 && (
              <p className="empty-library">
                Your first source will live here. Add a PDF, CSV, or DOCX to begin.
              </p>
            )}

            <ul className="document-list">
              {documents.map((document) => {
                const isPendingDelete = pendingDeleteId === document.id;
                const isDeleting = deletingId === document.id;
                const isChecked = activeSelectedIds.includes(document.id);

                return (
                  <li key={document.id}>
                    {isPendingDelete ? (
                      <div className="document-confirm">
                        <span>Delete "{document.filename}"?</span>
                        <div className="document-confirm-actions">
                          <button
                            className="document-confirm-cancel"
                            disabled={isDeleting}
                            onClick={() => { setPendingDeleteId(null); setDeleteError(null); }}
                          >
                            Cancel
                          </button>
                          <button
                            className="document-confirm-delete"
                            disabled={isDeleting}
                            onClick={() => handleDelete(document)}
                          >
                            {isDeleting ? "Deleting…" : "Delete"}
                          </button>
                        </div>
                      </div>
                    ) : (
                      <div className={`document-row ${selectedId === document.id && !multiSelectMode ? "is-selected" : ""} ${isChecked ? "is-checked" : ""}`}>
                        {/* Multi-select checkbox */}
                        {multiSelectMode && document.status === "ready" && (
                          <button
                            className={`doc-checkbox ${isChecked ? "checked" : ""}`}
                            onClick={() => toggleMultiSelect(document.id)}
                            aria-label={`${isChecked ? "Deselect" : "Select"} ${document.filename}`}
                          >
                            {isChecked && (
                              <svg width="9" height="9" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="2.5">
                                <path d="M2 6l3 3 5-5" />
                              </svg>
                            )}
                          </button>
                        )}

                        <button
                          onClick={() => handleDocumentClick(document)}
                          disabled={document.status !== "ready"}
                          className="document-button"
                        >
                          <span className="document-icon"><DocumentGlyph /></span>
                          <span className="document-copy">
                            <span className="document-name">{document.filename}</span>
                            <span className="document-meta">
                              {document.status === "ready"
                                ? `${document.page_count ?? "–"} pages · ready`
                                : document.status}
                            </span>
                          </span>
                          <span className={`status-dot ${document.status}`} aria-label={document.status} />
                        </button>

                        {!multiSelectMode && (
                          <button
                            className="document-delete"
                            aria-label={`Delete ${document.filename}`}
                            title="Delete source"
                            onClick={() => { setPendingDeleteId(document.id); setDeleteError(null); }}
                          >
                            <TrashGlyph />
                          </button>
                        )}
                      </div>
                    )}
                    {isPendingDelete && deleteError && (
                      <p className="document-delete-error">{deleteError}</p>
                    )}
                  </li>
                );
              })}
            </ul>
          </div>

          {/* Footer */}
          <div className="sidebar-footer">
            {userEmail && (
              <div className="user-chip">
                <span className="user-avatar">{userEmail.slice(0, 1)}</span>
                <span className="user-email">{userEmail}</span>
                <button className="sign-out-button" onClick={onSignOut}>Sign out</button>
              </div>
            )}
            <span className="privacy-note">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                <rect x="3" y="11" width="18" height="10" rx="2" />
                <path d="M7 11V7a5 5 0 0 1 10 0v4" />
              </svg>
              Your source stays in context
            </span>
          </div>
        </>
      )}
    </aside>
  );
}
