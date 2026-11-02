import { useEffect, useRef, useState, type DragEvent, type FormEvent, type ReactNode } from "react";
import { del, download, get, patch, post, put, uploadMany } from "../lib/api";
import { can, useApp, wsPath } from "../lib/app";
import { fmtNumber, fmtRelative } from "../lib/format";
import { Comments } from "../components/comments";
import { tr } from "../lib/i18n";
import { Confirm, Empty, ErrorBox, Field, Icon, Loading, Modal, PageHead, StatusTag, go, query, useAction, useLoad,
  useToast } from "../components/ui";

const ACCEPT = ".pdf,.docx,.xlsx,.xlsm,.csv,.txt,.md,.html,.htm,.eml,.json,.png,.jpg,.jpeg,.tif,.tiff";

function size(n: number): string {
  return n > 1048576 ? `${(n / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`;
}

export function DocumentsPage({ id }: { id?: string }): ReactNode {
  const { ws } = useApp();
  const toast = useToast();
  const [coll, setColl] = useState<string>("");
  const colls = useLoad(() => get<any[]>(wsPath(ws, "/collections")), [ws.id]);
  const docs = useLoad(() => get<any[]>(wsPath(ws, `/documents${coll ? `?collection=${coll}` : ""}`)), [ws.id, coll]);
  const kb = useLoad(() => get<any>(wsPath(ws, "/knowledge/settings")), [ws.id]);
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<any[] | null>(null);
  const [over, setOver] = useState(false);
  const [newColl, setNewColl] = useState(false);
  const [settings, setSettings] = useState(false);
  const file = useRef<HTMLInputElement>(null);
  const { busy, run } = useAction();
  const editor = can(ws, "editor");

  // refresh while documents are being processed
  useEffect(() => {
    kb.reload();
    if ((docs.data || []).some((d) => d.status === "processing")) {
      const t = setTimeout(docs.reload, 1500);
      return () => clearTimeout(t);
    }
    return undefined;
  }, [docs.data]);

  async function add(files: File[]) {
    if (!files.length) return;
    const r: any = await run(() => uploadMany(wsPath(ws, "/documents"), files, coll ? { collection_id: coll } : {}));
    if (!r) return;
    if (r.added.length) toast(`${r.added.length} document${r.added.length > 1 ? "s" : ""} added; indexing…`);
    for (const f of r.failed) toast(`${f.name}: ${f.error}`, "error");
    docs.reload();
    colls.reload();
  }

  async function search(e?: FormEvent) {
    e?.preventDefault();
    if (!q.trim()) { setHits(null); return; }
    const r = await run(() => post<any[]>(wsPath(ws, "/documents/search"), { query: q, collection_id: coll || null }));
    if (r) setHits(r);
  }

  function onDrop(e: DragEvent) {
    e.preventDefault();
    setOver(false);
    if (editor) void add(Array.from(e.dataTransfer.files));
  }

  const mode = kb.data?.mode === "hybrid" ? `Keyword + semantic search (${kb.data.embedding_model})` : "Keyword search";

  return (
    <div className="page" style={{ maxWidth: 1320 }} onDragOver={(e) => { e.preventDefault(); setOver(true); }}
      onDragLeave={() => setOver(false)} onDrop={onDrop}>
      <PageHead title={tr("Documents")} sub={tr("Policies, contracts, reports and statements the assistant can search and quote. Answers cite the document and page.")}
        actions={<>
          <button className="btn" onClick={() => setSettings(true)}>{tr("Search settings")}</button>
          {editor && <>
            <input ref={file} type="file" multiple accept={ACCEPT} className="hidden"
              onChange={(e) => { void add(Array.from(e.target.files || [])); e.target.value = ""; }} />
            <button className="btn primary" disabled={busy} onClick={() => file.current?.click()}><Icon name="upload" />{tr("Add documents")}</button>
          </>}
        </>} />
      <div className="docs-layout">
        <aside aria-label={tr("Collections")}>
          <ul className="coll-list">
            <li><button aria-pressed={coll === ""} onClick={() => setColl("")}><span>{tr("All documents")}</span></button></li>
            {(colls.data || []).map((c) => (
              <li key={c.id}><button aria-pressed={coll === c.id} onClick={() => setColl(c.id)}>
                <span>{c.name}</span><span className="xs muted">{c.documents}</span></button></li>
            ))}
          </ul>
          {editor && <button className="btn ghost small" style={{ marginTop: 6 }} onClick={() => setNewColl(true)}>
            <Icon name="plus" size={14} />{tr("New collection")}</button>}
          {coll && <DriveFollow coll={(colls.data || []).find((c) => c.id === coll)} editor={editor}
            onChange={() => { colls.reload(); docs.reload(); }} />}
          <p className="xs muted" style={{ marginTop: 16 }}>{mode}.
            {kb.data ? ` ${fmtNumber(kb.data.stats.documents, 0)} documents, ${fmtNumber(kb.data.stats.chunks, 0)} passages.` : ""}</p>
        </aside>
        <div className="stack">
          <form className="row" onSubmit={search} role="search">
            <input className="input" placeholder={tr("Search your documents, e.g. “payment terms with Northwind”")} value={q}
              onChange={(e) => { setQ(e.target.value); if (!e.target.value) setHits(null); }} aria-label={tr("Search documents")} />
            <button className="btn" disabled={busy}><Icon name="search" />{tr("Search")}</button>
          </form>
          {hits && (
            <div className="panel panel-pad">
              <div className="spread"><h3>{hits.length ? `${hits.length} passages` : tr("No passages matched")}</h3>
                <button className="btn ghost small" onClick={() => { setHits(null); setQ(""); }}>{tr("Clear")}</button></div>
              {hits.map((h, i) => (
                <div className="hit" key={i}>
                  <div className="spread">
                    <button className="linklike" onClick={() => h.document_id ? go(`documents/${h.document_id}${h.page ? `?page=${h.page}` : ""}`) : undefined}>
                      {h.name}{h.page ? `, page ${h.page}` : ""}</button>
                    <span className="tag">{h.match}</span>
                  </div>
                  {h.heading && <div className="xs muted">{h.heading}</div>}
                  <div className="small" style={{ marginTop: 4 }}>{h.text.slice(0, 420)}{h.text.length > 420 ? "…" : ""}</div>
                </div>
              ))}
            </div>
          )}
          {editor && <div className={`dropzone ${over ? "over" : ""}`}>{tr("Drop PDF, Word, Excel, CSV, text, HTML, e-mail or image files here")}
            {coll ? tr(" to add them to this collection") : ""}{tr(". Scanned PDFs and images are read with OCR.")}</div>}
          <ErrorBox error={docs.error} />
          {docs.loading && !docs.data ? <Loading /> : (docs.data || []).length === 0 ? (
            <div className="panel"><Empty title={tr("No documents yet")}>{tr("Add your expense policy, contracts, board minutes or audit letters. Ask the assistant to save e-mail attachments or Google Drive files here too.")}</Empty></div>
          ) : (
            <div className="table-wrap" style={{ maxHeight: "none" }}>
              <table className="ledger">
                <thead><tr><th>{tr("Name")}</th><th>{tr("Collection")}</th><th className="num">{tr("Pages")}</th><th>{tr("Status")}</th><th>{tr("Added")}</th></tr></thead>
                <tbody>{(docs.data || []).map((d) => (
                  <tr key={d.id}>
                    <td><a href={`#/documents/${d.id}`}>{d.name}</a><div className="xs muted">{size(d.size)}{d.source !== "upload" ? `, from ${d.source.replace("_", " ")}` : ""}</div></td>
                    <td className="small">{d.collection_name || "—"}</td>
                    <td className="num">{d.pages || "—"}</td>
                    <td><StatusTag status={d.status} />
                      {d.status === "indexed" && <span className="xs muted"> {d.embedded ? tr("semantic") : tr("keyword")}</span>}
                      {d.error && <div className="xs neg">{d.error}</div>}</td>
                    <td className="small">{fmtRelative(d.created_at)}{d.created_by_name ? `, ${d.created_by_name}` : ""}</td>
                  </tr>))}</tbody>
              </table>
            </div>
          )}
        </div>
      </div>
      {id && <DocumentView id={id} collections={colls.data || []} onClose={() => go("documents")} onChanged={() => { docs.reload(); colls.reload(); }} />}
      {newColl && <CollectionForm onClose={() => setNewColl(false)} onSaved={() => { setNewColl(false); colls.reload(); }} />}
      {settings && <SearchSettings onClose={() => { setSettings(false); kb.reload(); }} />}
    </div>
  );
}

function CollectionForm({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }): ReactNode {
  const { ws } = useApp();
  const [f, setF] = useState({ name: "", description: "" });
  const { busy, run } = useAction();
  return (
    <Modal title={tr("New collection")} onClose={onClose} footer={<>
      <button className="btn" onClick={onClose}>{tr("Cancel")}</button>
      <button className="btn primary" disabled={busy || !f.name} onClick={async () => {
        if (await run(() => post(wsPath(ws, "/collections"), f))) onSaved();
      }}>{tr("Create")}</button></>}>
      <div className="stack">
        <Field label={tr("Name")}><input className="input" value={f.name} placeholder={tr("e.g. Contracts")} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
        <Field label={tr("Description (optional)")}><input className="input" value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} /></Field>
      </div>
    </Modal>
  );
}

function DocumentView({ id, collections, onClose, onChanged }: { id: string; collections: any[]; onClose: () => void;
  onChanged: () => void }): ReactNode {
  const { ws } = useApp();
  const [page, setPage] = useState<number | null>(() => Number(query().get("page")) || null);
  const d = useLoad(() => get<any>(wsPath(ws, `/documents/${id}${page ? `?page=${page}` : ""}`)), [id, page]);
  const [confirm, setConfirm] = useState(false);
  const { run } = useAction();
  const doc = d.data?.document;
  return (
    <Modal wide title={doc?.name || tr("Document")} onClose={onClose} footer={doc && <>
      <button className="btn" onClick={() => download(wsPath(ws, `/documents/${id}/file`), doc.name)}>{tr("Download original")}</button>
      {can(ws, "editor") && <button className="btn" onClick={() => run(() => post(wsPath(ws, `/documents/${id}/reindex`)), tr("Re-indexing")).then(() => { onChanged(); d.reload(); })}>{tr("Re-index")}</button>}
      {can(ws, "editor") && <button className="btn danger" onClick={() => setConfirm(true)}>{tr("Delete")}</button>}
    </>}>
      {!doc ? <Loading /> : (
        <div className="stack">
          <div className="row wrap small">
            <StatusTag status={doc.status} />
            <span className="muted">{doc.pages ? `${doc.pages} pages, ` : ""}{doc.chunks}{" "}{tr("passages,")}{" "}{doc.embedded ? tr("semantic and keyword") : tr("keyword")}{" "}{tr("search")}</span>
            {can(ws, "editor") && (
              <select className="select" style={{ width: 200 }} value={doc.collection_id || ""} aria-label={tr("Collection")}
                onChange={(e) => run(() => patch(wsPath(ws, `/documents/${id}`), { collection_id: e.target.value || null })).then(() => { onChanged(); d.reload(); })}>
                <option value="">{tr("No collection")}</option>{collections.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
              </select>
            )}
            {doc.pages > 1 && (
              <span className="row">
                <label className="sr" htmlFor="pg">{tr("Page")}</label>
                <select id="pg" className="select" style={{ width: 130 }} value={page ?? ""} onChange={(e) => setPage(e.target.value ? Number(e.target.value) : null)}>
                  <option value="">{tr("All pages")}</option>
                  {Array.from({ length: Math.min(doc.pages, 3000) }, (_, i) => <option key={i + 1} value={i + 1}>{tr("Page")}{" "}{i + 1}</option>)}
                </select>
              </span>
            )}
          </div>
          {doc.error && <div className="notice warn small">{doc.error}</div>}
          <div className="doc-text">{d.data.text || tr("No text.")}</div>
          <Comments targetType="document" targetId={id} />
        </div>
      )}
      {confirm && <Confirm text={tr("Delete {0}? It is removed from search.", { 0: doc?.name })}
        onClose={() => setConfirm(false)} onYes={async () => { await run(() => del(wsPath(ws, `/documents/${id}`))); onChanged(); onClose(); }} />}
    </Modal>
  );
}

function SearchSettings({ onClose }: { onClose: () => void }): ReactNode {
  const { ws } = useApp();
  const kb = useLoad(() => get<any>(wsPath(ws, "/knowledge/settings")), [ws.id]);
  const providers = useLoad(() => get<any>(wsPath(ws, "/providers")), [ws.id]);
  const [pid, setPid] = useState("");
  const [model, setModel] = useState("");
  const { busy, run } = useAction();
  useEffect(() => {
    if (kb.data) { setPid(kb.data.embedding_provider_id || ""); setModel(kb.data.embedding_model || ""); }
  }, [kb.data]);
  const current = (providers.data?.providers || []).find((p: any) => p.id === pid);
  const admin = can(ws, "admin");
  return (
    <Modal title={tr("Document search settings")} onClose={onClose} footer={<button className="btn" onClick={onClose}>{tr("Close")}</button>}>
      {!kb.data ? <Loading /> : (
        <div className="stack">
          <p className="small">{tr("Keyword search (BM25) always works and runs on this device or server. Add an embedding model to also find passages that use different words (semantic search). Embedding a document sends its text to that provider.")}</p>
          <div className="grid2">
            <Field label={tr("Embedding provider")}><select className="select" value={pid} disabled={!admin} onChange={(e) => { setPid(e.target.value); setModel(""); }}>
              <option value="">{tr("None (keyword search only)")}</option>
              {(providers.data?.providers || []).map((p: any) => <option key={p.id} value={p.id}>{p.label}</option>)}</select></Field>
            <Field label={tr("Embedding model")} hint={tr("e.g. text-embedding-3-small, gemini-embedding-001, nomic-embed-text (Ollama)")}>
              <input className="input" list="emb-models" value={model} disabled={!admin || !pid} onChange={(e) => setModel(e.target.value)} />
              <datalist id="emb-models">{(current?.models || []).filter((m: string) => /embed/i.test(m)).map((m: string) => <option key={m} value={m} />)}</datalist></Field>
          </div>
          {admin && <div className="row"><button className="btn primary" disabled={busy || (!!pid && !model)} onClick={async () => {
            const r: any = await run(() => put(wsPath(ws, "/knowledge/embeddings"), { provider_id: pid || null, model: model || null }), tr("Saved"));
            if (r) kb.reload();
          }}>{tr("Save")}</button>{kb.data.embedding_model && <span className="small muted">{tr("Changing the model re-embeds every document.")}</span>}</div>}
          <div className="small muted">{fmtNumber(kb.data.stats.documents, 0)}{" "}{tr("documents,")}{" "}{fmtNumber(kb.data.stats.embedded, 0)}{" "}{tr("with embeddings.")}</div>
        </div>
      )}
    </Modal>
  );
}

function DriveFollow({ coll, editor, onChange }: { coll: any; editor: boolean; onChange: () => void }): ReactNode {
  const { ws } = useApp();
  const [open, setOpen] = useState(false);
  const [folder, setFolder] = useState("");
  const { busy, run } = useAction();
  if (!coll) return null;
  let src: any = null;
  try { src = coll.sync_source ? JSON.parse(coll.sync_source) : null; } catch { src = null; }
  if (src?.type === "google_drive") {
    const r = src.last_result || {};
    return (
      <div className="panel panel-pad small" style={{ marginTop: 12 }}>
        <div><Icon name="link" size={14} />{" "}{tr("Follows the Drive folder")}{" "}<strong>{src.folder_name}</strong></div>
        <div className="xs muted">{src.last_sync ? `Checked ${fmtRelative(src.last_sync)}: ${r.added || 0} added, ${r.updated || 0} updated,
          ${r.removed || 0} removed` : tr("Not checked yet")}{tr(". Checked every hour.")}</div>
        {editor && <div className="row" style={{ marginTop: 6 }}>
          <button className="btn small" disabled={busy} onClick={() => run(() => post(wsPath(ws, `/collections/${coll.id}/drive/sync`)), tr("Folder checked")).then(onChange)}>{tr("Check now")}</button>
          <button className="btn small ghost" onClick={() => run(() => del(wsPath(ws, `/collections/${coll.id}/drive`))).then(onChange)}>{tr("Stop following")}</button>
        </div>}
      </div>
    );
  }
  if (!editor) return null;
  return (
    <>
      <button className="btn ghost small" style={{ marginTop: 6 }} onClick={() => setOpen(true)}><Icon name="link" size={14} />{tr("Follow a Drive folder")}</button>
      {open && <Modal title={tr("Keep “{0}” in step with a Drive folder", { 0: coll.name })} onClose={() => setOpen(false)} footer={<>
        <button className="btn" onClick={() => setOpen(false)}>{tr("Cancel")}</button>
        <button className="btn primary" disabled={busy || !folder} onClick={async () => {
          const r: any = await run(() => post(wsPath(ws, `/collections/${coll.id}/drive`), { folder }));
          if (r) { setOpen(false); onChange(); }
        }}>{tr("Follow folder")}</button></>}>
        <div className="stack">
          <p className="small">{tr("New and changed files in the folder are added to this collection every hour; files removed from the folder are removed here. Needs a Google connection (Connections → Google).")}</p>
          <Field label={tr("Folder link")} hint={tr("Open the folder in Google Drive and copy the address, …/drive/folders/…")}>
            <input className="input" value={folder} onChange={(e) => setFolder(e.target.value)} /></Field>
        </div>
      </Modal>}
    </>
  );
}
