"use client";

import { useState } from "react";
import type { Source } from "@/lib/types";
import SimilarityBadge from "./SimilarityBadge";

export default function SourceList({ sources }: { sources: Source[] }) {
  const [open, setOpen] = useState(false); const [expandedId, setExpandedId] = useState<string | null>(null);
  if (!sources.length) return null;
  return <div className="sources"><button onClick={() => setOpen((value) => !value)} className="sources-toggle"><span className={`sources-chevron ${open ? "open" : ""}`}>›</span>{sources.length} {sources.length === 1 ? "source" : "sources"} inspected</button>{open && <ul className="source-list">{sources.map((source, index) => { const expanded = expandedId === source.chunk_id; return <li key={source.chunk_id} className="source-card"><button className="source-button" onClick={() => setExpandedId(expanded ? null : source.chunk_id)}><span className="source-reference"><b>{String(index + 1).padStart(2, "0")}</b><span className="source-page">{source.page_number != null ? `Page ${source.page_number}` : "Source excerpt"}</span></span><SimilarityBadge score={source.similarity_score}/></button>{expanded && <p className="source-text">{source.text}</p>}</li>; })}</ul>}</div>;
}
