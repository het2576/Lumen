"use client";

import { useState } from "react";
import { useAuth } from "@/lib/AuthProvider";

function GoogleGlyph() {
  return (
    <svg width="17" height="17" viewBox="0 0 24 24" aria-hidden="true">
      <path fill="#4285F4" d="M23.52 12.27c0-.85-.08-1.67-.22-2.45H12v4.64h6.47a5.53 5.53 0 0 1-2.4 3.63v3h3.87c2.27-2.09 3.58-5.17 3.58-8.82Z"/>
      <path fill="#34A853" d="M12 24c3.24 0 5.96-1.07 7.94-2.91l-3.87-3c-1.08.72-2.45 1.15-4.07 1.15-3.13 0-5.78-2.11-6.73-4.96H1.27v3.09A12 12 0 0 0 12 24Z"/>
      <path fill="#FBBC05" d="M5.27 14.28A7.2 7.2 0 0 1 4.89 12c0-.79.14-1.56.38-2.28V6.63H1.27A12 12 0 0 0 0 12c0 1.94.46 3.77 1.27 5.37l4-3.09Z"/>
      <path fill="#EA4335" d="M12 4.75c1.77 0 3.35.61 4.6 1.8l3.42-3.42C17.95 1.19 15.24 0 12 0 7.31 0 3.26 2.69 1.27 6.63l4 3.09C6.22 6.86 8.87 4.75 12 4.75Z"/>
    </svg>
  );
}

export default function LoginScreen() {
  const { signInWithGoogle, signInWithMagicLink } = useAuth();
  const [email, setEmail] = useState("");
  const [sending, setSending] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleGoogle() {
    setError(null);
    try {
      await signInWithGoogle();
    } catch {
      setError("Couldn't start Google sign-in. Try again.");
    }
  }

  async function handleMagicLink(e: React.FormEvent) {
    e.preventDefault();
    if (!email.trim() || sending) return;
    setSending(true);
    setError(null);
    try {
      await signInWithMagicLink(email.trim());
      setSent(true);
    } catch {
      setError("Couldn't send the link. Check the address and try again.");
    } finally {
      setSending(false);
    }
  }

  return (
    <main className="auth-shell">
      <div className="auth-card">
        <div className="brand auth-brand">
          <span className="brand-mark" aria-hidden="true" />
          <span className="brand-name">Lumen</span>
          <span className="brand-tag">READER</span>
        </div>

        <h1 className="auth-title">
          Read beyond the <em>surface.</em>
        </h1>
        <p className="auth-subtitle">Sign in to bring your own sources into a grounded, cited conversation.</p>

        <button className="auth-google-button" onClick={handleGoogle}>
          <GoogleGlyph />
          Continue with Google
        </button>

        <div className="auth-divider"><span>or</span></div>

        {sent ? (
          <p className="auth-sent">Check <strong>{email}</strong> for a sign-in link.</p>
        ) : (
          <form className="auth-email-form" onSubmit={handleMagicLink}>
            <input
              type="email"
              required
              placeholder="you@example.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              disabled={sending}
              className="auth-email-input"
              aria-label="Email address"
            />
            <button type="submit" disabled={sending || !email.trim()} className="auth-email-button">
              {sending ? "Sending…" : "Send magic link"}
            </button>
          </form>
        )}

        {error && <p className="auth-error">{error}</p>}
      </div>
    </main>
  );
}
