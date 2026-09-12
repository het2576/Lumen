"use client";

import { useEffect, useState } from "react";
import ChatPanel from "@/components/ChatPanel";
import LoginScreen from "@/components/LoginScreen";
import Sidebar from "@/components/Sidebar";
import StatsBar from "@/components/StatsBar";
import { useAuth } from "@/lib/AuthProvider";
import { listDocuments } from "@/lib/api";
import type { Document } from "@/lib/types";

export default function Home() {
  const { user, loading: authLoading, signOut } = useAuth();
  const [documents, setDocuments] = useState<Document[]>([]);
  const [activeDocuments, setActiveDocuments] = useState<Document[]>([]);
  const [loading, setLoading] = useState(true);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [chatSession, setChatSession] = useState(0);

  useEffect(() => {
    if (!user) return;
    setLoading(true);
    listDocuments()
      .then((docs) => {
        setDocuments(docs);
        const firstReady = docs.find((document) => document.status === "ready");
        setActiveDocuments(firstReady ? [firstReady] : []);
      })
      .catch(() => undefined)
      .finally(() => setLoading(false));
  }, [user]);

  function handleUploaded(document: Document) {
    setDocuments((previous) => {
      const exists = previous.some((item) => item.id === document.id);
      return exists ? previous.map((item) => (item.id === document.id ? document : item)) : [document, ...previous];
    });
    setActiveDocuments((previous) => previous.some((item) => item.id === document.id) ? previous : [...previous, document]);
    setSidebarOpen(false);
  }

  function handleSelect(document: Document) {
    setActiveDocuments((previous) => previous.some((item) => item.id === document.id) ? [document, ...previous.filter((item) => item.id !== document.id)] : [document]);
    setSidebarOpen(false);
  }

  function handleDeleted(documentId: string) {
    setDocuments((previous) => previous.filter((item) => item.id !== documentId));
    setActiveDocuments((previous) => previous.filter((item) => item.id !== documentId));
    window.localStorage.removeItem(`lumen:conversation:${documentId}`);
  }

  function handleToggleActive(document: Document) {
    setActiveDocuments((previous) => previous.some((item) => item.id === document.id)
      ? previous.filter((item) => item.id !== document.id)
      : [...previous, document]);
    setChatSession((session) => session + 1);
  }

  if (authLoading) {
    return <div className="auth-loading">Loading…</div>;
  }

  if (!user) {
    return <LoginScreen />;
  }

  return (
    <main className={`app-shell ${sidebarCollapsed ? "is-sidebar-collapsed" : ""}`}>
      <div className={`sidebar-scrim ${sidebarOpen ? "is-visible" : ""}`} onClick={() => setSidebarOpen(false)} />
      <Sidebar
        documents={documents}
        selectedId={activeDocuments[0]?.id ?? null}
        activeIds={activeDocuments.map((document) => document.id)}
        onSelect={handleSelect}
        onToggleActive={handleToggleActive}
        onUploaded={handleUploaded}
        onDeleted={handleDeleted}
        loading={loading}
        open={sidebarOpen}
        collapsed={sidebarCollapsed}
        onToggleCollapse={() => setSidebarCollapsed((collapsed) => !collapsed)}
        userEmail={user.email ?? null}
        onSignOut={signOut}
      />
      <section className="workspace">
        <StatsBar document={activeDocuments[0] ?? null} compact={sidebarCollapsed} onMenu={() => { setSidebarCollapsed(false); setSidebarOpen(true); }} onNewChat={() => setChatSession((session) => session + 1)} />
        <ChatPanel documents={activeDocuments} onRemoveDocument={handleToggleActive} resetSignal={chatSession} onOpenLibrary={() => setSidebarOpen(true)} />
      </section>
    </main>
  );
}
