import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { post } from "../lib/api";
import { useApp, wsPath } from "../lib/app";
import { fmtRelative } from "../lib/format";
import { Empty, ErrorBox, Icon, PageHead, errorText, go, query } from "../components/ui";
import { N, tr } from "../lib/i18n";

const KINDS: [string, string, string][] = [
  ["documents", N("Documents"), "docs"], ["conversations", N("Conversations"), "chat"], ["memory", N("Memory"), "memory"],
  ["files", N("Files"), "sheet"], ["datasets", N("Data"), "data"],
];

/** Text with «highlighted» terms from the conversation search snippets. */
function Highlight({ text }: { text: string }): ReactNode {
  const parts = String(text || "").split(/(«[^»]*»)/);
  return <>{parts.map((p, i) => p.startsWith("«") ? <mark key={i}>{p.slice(1, -1)}</mark> : <span key={i}>{p}</span>)}</>;
}

export function SearchPage(): ReactNode {
  const { ws } = useApp();
  const [q, setQ] = useState(() => query().get("q") || "");
  const [kinds, setKinds] = useState<string[]>([]);
  const [data, setData] = useState<any>(null);
  const [answer, setAnswer] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [asking, setAsking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => { input.current?.focus(); if (q) void run(); }, []);  // eslint-disable-line react-hooks/exhaustive-deps

  async function run(e?: FormEvent) {
    e?.preventDefault();
    if (!q.trim()) return;
    setBusy(true); setError(null); setAnswer(null);
    try {
      setData(await post(wsPath(ws, "/search"), { query: q, sources: kinds.length ? kinds : null }));
      history.replaceState(null, "", `#/search?q=${encodeURIComponent(q)}`);
    } catch (err) { setError(errorText(err)); } finally { setBusy(false); }
  }

  async function ask() {
    setAsking(true); setError(null);
    try {
      const r: any = await post(wsPath(ws, "/search"), { query: q, sources: kinds.length ? kinds : null, answer: true });
      setData(r); setAnswer(r.answer);
    } catch (err) { setError(errorText(err)); } finally { setAsking(false); }
  }

  const results = data?.results || {};
  const total = Object.values(results).reduce((n: number, xs: any) => n + xs.length, 0);
  return (
    <div className="page" style={{ maxWidth: 1000 }}>
      <PageHead title={tr("Search")} sub={tr("One search over documents (including scans read with OCR), past conversations, saved memory, generated files and data. Ask for an answer to get a short reply that cites what it used.")} />
      <form className="search-box" onSubmit={run} role="search">
        <Icon name="search" />
        <input ref={input} className="input" value={q} onChange={(e) => setQ(e.target.value)} aria-label={tr("Search the workspace")}
          placeholder={tr("e.g. payment terms with Northwind, audit fee accrual, Q3 variance workbook")} />
        <button className="btn" disabled={busy || !q.trim()}>{tr("Search")}</button>
        <button type="button" className="btn primary" disabled={asking || !q.trim()} onClick={ask}>{tr("Answer")}</button>
      </form>
      <div className="row wrap" style={{ margin: "10px 0 18px" }} aria-label={tr("Search in")}>
        {KINDS.map(([k, label, icon]) => (
          <button key={k} className="chip" aria-pressed={kinds.includes(k)}
            onClick={() => setKinds(kinds.includes(k) ? kinds.filter((x) => x !== k) : [...kinds, k])}>
            <Icon name={icon} size={14} />{tr(label)}{results[k]?.length ? <span className="xs muted"> {results[k].length}</span> : null}</button>))}
      </div>
      <ErrorBox error={error} />
      {asking && <p className="muted small" role="status">{tr("Reading the best matches…")}</p>}
      {answer && (
        <section className="panel panel-pad answer-box" aria-label={tr("Answer")}>
          <p style={{ margin: 0, lineHeight: 1.6 }}>{answer.text}</p>
          {answer.sources.length > 0 && <ol className="sources" style={{ marginTop: 10 }}>{answer.sources.map((s: any) => (
            <li key={s.n} value={s.n}><button className="linklike" onClick={() => s.link && (/^https?:/.test(s.link) ? window.open(s.link) : go(s.link))}>
              {s.title}{s.page ? `, page ${s.page}` : ""}</button> <span className="xs muted">{s.kind}</span></li>))}</ol>}
        </section>
      )}
      {data && total === 0 && <div className="panel"><Empty title={tr("Nothing found")}>{tr("Try other words, or check the spelling.")}</Empty></div>}
      {KINDS.filter(([k]) => (results[k] || []).length).map(([k, label, icon]) => (
        <section key={k} className="result-group">
          <h2><Icon name={icon} size={16} /> {tr(label)}</h2>
          <ul className="results">
            {results[k].map((r: any, i: number) => (
              <li key={`${r.id}-${i}`}>
                <button className="result" onClick={() => r.external && r.link ? window.open(r.link) : r.link && go(r.link)}>
                  <span className="result-title">{r.title}{r.page ? `, page ${r.page}` : ""}
                    {r.at && <span className="xs muted"> · {fmtRelative(r.at)}</span>}</span>
                  <span className="result-snippet"><Highlight text={String(r.snippet || "").slice(0, 400)} /></span>
                </button>
              </li>))}
          </ul>
        </section>
      ))}
    </div>
  );
}
