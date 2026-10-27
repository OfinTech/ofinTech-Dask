# Search and the document knowledge base (RAG)

The assistant answers questions about policies, contracts, board minutes, audit letters and statements by searching
the workspace's documents. It quotes the passages it used and cites them as [1], [2] with the document name and page.

## Workspace search

The **Search** page (Ctrl/Cmd + K from anywhere) runs one query over:

| Source | What is searched | Permissions |
|---|---|---|
| Documents | passages (keyword + semantic, plus PipesHub when connected), including scans read with OCR | the workspace |
| Conversations | every user and assistant message (SQLite FTS5) | shared conversations, and your own private ones |
| Memory | saved memories | workspace memories, and your personal ones |
| Files | names and row labels of workbooks, reports and your own files | the workspace |
| Data | dataset names and columns | the workspace |

**Answer** adds a short reply (at most about 120 words) written only from the numbered results, citing them as [n].
It is one model call with no tools, made with the workspace's *model for short jobs* if one is set (Settings →
Models). The assistant has the same search as the `search_workspace` tool.

## How it works

```
upload / chat attachment / Gmail attachment / Google Drive (single files or a followed folder)
   → text extraction (PDF text layer, Word, Excel, CSV, text, Markdown, HTML, e-mail, JSON)
   → OCR with Tesseract for images and scanned PDFs (English, Arabic, Spanish, French)
   → passages of about 1,600 characters with a small overlap, keeping page numbers and headings
   → full-text index (SQLite FTS5, BM25, English stemming)        always on, local
   → embeddings via your model provider (optional)                 semantic search
   → PipesHub (optional): the original file is uploaded         OCR, knowledge graph, connectors
search_documents: keyword ranking + semantic ranking (+ PipesHub) merged with reciprocal rank fusion
```

- **Keyword search** works with no set-up and keeps everything on the device or server.
- **Semantic search** turns on when an admin chooses an embedding model under **Documents → Search settings**. Any
  provider with an OpenAI-compatible `/embeddings` endpoint works: OpenAI (`text-embedding-3-small`), Gemini
  (`gemini-embedding-001`), Mistral, Together, Azure OpenAI, or Ollama locally (`nomic-embed-text`, so nothing
  leaves the computer). Changing the model re-embeds every document in the background.
- **Collections** group documents, for example Contracts, Policies or Board. The assistant can search one collection,
  and the system prompt lists the collections.
- **A collection can follow a Google Drive folder** (Documents → collection → *Follow a Drive folder*, paste the
  folder's address). Files in the folder are added, files changed in Drive are replaced and files removed from the
  folder are removed, checked every hour and on *Check now*. Google Docs are exported as Word; other Google files
  (Sheets, Slides) are skipped, so import Sheets under Data instead.
  It needs the Google connection with Drive access.
- **OCR.** PNG, JPEG and TIFF images and PDFs without a text layer are read with Tesseract, page by page,
  when it is installed (it is in the Team Server image). `DESK_OCR=off` turns it off; `DESK_OCR_LANGS` picks the
  languages (default `eng+ara+spa+fra`, whichever are installed). Without Tesseract these files are read by PipesHub
  when it is connected, or refused with a clear message.
- **Citations.** Search results come back numbered. The answer cites them, and the app shows the sources under the
  answer; clicking one opens the document at that page. The figure check also counts numbers taken from passages
  as verified.

## Files attached in the assistant

Attach files with the paperclip in the chat box, by dragging them onto it, or by pasting them. You can attach up to
10 files per message, and you can also send a message that has files but no text.

- **Where they go.** Attached files are added to the knowledge base in a collection called **Chat attachments**.
  Attaching the same file again reuses the copy already there. Viewers can attach files too.
- **When the assistant replies.** The assistant starts once the files are processed. Its message lists each file
  with its document id and whether it could be read.
- **How the assistant uses them.** It reads a whole file with `read_document`, or searches only the attached files
  with `search_documents` and `document_ids`, and cites passages as usual. In the app the files appear as chips on
  your message; each chip opens the document.

## PipesHub

[PipesHub](https://github.com/pipeshub-ai/pipeshub-ai) is an open-source enterprise search and knowledge platform
under Apache-2.0. You run it yourself, or use PipesHub Cloud. It replaces Onyx as the optional external engine. Connect it
under **Connections → Knowledge** with:

- **Address:** for example `https://pipeshub.example.com`.
- **Personal access token:** create one in *Workspace settings → Developer settings → Personal access tokens*,
  with the scopes `kb:read`, `kb:write`, `kb:upload`, `kb:delete` and `semantic:write`. PipesHub's default scope set
  does not include `kb:delete`; without it, documents deleted here stay in PipesHub.
- **Knowledge base id (optional):** leave it empty and OfinTech Desk creates a knowledge base called *OfinTech
  Desk · \<workspace\>*.

| Feature | How |
|---|---|
| Upload | The original file is uploaded with `POST /api/v1/knowledgeBase/{kbId}/upload`, and PipesHub parses it itself. Every document is sent, or only those in chosen collections. Chat attachments are always sent. Documents that were already here are sent when you connect. The record id is kept. |
| Scans and images | Read by the built-in OCR when Tesseract is installed. Otherwise, with PipesHub connected, they are uploaded, PipesHub reads them with OCR, and they show as *via PipesHub*. |
| Search | Every `search_documents` call also calls `POST /api/v1/retrieve`, filtered to the workspace's knowledge base (`filters.kb`). Results are merged with the built-in ranking. A result that is one of this workspace's documents opens the document here, at the page PipesHub cites. |
| Workspace isolation | Each workspace has its own knowledge base. Results from records that belong to another workspace are dropped, even if PipesHub returned them. |
| Other PipesHub sources | Optional and off by default. When it is on, the search also covers everything the token's user can read in PipesHub, through its connectors (SharePoint, Confluence, Google Drive and others), and every member of the workspace can query it. |
| Deletion | Deleting a document here deletes its PipesHub record with `DELETE /api/v1/knowledgeBase/record/{id}`. |

Queries are cleaned before they are sent, because PipesHub refuses text that looks like markup or printf patterns
(`50% discount` is sent as `50 percent discount`).

PipesHub is optional. The built-in knowledge base gives keyword and semantic search on its own. PipesHub adds:

- OCR;
- parsing of complex layouts with Docling;
- knowledge-graph retrieval;
- its connectors;
- scale for large collections.

To deploy PipesHub, follow its own `deployment/docker-compose` guide. It needs MongoDB, Redis, Qdrant, Neo4j or
ArangoDB, and optionally Kafka. Run it next to the Team Server, not inside the desktop app.

## Limits

- Files up to 50 MB and 3,000 pages, with at most 20 files per upload.
- OCR quality depends on the scan; check figures read from a scan against the original before relying on them.
- Old binary formats (.doc, .xls) must be saved as .docx or .xlsx.
- Original files are kept under the engine's data folder with owner-only permissions, so they can be downloaded and
  re-indexed. Deleting a workspace deletes its files.
- Semantic search uses an exact (brute-force) similarity search over the workspace's passages. That is fine for tens
  of thousands of passages. For very large collections, use PipesHub.
