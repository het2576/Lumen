"use client";

import { useEffect, useState } from "react";
import { getStats } from "@/lib/api";
import type { Document, Stats } from "@/lib/types";

function FileGlyph() { return <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/></svg>; }

export default function StatsBar({ document, onMenu, onNewChat }: { document: Document | null; onMenu: () => void; onNewChat: () => void }) {
  const [stats, setStats] = useState<Stats | null>(null);
  useEffect(() => { getStats().then(setStats).catch(() => undefined); }, []);
  return <header className="topbar">
    <div className="context"><button className="menu-button" onClick={onMenu} aria-label="Open document library"><svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"><path d="M4 7h16M4 12h16M4 17h16"/></svg></button><span className="context-file"><FileGlyph/></span><div><p className="context-label">Active source</p><p className="context-title">{document?.filename ?? "No document selected"}</p></div></div>
    <div className="header-actions"><div className="health"><span><strong>{stats?.total_documents ?? "–"}</strong> sources</span><span className="health-separator"/><span><strong>{stats?.total_queries ?? "–"}</strong> questions</span><span className="health-separator"/><span className="live-pill"><i className="live-pulse"/>Ready</span></div><button className="new-chat-button" onClick={onNewChat} disabled={!document}><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg><span>New chat</span></button></div>
  </header>;
}
