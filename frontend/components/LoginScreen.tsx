"use client";

import { useState } from "react";
import { useAuth } from "@/lib/AuthProvider";

function GoogleGlyph() {
  return <svg width="19" height="19" viewBox="0 0 24 24" aria-hidden="true"><path fill="#4285F4" d="M23.52 12.27c0-.85-.08-1.67-.22-2.45H12v4.64h6.47a5.53 5.53 0 0 1-2.4 3.63v3h3.87c2.27-2.09 3.58-5.17 3.58-8.82Z"/><path fill="#34A853" d="M12 24c3.24 0 5.96-1.07 7.94-2.91l-3.87-3c-1.08.72-2.45 1.15-4.07 1.15-3.13 0-5.78-2.11-6.73-4.96H1.27v3.09A12 12 0 0 0 12 24Z"/><path fill="#FBBC05" d="M5.27 14.28A7.2 7.2 0 0 1 4.89 12c0-.79.14-1.56.38-2.28V6.63H1.27A12 12 0 0 0 0 12c0 1.94.46 3.77 1.27 5.37l4-3.09Z"/><path fill="#EA4335" d="M12 4.75c1.77 0 3.35.61 4.6 1.8l3.42-3.42C17.95 1.19 15.24 0 12 0 7.31 0 3.26 2.69 1.27 6.63l4 3.09C6.22 6.86 8.87 4.75 12 4.75Z"/></svg>;
}

function TrailGlyph({ index }: { index: number }) {
  return <span className={`auth-trail-node node-${index}`}><i/><span>{index === 1 ? "PDF" : index === 2 ? "READ" : "ASK"}</span></span>;
}

export default function LoginScreen() {
  const { signInWithGoogle, signInWithMagicLink } = useAuth();
  const [email, setEmail] = useState("");
  const [emailOpen, setEmailOpen] = useState(false);
  const [googleLoading, setGoogleLoading] = useState(false);
  const [sending, setSending] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleGoogle() {
    if (googleLoading) return;
    setError(null); setGoogleLoading(true);
    try { await signInWithGoogle(); }
    catch { setGoogleLoading(false); setError("Google sign-in couldn’t start. Please try again."); }
  }

  async function handleMagicLink(event: React.FormEvent) {
    event.preventDefault();
    if (!email.trim() || sending) return;
    setSending(true); setError(null);
    try { await signInWithMagicLink(email.trim()); setSent(true); }
    catch { setError("We couldn’t send that sign-in link. Check the address and try again."); }
    finally { setSending(false); }
  }

  return <main className="auth-shell">
    <div className="auth-noise" aria-hidden="true"/>
    <div className="auth-layout">
      <section className="auth-story" aria-labelledby="auth-story-title">
        <div className="brand auth-brand"><span className="brand-mark" aria-hidden="true"/><span className="brand-name">Lumen</span><span className="brand-tag">READER</span></div>
        <div className="auth-story-copy"><p className="auth-kicker">A calmer way to work with knowledge</p><h1 id="auth-story-title">Make every page<br/><em>answerable.</em></h1><p>Bring your documents into one deliberate space. Ask plainly, follow the evidence, and return to the thinking that matters.</p></div>
        <div className="auth-trail" aria-label="From source to answer"><div className="auth-trail-line"/><TrailGlyph index={1}/><TrailGlyph index={2}/><TrailGlyph index={3}/></div>
        <div className="auth-proof"><span className="auth-proof-rule"/><p>Every answer stays connected to the source it came from.</p></div>
      </section>

      <section className="auth-entry" aria-labelledby="sign-in-title">
        <div className="auth-entry-inner">
          <div className="auth-entry-heading"><p className="auth-kicker">Your private workspace</p><h2 id="sign-in-title">Welcome to Lumen.</h2><p>Sign in to continue your document conversations.</p></div>
          <button className="auth-google-button" onClick={handleGoogle} disabled={googleLoading} aria-busy={googleLoading}><GoogleGlyph/><span>{googleLoading ? "Opening Google…" : "Continue with Google"}</span><svg className="auth-arrow" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true"><path d="M5 12h14M13 6l6 6-6 6"/></svg></button>
          <p className="auth-google-note"><span>⌁</span> Uses your Google account only to sign you in.</p>

          <div className="auth-alternate">
            {!emailOpen ? <button className="auth-email-reveal" onClick={() => setEmailOpen(true)}>Use an email link instead <span>→</span></button> : sent ? <div className="auth-sent"><span className="auth-sent-icon">✓</span><p>We sent a secure sign-in link to <strong>{email}</strong>.</p><button onClick={() => { setSent(false); setEmail(""); }}>Use a different address</button></div> : <form className="auth-email-form" onSubmit={handleMagicLink}><label htmlFor="auth-email">Email address</label><div className="auth-email-row"><input id="auth-email" type="email" required autoComplete="email" placeholder="you@company.com" value={email} onChange={(event) => setEmail(event.target.value)} disabled={sending} className="auth-email-input"/><button type="submit" disabled={sending || !email.trim()} className="auth-email-button">{sending ? "Sending…" : "Send link"}</button></div></form>}
          </div>
          {error && <p className="auth-error" role="alert"><span>!</span>{error}</p>}
        </div>
        <footer className="auth-footer"><span>By continuing, you agree to use Lumen responsibly.</span><span className="auth-footer-lock"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><rect x="4" y="10" width="16" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/></svg>Private by design</span></footer>
      </section>
    </div>
  </main>;
}
