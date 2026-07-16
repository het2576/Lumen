"use client";

import { useEffect, useState } from "react";
import ChatPanel from "@/components/ChatPanel";
import Sidebar from "@/components/Sidebar";
import StatsBar from "@/components/StatsBar";
import { listDocuments } from "@/lib/api";
import type { Document } from "@/lib/types";

export default function Home() {
  const [documents, setDocuments] = useState<Document[]>([]);
  const [selected, setSelected] = useState<Document | null>(null);
  const [loading, setLoading] = useState(true);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [chatSession, setChatSession] = useState(0);

  useEffect(() => {
    listDocuments()
      .then((docs) => {
        setDocuments(docs);
        setSelected(docs.find((document) => document.status === "ready") ?? null);
      })
      .catch(() => undefined)
      .finally(() => setLoading(false));
  }, []);

  function handleUploaded(document: Document) {
    setDocuments((previous) => {
      const exists = previous.some((item) => item.id === document.id);
      return exists ? previous.map((item) => (item.id === document.id ? document : item)) : [document, ...previous];
    });
    setSelected(document);
    setSidebarOpen(false);
  }

  function handleSelect(document: Document) {
    setSelected(document);
    setSidebarOpen(false);
  }

  return (
    <main className="app-shell">
      <div className={`sidebar-scrim ${sidebarOpen ? "is-visible" : ""}`} onClick={() => setSidebarOpen(false)} />
      <Sidebar documents={documents} selectedId={selected?.id ?? null} onSelect={handleSelect} onUploaded={handleUploaded} loading={loading} open={sidebarOpen} />
      <section className="workspace">
        <StatsBar document={selected} onMenu={() => setSidebarOpen(true)} onNewChat={() => setChatSession((session) => session + 1)} />
        <ChatPanel document={selected} resetSignal={chatSession} onOpenLibrary={() => setSidebarOpen(true)} />
      </section>
    </main>
  );
}
