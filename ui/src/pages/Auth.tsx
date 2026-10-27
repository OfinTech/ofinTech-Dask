import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { get, post } from "../lib/api";
import type { Session } from "../lib/app";
import { BrandMark, ErrorBox, LanguagePicker, Wordmark, Field, errorText } from "../components/ui";
import { tr } from "../lib/i18n";

function Tape(): ReactNode {
  // an adding-machine tape: the product's promise in figures that add up
  return (
    <div className="tape" aria-hidden="true">
      <div><span>{tr("Revenue")}</span><span>184,250.00</span></div>
      <div><span>{tr("Cost of sales")}</span><span>(71,980.00)</span></div>
      <div><span>{tr("Operating expenses")}</span><span>(58,410.00)</span></div>
      <div className="total"><span>{tr("Operating profit")}</span><span>53,860.00</span></div>
    </div>
  );
}

function Art(): ReactNode {
  return (
    <section className="auth-art">
      <div className="spread"><div className="row"><BrandMark /><Wordmark height={16} /><strong className="brand-desk">Desk</strong></div>
        <LanguagePicker compact /></div>
      <div>
        <h1 style={{ fontSize: 40, maxWidth: "14ch", marginBottom: 16 }}>{tr("Every figure traced to its source.")}</h1>
        <p className="muted" style={{ maxWidth: "46ch" }}>{tr("An assistant for finance teams: it reads your ledger, spreadsheets and inbox, does the arithmetic in code, and asks before it sends or changes anything.")}</p>
      </div>
      <Tape />
    </section>
  );
}

export function GoogleButton({ invite }: { invite?: string }): ReactNode {
  const [error, setError] = useState<string | null>(null);
  const [waiting, setWaiting] = useState(false);
  return (
    <>
      <button type="button" className="btn google-btn" onClick={async () => {
        try {
          const desk = (window as any).desk;
          const challenge: string | null = desk?.googleChallenge ? desk.googleChallenge() : null;
          const r: any = await post("/api/auth/google/start", { ...(invite ? { invite_token: invite } : {}),
            ...(challenge ? { desktop_challenge: challenge } : {}) });
          if (challenge) {  // desktop app: Google runs in the system browser and hands the sign-in back
            desk.openExternal(r.url);
            setWaiting(true);
          } else location.href = r.url;
        } catch (e) { setError(errorText(e)); }
      }}>
        <svg width="18" height="18" viewBox="0 0 48 48" aria-hidden="true"><path fill="#FFC107" d="M43.6 20.5H42V20H24v8h11.3C33.7 32.7 29.3 36 24 36c-6.6 0-12-5.4-12-12s5.4-12 12-12c3.1 0 5.8 1.2 7.9 3l5.7-5.7C34 6.1 29.3 4 24 4 12.9 4 4 12.9 4 24s8.9 20 20 20 20-8.9 20-20c0-1.3-.1-2.4-.4-3.5z"/><path fill="#FF3D00" d="m6.3 14.7 6.6 4.8C14.7 15.1 19 12 24 12c3.1 0 5.8 1.2 7.9 3l5.7-5.7C34 6.1 29.3 4 24 4 16.3 4 9.7 8.3 6.3 14.7z"/><path fill="#4CAF50" d="M24 44c5.2 0 9.9-2 13.4-5.2l-6.2-5.2C29.2 35.1 26.7 36 24 36c-5.3 0-9.7-3.3-11.3-8l-6.5 5C9.5 39.6 16.2 44 24 44z"/><path fill="#1976D2" d="M43.6 20.5H42V20H24v8h11.3c-.8 2.2-2.2 4.2-4.1 5.6l6.2 5.2C37 39.2 44 34 44 24c0-1.3-.1-2.4-.4-3.5z"/></svg>
        {tr("Continue with Google")}</button>
      {waiting && <p className="small muted" role="status">{tr("Continue in your browser. When you have signed in, OfinTech Desk opens again.")}</p>}
      <ErrorBox error={error} />
    </>
  );
}

export function ResetPage({ token, onDone }: { token: string; onDone: () => Promise<void> }): ReactNode {
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  return (
    <div className="auth">
      <Art />
      <section className="auth-form">
        <form className="stack" style={{ maxWidth: 360 }} onSubmit={async (e) => {
          e.preventDefault();
          setError(null);
          try {
            await post("/api/auth/reset", { token, password });
            location.hash = "#/chat";
            await onDone();
          } catch (err) { setError(errorText(err)); }
        }}>
          <h2>{tr("Choose a new password")}</h2>
          <Field label={tr("New password")} hint={tr("At least 12 characters with upper- and lower-case letters and a digit.")}>
            <input className="input" type="password" required autoComplete="new-password" value={password}
              onChange={(e) => setPassword(e.target.value)} /></Field>
          <ErrorBox error={error} />
          <button className="btn primary">{tr("Save password and sign in")}</button>
        </form>
      </section>
    </div>
  );
}

export function AuthPage({ session, onDone }: { session: Session; onDone: () => Promise<void> }): ReactNode {
  const [mode, setMode] = useState<"login" | "signup" | "forgot">(session.needs_first_user ? "signup" : "login");
  const [notice, setNotice] = useState<string | null>(null);
  const [form, setForm] = useState({ name: "", email: "", password: "" });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (mode === "forgot") {
        const r: any = await post("/api/auth/forgot", { email: form.email });
        setNotice(r.message);
        return;
      }
      await post(mode === "login" ? "/api/auth/login" : "/api/auth/signup", form);
      await onDone();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="auth">
      <Art />
      <section className="auth-form">
        <form onSubmit={submit} className="stack" style={{ maxWidth: 360 }}>
          <h2>{session.needs_first_user ? tr("Create the administrator account") : mode === "login" ? tr("Sign in")
            : mode === "forgot" ? tr("Reset your password") : tr("Create your account")}</h2>
          {session.needs_first_user && <p className="muted">{tr("This is a new Team Server. The first account administers it.")}</p>}
          {session.features.google_login && mode !== "forgot" && <>
            <GoogleButton />
            <div className="or-line"><span>{tr("or with e-mail")}</span></div>
          </>}
          {mode === "signup" && (
            <Field label={tr("Full name")}><input className="input" required autoComplete="name" value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })} /></Field>
          )}
          <Field label={tr("Work e-mail")}><input className="input" type="email" required autoComplete="email" value={form.email}
            onChange={(e) => setForm({ ...form, email: e.target.value })} /></Field>
          {mode !== "forgot" && <Field label={tr("Password")} hint={mode === "signup" ? tr("At least 12 characters with upper- and lower-case letters and a digit.") : undefined}>
            <input className="input" type="password" required autoComplete={mode === "login" ? "current-password" : "new-password"}
              value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} /></Field>}
          <ErrorBox error={error} />
          {notice && <div className="notice small">{notice}</div>}
          <button className="btn primary" disabled={busy}>{mode === "login" ? tr("Sign in") : mode === "forgot" ? tr("Send reset link") : tr("Create account")}</button>
          {mode === "login" && <button type="button" className="btn ghost small" onClick={() => { setMode("forgot"); setNotice(null); }}>
            {tr("Forgot your password?")}</button>}
          {mode === "forgot" && <button type="button" className="btn ghost small" onClick={() => { setMode("login"); setNotice(null); }}>
            {tr("Back to sign in")}</button>}
          {!session.needs_first_user && session.signup_open && (
            <button type="button" className="btn ghost" onClick={() => setMode(mode === "login" ? "signup" : "login")}>
              {mode === "login" ? tr("Create an account") : tr("I already have an account")}</button>
          )}
          {!session.signup_open && mode === "login" && (
            <p className="small muted">{tr("New here? Ask a workspace administrator for an invitation link.")}</p>
          )}
        </form>
      </section>
    </div>
  );
}

export function InvitePage({ token, session, onDone }: { token: string; session: Session; onDone: () => Promise<void> }): ReactNode {
  const [info, setInfo] = useState<{ email: string; role: string; workspace: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [form, setForm] = useState({ name: "", password: "" });

  useEffect(() => {
    get(`/api/invites/${encodeURIComponent(token)}`).then(setInfo).catch((e) => setError(errorText(e)));
  }, [token]);

  async function accept(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      if (session.user) await post(`/api/invites/${encodeURIComponent(token)}/accept`);
      else await post("/api/auth/signup", { ...form, email: info!.email, invite_token: token });
      location.hash = "#/chat";
      await onDone();
    } catch (err) {
      setError(errorText(err));
    }
  }

  return (
    <div className="auth">
      <Art />
      <section className="auth-form">
        <form className="stack" style={{ maxWidth: 360 }} onSubmit={accept}>
          <h2>{tr("Join a workspace")}</h2>
          {info ? <p>{tr("You were invited to")}{" "}<strong>{info.workspace}</strong>{" "}{tr("as")}{" "}<strong>{info.role}</strong> ({info.email}).</p>
            : !error && <p className="muted">{tr("Checking the invitation…")}</p>}
          {info && !session.user && (
            <>
              <Field label={tr("Full name")}><input className="input" required value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })} /></Field>
              <Field label={tr("Choose a password")} hint={tr("At least 12 characters with upper- and lower-case letters and a digit.")}>
                <input className="input" type="password" required autoComplete="new-password" value={form.password}
                  onChange={(e) => setForm({ ...form, password: e.target.value })} /></Field>
            </>
          )}
          <ErrorBox error={error} />
          {info && <button className="btn primary">{session.user ? tr("Join workspace") : tr("Create account and join")}</button>}
          {info && !session.user && session.features.google_login && <>
            <div className="or-line"><span>{tr("or")}</span></div><GoogleButton invite={token} /></>}
        </form>
      </section>
    </div>
  );
}
