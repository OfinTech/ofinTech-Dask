# Memory

Memory lets the assistant reuse lasting facts across conversations: a materiality threshold, an account mapping, a
close calendar, who approves what. It is built to be **visible and under the team's control**.

## How a memory is created

1. **The assistant proposes.** When you state something lasting, or ask it to remember something, the assistant
   calls `propose_memory`. A card appears in the conversation: *Save to workspace memory?* with **Save**, **Edit**
   and **Discard**. Nothing is used until a person saves it.
2. **Or you add it by hand** under **Memory → Add memory**.

| | Workspace memory | Personal memory |
|---|---|---|
| Who sees and uses it | everyone in the workspace | only you |
| Who can save, change or delete it | editors, admins, owners | you |

Each memory is one short sentence (at most 500 characters) with a category: fact, preference, policy, mapping,
contact or deadline.

**Secrets are refused**, whether proposed or typed: passwords (in English, Arabic, Spanish and French), API keys and
tokens, private keys, IBANs, card numbers (checked with the Luhn test) and bank account numbers. Keep credentials
in Connections, where they are encrypted.

## How a memory is used

- Before each answer the engine picks up to **6** memories marked **Always include** (pinned) and the **8** that best
  match the conversation. Only saved (active) memories you are allowed to see are considered; other workspaces never
  see them.
- **Matching:**
  - It reads the latest three messages you wrote, with the newest counting most. A short follow-up such as "and
    last month?" still finds what the conversation is about.
  - Words are compared after normalising case, accents and Arabic letter forms, and after removing common words.
  - Each word is reduced to a light stem: Arabic articles, prefixes and endings, and English, Spanish and French
    plurals and endings. So "متى نرحل المستحقات؟" finds "الحسابات المستحقة تُرحَّل…" and "accrual entries" finds
    "Accruals are posted…".
  - Rare words count more than common ones.
- **Correcting a memory.** When you tell the assistant that a saved memory is out of date, it proposes the corrected
  one with `replaces`. The card shows what it replaces. Saving it archives the old memory and keeps its *Always
  include* setting.
- They are given to the model as *memory saved by this workspace's people (approved, may be out of date)*.
- Under the answer the app shows **Memory consulted**, with links to each entry. Every use increases the entry's use
  count and *last used* date.
- A figure that comes from a memory counts as sourced in the figure check and in evidence-linked files (the source
  reads *Saved memory*).

## Transparency

The **Memory** page lists saved, waiting and archived entries, with a search that matches words as you type them
("accru" finds *Accruals…*). Each entry shows:

- where it came from (*Suggested by the assistant in “conversation”*, with the reason it gave, or *Added by hand*);
- who created it and who saved it, and when it last changed;
- how often it was used and when.

Entries can be edited, pinned, archived (kept but no longer used), restored or deleted. Every proposal, save, change
and deletion is in the audit log (`memory.propose`, `memory.add`, `memory.save`, `memory.discard`, `memory.edit`, `memory.delete`).

## API

`GET /api/w/{ws}/memories?status=active|proposed|archived&q=…`, `POST /api/w/{ws}/memories`,
`POST /api/w/{ws}/memories/{id}/decide` (`{"approve": true, "content": "optional edited text"}`),
`PATCH /api/w/{ws}/memories/{id}` (`content`, `category`, `pinned`, `status`), `DELETE /api/w/{ws}/memories/{id}`.
