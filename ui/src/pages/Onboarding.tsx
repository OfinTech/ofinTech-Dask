import { useState, type FormEvent, type ReactNode } from "react";
import { post } from "../lib/api";
import type { Session } from "../lib/app";
import { CURRENCIES, MONTHS } from "../lib/format";
import { BrandMark, ErrorBox, Wordmark, Field, errorText } from "../components/ui";
import { tr } from "../lib/i18n";

const FRAMEWORKS = ["IFRS", "IFRS for SMEs", "US GAAP", "UK GAAP (FRS 102)", "Local GAAP", "Other"];

function guessCurrency(): string {
  try {
    const region = new Intl.Locale(navigator.language).maximize().region || "US";
    const map: Record<string, string> = { US: "USD", GB: "GBP", IN: "INR", AU: "AUD", CA: "CAD", JP: "JPY", CN: "CNY", AE: "AED",
      SA: "SAR", SG: "SGD", CH: "CHF", ZA: "ZAR", NG: "NGN", KE: "KES", BR: "BRL", MX: "MXN", NZ: "NZD", EG: "EGP",
      DE: "EUR", FR: "EUR", ES: "EUR", IT: "EUR", NL: "EUR", IE: "EUR", BE: "EUR", AT: "EUR", PT: "EUR", FI: "EUR" };
    return map[region] || "USD";
  } catch {
    return "USD";
  }
}

function guessCountry(): string {
  try { return new Intl.Locale(navigator.language).maximize().region || ""; } catch { return ""; }
}

export function Onboarding({ session, onDone, embedded }: { session: Session; onDone: (id: string) => Promise<void>;
  embedded?: boolean }): ReactNode {
  const [f, setF] = useState({
    name: "", base_currency: guessCurrency(), country: guessCountry(), fiscal_year_start_month: 1,
    accounting_framework: "IFRS", timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
  });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      const ws = await post<{ id: string }>("/api/workspaces", { ...f, country: f.country.toUpperCase() });
      await onDone(ws.id);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="page" style={{ maxWidth: 720, margin: embedded ? undefined : "0 auto", paddingTop: embedded ? undefined : 64 }}>
      {!embedded && <div className="row" style={{ marginBottom: 24 }}><BrandMark /><Wordmark height={16} /><strong className="brand-desk">Desk</strong></div>}
      <h1>{embedded ? tr("New workspace") : `Welcome${session.user ? ", " + session.user.name.split(" ")[0] : ""}`}</h1>
      <p className="muted" style={{ margin: "6px 0 24px", maxWidth: "60ch" }}>{tr("A workspace holds one company's books, data, dashboards and conversations. These settings tell the assistant which currency, fiscal year and accounting framework to use.")}</p>
      <form className="panel panel-pad stack" onSubmit={submit}>
        <Field label={tr("Company or workspace name")}><input className="input" required maxLength={120} value={f.name}
          placeholder={tr("e.g. Harbour Logistics Ltd")} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
        <div className="grid2">
          <Field label={tr("Base currency")}>
            <select className="select" value={f.base_currency} onChange={(e) => setF({ ...f, base_currency: e.target.value })}>
              {[...new Set([f.base_currency, ...CURRENCIES])].map((c) => <option key={c}>{c}</option>)}
            </select>
          </Field>
          <Field label={tr("Country (ISO code)")} hint={tr("Two letters, e.g. GB, US, IN, AE")}>
            <input className="input" maxLength={2} value={f.country} onChange={(e) => setF({ ...f, country: e.target.value.toUpperCase() })} />
          </Field>
          <Field label={tr("Fiscal year starts in")}>
            <select className="select" value={f.fiscal_year_start_month}
              onChange={(e) => setF({ ...f, fiscal_year_start_month: Number(e.target.value) })}>
              {MONTHS.map((m, i) => <option key={m} value={i + 1}>{m}</option>)}
            </select>
          </Field>
          <Field label={tr("Accounting framework")}>
            <select className="select" value={f.accounting_framework} onChange={(e) => setF({ ...f, accounting_framework: e.target.value })}>
              {FRAMEWORKS.map((x) => <option key={x}>{x}</option>)}
            </select>
          </Field>
        </div>
        <Field label={tr("Time zone")} hint={tr("Used for schedules and dates.")}><input className="input" value={f.timezone}
          onChange={(e) => setF({ ...f, timezone: e.target.value })} /></Field>
        <ErrorBox error={error} />
        <div><button className="btn primary" disabled={busy}>{tr("Create workspace")}</button></div>
      </form>
    </div>
  );
}
