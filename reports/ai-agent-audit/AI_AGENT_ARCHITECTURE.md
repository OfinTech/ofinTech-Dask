# OfinTech Desk AI Agent: architecture as built

This is the architecture as it appears in the source at commit `f8c58cd` (`main`, 13 Nov 2026). Every component
named here was read in the code. Nothing has been inferred from documentation alone.

## 1. Components

| Component | Where | What it does |
|---|---|---|
| Agent loop | `engine/desk_engine/agent/harness.py` (`Agent`, 455 lines) | One run per user turn: model call → tool calls → results → model call, until a final answer. At most `MAX_STEPS = 16` model calls per run (line 32). |
| System prompt | `engine/desk_engine/agent/prompts.py` | 13 rules, then the workspace profile, connected systems, dataset names (up to 40), knowledge note, skill index and pinned skills. These form a **stable** part kept for prompt caching. A **dynamic** part holds the date, run instructions, interface language and approved memories. They are sent as two system messages. |
| Tool registry | `engine/desk_engine/agent/tools.py` | Each tool declares `effect` (`read` or `write`), `min_role`, `timeout` (300 s default) and a JSON schema. `Tool.run` (lines 196–223) re-checks the payment guard, the sealed-workspace guard, JSON validity, role and schema at every call. |
| Tools | `agent/*_tools.py` | 60+ built-in tools. They cover deterministic calculators (`finance_tools.py`), ledger KPIs (`analysis_tools.py`), datasets, documents, files, cost centres, close, memory, skills, workflows, collaboration and Google. MCP servers and connections add dynamic tools. |
| Tool selection | `engine/desk_engine/agent/context.py` | Sends only the tool groups a request needs: a core set, groups triggered by words in the last three user messages (in four languages), groups already used, and groups the model asks for with `more_tools`. Old tool results are cut to 600 characters, and any single result to 16,000. |
| History | `harness.py` `history()` (lines 89–117) | The last `HISTORY_MESSAGES = 60` stored messages of the thread. Pairing is repaired so that every tool call has a result. There is no token budget and no summarisation. |
| Model adapters | `engine/desk_engine/llm/openai_compat.py`, `anthropic.py` | Streaming over HTTP (httpx) with a 120 s read timeout and a 15 s connect timeout. There are no retries. Tool-argument JSON that fails to parse becomes `{"_invalid_json": …}`. |
| Approvals | `engine/desk_engine/approvals.py`, `harness.py` `_request_approval` / `_resolve_pending` | A `write` tool pauses the run (`waiting_approval`). An editor or higher approves the exact arguments. `approvals.claim` binds the approval to that workspace, tool, requester and those arguments, once. |
| Figure check | `harness.py` `unverified_figures()` (lines 60–83) | After the final answer, it lists numbers that appear in no tool result, user message or memory. The list is advisory: it is stored on the message and shown in the app, and it does not block the answer. |
| Memory | `engine/desk_engine/memory.py` | Memories that a person has approved are appended to the dynamic system message. |
| Retrieval | `engine/desk_engine/knowledge/` | BM25 full-text search, made hybrid with embeddings when an embeddings-capable provider is configured. OCR is included. |
| Persistence | `engine/desk_engine/db.py` | SQLite (one file, a single process). Runs, messages, approvals, usage and audit live in tables. |
| Events | `engine/desk_engine/events.py` | A per-thread publish/subscribe channel that streams deltas, tool starts and messages to the app. |
| Audit | `app.py` `audit()`; `tools.py` `_refused` | Every tool run, denial and refusal is audited without its arguments, with `run_id` as the request id. |
| Usage | `app.py` `record_usage` (lines 209–213) | Input, output and cache tokens per model call. No latency is recorded. |
| Embedded gateway | `engine/desk_engine/embedded/` | Runs started by a partner application. These are limited to a read-only `ToolScope` that persists in the run state. |
| Restart recovery | `app.py` lines 134–136 | On start-up, any run still `running` is marked `error` ("Interrupted by a restart."). |

## 2. Execution flow of one turn

```mermaid
sequenceDiagram
    participant U as User (app / embedded host)
    participant R as POST /threads/{id}/messages
    participant A as Agent._run
    participant P as prompts.system_parts + memory
    participant M as Model adapter (stream)
    participant T as Tool.run (guards, schema, timeout)
    participant AP as Approvals
    participant DB as SQLite (messages, runs, audit, usage)

    U->>R: text (+ attachments)
    R->>DB: insert user message, agent_run(status=running)
    R-->>A: asyncio task (one per thread; 409 if busy)
    A->>P: stable + dynamic system prompt
    loop up to MAX_STEPS = 16
        A->>DB: history(thread, last 60 messages)
        A->>A: select_groups / choose_tools / compact
        A->>M: system ×2 + history + tool specs
        M-->>U: text deltas (event channel)
        M-->>A: LLMResult(text, tool_calls, usage, finish)
        A->>DB: assistant message, usage row
        alt no tool calls
            A->>A: unverified_figures(answer, tool+user+memory text)
            A->>DB: run done (final_text)
        else tool calls
            loop each call, in order
                alt unknown tool
                    A->>DB: audit tool.denied, error result
                else write tool (needs approval)
                    A->>AP: request(exact arguments)
                    A->>DB: run waiting_approval → stop
                else read tool
                    A->>T: run(ctx, args)
                    T->>DB: audit tool.run / tool.refused
                    T-->>A: ToolOutput(text masked for the model, blocks, data)
                end
            end
        end
    end
    AP-->>A: all decided → resume: claim(approval) → execute → continue loop
```

## 3. What the model sees in one request

| Part | Measured size |
|---|---|
| System prompt (both parts), test workspace with one dataset | 7,866 characters (E7) |
| Tool schemas sent | 25–26 tools at the median (E6). `context.py` estimates about 8,000 tokens for all built-in tools. |
| Whole request, median of the last 50 benchmark requests | 29,341 characters (≈ 7,300 tokens) (E6) |
| Whole request, 80-turn conversation with large tool results | 18,004 at the first request, 47,704 at most (E3 `long_conversation_80_turns`) |

## 4. Model configuration

- **Provider and model:** chosen per workspace and per thread (`llm/registry.py` `resolve_choice`). Anthropic and any OpenAI-compatible endpoint are supported, including local servers.
- **Sampling:** the agent loop sends no `temperature` and no `max_tokens` (`harness.py` line 317 calls `model.stream(messages, specs)`), so provider defaults apply. Repeatability across runs therefore depends on the provider.
- **Prompt caching:** the stable system part is kept byte-stable and tools are sorted (`context.py` line 123). `llm/anthropic.py` `with_cache` marks cache breakpoints.

## 5. Trust boundaries relevant to the agent

| Boundary | Control in code | Verified by |
|---|---|---|
| Workspace (tenant) | Every tool reads with `ctx.ws_id`; ids from another workspace are "not found". | E5 (657 calls, 0 exposures) and `tests/test_ai_financial_assurance.py` |
| Role | `Tool.min_role` is checked when tools are offered and again at the call (`tools.py` 208–211). Approvals need an editor or higher. | `tests/test_rbac.py`, `tests/test_ai_financial_assurance.py` |
| Payments | `egress.is_payment_tool` at registration, when tools are offered, and at the call | `tests/test_ai_security.py` cases 5 and 8 |
| Sealed workspace | `tool_sends_out` with `workspace_sealed` | `tests/test_guard_audit.py` |
| Model-written arguments for writes | An approval binds the exact arguments (`approvals.claim`). | `tests/test_ai_financial_assurance.py` |
| **System-role text** | Rule 13 tells the model that data is not instructions. **Skill text, and dataset, connection and workspace names, enter the system role.** | E7 (4 of 4 probes reach the system role; see the security report, AI-01, AI-02 and AI-09) |

## 6. Prompt-engineering review

The system prompt is reviewed here by structure and behaviour. It is not reproduced.

**Structure.**
- There are two system messages: a stable one (13 numbered rules, the workspace profile, connected systems, datasets, the knowledge note, the skill index and pinned skills) and a dynamic one (the date, run instructions, interface language and approved memories).
- Measured length in a workspace with one dataset is 7,866 characters (E7).
- Tool descriptions are short, and say when to use one tool rather than another (for example `financial_kpis` against `kpi_detail`).

| Aspect | Assessment | Evidence |
|---|---|---|
| Instruction hierarchy | Clear: rules first, then context. But text written by workspace members (skills) and names taken from data (datasets, connections, collections, workspace) share the system role with the rules. | E7 P1–P4 |
| Clarity | Good: each rule is one topic, numbered, and in plain language. | review |
| Tool-selection guidance | Present for documents, files, KPIs, memory and skills. Not testable without a live model. | E8 (not run) |
| Financial terminology | Accurate; international terms; currency and fiscal-year context provided. | review |
| Hallucination mitigation | The figures rule says every number must come from a tool, the user or a document. Two weaknesses: **the user is listed as a source of figures**, which conflicts with challenging a wrong user assumption, and the figure check enforces it the same way (E4 user echo: 0% flagged). There is also no rule to say when a requested period is outside the data (E2 AI-17). | E4, E2 |
| Injection resistance | One rule says data is not instructions, and every write needs an approval, which is the stronger control. Untrusted text is not delimited structurally (AI-22), and some of it is placed in the system role (AI-09). | E7, existing tests |
| Length and duplication | No contradictions found between the rules. The payment refusal appears in the prompt and in code, which is intended defence in depth. Not excessive. | review |
| Structured output | Answers are Markdown, and the app renders tool blocks itself (never model HTML). There is no structured-output requirement on final answers, so figures in prose are checked only by the advisory figure check. | `agent/tools.py` docstring; E4 |

**Recommended prompt changes.** None of these is proven by a live run yet, so each should be measured with E8
before and after.
1. Say that figures from the user's own messages are user-provided, and must be checked against the books when a tool can, and corrected when they differ.
2. Say that when a requested period or item is not in the data, the answer must state this before giving any figure, and must not substitute another period.
3. Move the dataset, connection and collection lists into a delimited data block outside the system role (AI-09).
4. Keep pinned-skill text only for enabled skills (AI-01), and label skill text as workspace guidance below the numbered rules, not as "follow it".
