// Two-factor authentication, platform notifications and their preferences — shown under Settings → Account, and as
// full-screen gates when policy requires enrolment or a new password before anything else.
import { useState, type FormEvent, type ReactNode } from "react";
import { get, post, put } from "../lib/api";
import { useApp } from "../lib/app";
import { BrandMark, ErrorBox, Field, Loading, errorText, useAction, useLoad } from "../components/ui";
import { fmtRelative } from "../lib/format";
import { tr } from "../lib/i18n";

export function MfaSection(): ReactNode {
  const { session, reloadSession } = useApp();
  const [setup, setSetup] = useState<any>(null);
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const { busy, run } = useAction();
  const on = Boolean(session.user?.mfa_enabled);
  async function confirm(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try { await post("/api/auth/mfa/confirm", { code }); setSetup(null); setCode(""); await reloadSession(); } catch (err) { setError(errorText(err)); }
  }
  return (
    <div className="panel panel-pad stack">
      <h3>{tr("Two-factor authentication")} {on ? <span className="tag green">{tr("on")}</span> : <span className="tag">{tr("off")}</span>}</h3>
      <p className="small muted">{tr("A code from an authenticator app (any TOTP app) is asked for at every sign-in, after the password.")}</p>
      {!on && !setup && <button className="btn primary" disabled={busy} onClick={() => void run(() => post("/api/auth/mfa/begin")).then(setSetup)}>{tr("Set up two-factor authentication")}</button>}
      {setup && <form className="stack" onSubmit={confirm}>
        <p className="small">{tr("Add this key to your authenticator app, then enter the current code to confirm.")}</p>
        <p><code style={{ fontSize: "1.1em", letterSpacing: 2 }}>{setup.key.match(/.{1,4}/g)?.join(" ")}</code></p>
        <p className="small muted" style={{ wordBreak: "break-all" }}>{setup.otpauth}</p>
        <Field label={tr("Code from the app")}><input className="input" inputMode="numeric" autoComplete="one-time-code" maxLength={8} value={code} onChange={(e) => setCode(e.target.value)} style={{ maxWidth: 160 }} /></Field>
        <ErrorBox error={error} />
        <div className="row"><button className="btn primary" disabled={code.length < 6}>{tr("Confirm")}</button><button type="button" className="btn ghost" onClick={() => setSetup(null)}>{tr("Cancel")}</button></div>
      </form>}
      {on && <form className="row" onSubmit={async (e) => { e.preventDefault(); setError(null); try { await post("/api/auth/mfa/disable", { code }); setCode(""); await reloadSession(); } catch (err) { setError(errorText(err)); } }}>
        <input className="input" inputMode="numeric" placeholder={tr("current code")} maxLength={8} value={code} onChange={(e) => setCode(e.target.value)} style={{ maxWidth: 160 }} />
        <button className="btn danger" disabled={code.length < 6}>{tr("Turn off")}</button>
        <ErrorBox error={error} />
      </form>}
    </div>
  );
}

/** Policy requires two-factor authentication: nothing else is reachable until it is set up. */
export function MfaEnrollGate(): ReactNode {
  const { reloadSession } = useApp();
  return (
    <div className="page" style={{ maxWidth: 560, margin: "10vh auto" }}>
      <BrandMark />
      <h1>{tr("Set up two-factor authentication")}</h1>
      <p className="muted">{tr("Your organisation's policy requires two-factor authentication for your account before you continue.")}</p>
      <MfaSection />
      <p><button className="btn ghost small" onClick={() => void post("/api/auth/logout").then(() => reloadSession())}>{tr("Sign out")}</button></p>
    </div>
  );
}

/** A platform administrator required a new password: only this form works until it is done. */
export function ForcedPasswordChange(): ReactNode {
  const { reloadSession } = useApp();
  const [f, setF] = useState({ current: "", new: "" });
  const [error, setError] = useState<string | null>(null);
  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try { await post("/api/auth/password", f); await reloadSession(); } catch (err) { setError(errorText(err)); }
  }
  return (
    <div className="page" style={{ maxWidth: 480, margin: "10vh auto" }}>
      <BrandMark />
      <h1>{tr("Choose a new password")}</h1>
      <p className="muted">{tr("A platform administrator has required a password change for your account.")}</p>
      <form className="stack" onSubmit={submit}>
        <Field label={tr("Current password")}><input className="input" type="password" autoComplete="current-password" required value={f.current} onChange={(e) => setF({ ...f, current: e.target.value })} /></Field>
        <Field label={tr("New password")} hint={tr("At least 12 characters with upper- and lower-case letters and a digit.")}><input className="input" type="password" autoComplete="new-password" required value={f.new} onChange={(e) => setF({ ...f, new: e.target.value })} /></Field>
        <ErrorBox error={error} />
        <div className="row"><button className="btn primary">{tr("Save new password")}</button>
          <button type="button" className="btn ghost" onClick={() => void post("/api/auth/logout").then(() => reloadSession())}>{tr("Sign out")}</button></div>
      </form>
    </div>
  );
}

export function NotificationsSection(): ReactNode {
  const { data, reload } = useLoad(() => get("/api/me/notifications"), []);
  const { data: prefs, reload: reloadPrefs } = useLoad(() => get("/api/me/notification-preferences"), []);
  const { busy, run } = useAction();
  if (!data || !prefs) return <Loading />;
  const set = (cat: string, ch: "in_app" | "email", v: boolean) => void run(() => put("/api/me/notification-preferences", { preferences: { [cat]: { ...prefs.preferences[cat], [ch]: v } } })).then(reloadPrefs);
  return (
    <div className="panel panel-pad stack">
      <div className="row" style={{ justifyContent: "space-between" }}><h3>{tr("Platform notifications")} {data.unread ? <span className="count">{data.unread}</span> : null}</h3>
        {data.unread ? <button className="btn small" disabled={busy} onClick={() => void run(() => post("/api/me/notifications/read", { ids: null })).then(reload)}>{tr("Mark all read")}</button> : null}</div>
      {data.items.length ? <ul className="stack small">{data.items.slice(0, 20).map((n: any) => <li key={n.id} style={{ opacity: n.read_at ? 0.7 : 1 }}>
        <strong>{n.title}</strong> <span className="muted">· {tr(n.category)} · {fmtRelative(n.created_at)}</span>{n.body && <div className="muted">{n.body}</div>}{n.link && <a href={n.link}>{tr("Open")}</a>}</li>)}</ul>
        : <p className="muted small">{tr("No notifications.")}</p>}
      <h4>{tr("What you receive")}</h4>
      <table className="ledger"><thead><tr><th>{tr("Category")}</th><th>{tr("In-app")}</th><th>{tr("E-mail")}</th></tr></thead><tbody>
        {prefs.categories.map((c: string) => <tr key={c}><td>{tr(c)}</td>
          <td><input type="checkbox" checked={prefs.preferences[c].in_app} disabled={prefs.mandatory_in_app.includes(c)} onChange={(e) => set(c, "in_app", e.target.checked)} /></td>
          <td><input type="checkbox" checked={prefs.preferences[c].email} onChange={(e) => set(c, "email", e.target.checked)} /></td></tr>)}
      </tbody></table>
      <p className="small muted">{tr("Security and billing notices always reach you in the app.")}</p>
    </div>
  );
}

export function VerifyEmailPage({ token, onDone }: { token: string; onDone: () => Promise<void> }): ReactNode {
  const { data, error } = useLoad(() => get(`/api/auth/verify-email/${encodeURIComponent(token)}`), [token]);
  return (
    <div className="page" style={{ maxWidth: 480, margin: "10vh auto" }}>
      <BrandMark />
      <h1>{tr("E-mail verification")}</h1>
      {error ? <ErrorBox error={error} /> : data ? <p>{tr("Your e-mail address is verified. Thank you.")}</p> : <Loading />}
      <a className="btn primary" href="#/" onClick={() => void onDone()}>{tr("Continue")}</a>
    </div>
  );
}
