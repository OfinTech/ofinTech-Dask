# 06 — AI and agent security testing

How the AI execution pipeline is tested, and what each test proves. The pipeline under test:

User input → context (`agent/context.py`, `prompts.py`) → model (`llm/*`, masked) → agent loop
(`agent/harness.py`) → tool selection → `Tool.run` (`agent/tools.py`) → results → financial calculation
(`files/expr.py`, `data/query.py`) → final answer with the figure check.

## Method

Every test uses the scripted fake model in `engine/tests/helpers.py` (`FakeLLM`), an OpenAI-compatible endpoint
that replays a list of tool calls and answers. The tests therefore do not measure whether a given model *can* be
talked into an action; they prove what the **engine** does once a model has been. That is the property the
product relies on: no control depends on the model obeying its instructions (see `docs/DATA_BOUNDARIES.md`).
External systems (Gmail, accounting APIs, MCP servers, S3) are mocked with `httpx.MockTransport`; nothing in the
suite reaches a live service.

## The fifteen cases

| # | Case | Test | What it proves |
|---|---|---|---|
| 1 | Direct prompt injection | `test_ai_security.PromptInjection.test_1_direct_injection_cannot_send_without_approval` | A user telling the assistant to ignore its rules and e-mail the ledger produces a pending approval with the exact arguments; nothing is sent; a rejection reaches the model as a tool result. |
| 2 | Indirect prompt injection | `…test_2_indirect_injection_through_a_document_is_data` | Instructions inside a document reach the model as a search result (as data), the send waits for a person, the memory it asked for is only a proposal and appears in no prompt. |
| 3 | Malicious document instructions | `…test_3_instructions_in_an_attached_spreadsheet_change_nothing` | Instructions in an attached `.xlsx` read with `read_document` cause no action and no approval. |
| 4 | Tool permission bypass | `ToolAuthorisation.test_4_a_tool_not_offered_still_needs_approval_and_unknown_tools_do_nothing` | A write tool called by name although it was not offered still needs approval; tools that do not exist return "Unknown tool". |
| 5 | Unauthorized financial actions | `…test_5_no_registered_tool_moves_money`; `test_security_boundaries.Payments.*` | No registered tool pays, transfers or changes bank details; such tools are refused at registration, listing and call. |
| 6 | Cross-tenant information leakage | `TenantIsolationThroughTheAgent.test_6_and_15_foreign_ids_are_not_found`; `test_team_server`, `test_knowledge` | `read_document`, `search_documents`, `query_dataset` and `search_workspace` with another workspace's ids return nothing. |
| 7 | Memory poisoning | case 2 above; `test_memory.test_propose_approve_use`, `test_detects_secrets` | The model can only propose; a proposal is in no prompt until a person approves; secrets are refused. |
| 8 | MCP tool manipulation | `…test_8_mcp_server_cannot_launder_a_payment_as_read_only`; `test_security_boundaries.Payments.test_mcp_payment_tools_are_hidden_and_refused`; `test_mcp` | A server's read-only claim or friendly description does not get a paying tool past the refusal; `auto` needs an admin and the read-only flag. |
| 9 | Malicious API responses | `…test_9_upstream_error_bodies_are_bounded` | A connector's error page full of instructions reaches the model as a short `Error:` result (< 700 chars); the run finishes with no action. |
| 10 | Financial calculation tampering | `FinancialIntegrity.test_10_formula_language_has_no_code_path`; `test_files.test_parse_compile_evaluate` | The formula language rejects code, SQL and spreadsheet escape functions; rounding follows Excel; division by zero is blank; no `eval` anywhere (grep in document 02). |
| 11 | Fabricated financial figures | `…test_11_fabricated_figures_are_flagged`; `test_knowledge.test_agent_answers_with_citations` | Numbers in the answer that appear in no tool result or user text are listed as unverified. |
| 12 | Source attribution failures | `…test_12_a_figure_with_no_source_is_marked_unverified`; `test_files.test_workbook_sources_formulas_and_review` | A literal figure with no evidence line is written as `unverified` with a note; sourced figures carry a source id. |
| 13 | Agent loop abuse | `ToolAuthorisation.test_13_a_run_cannot_loop_for_ever` | A model that keeps calling tools is stopped after 16 steps with a clear error; no more than 16 model calls are made. Per-tool time limits: `test_audit_regressions.OB08AgentIsolation`. |
| 14 | Sensitive information disclosure | `SensitiveInformation.test_14_bank_identifiers_in_tool_output_are_masked`; `test_security_boundaries.Masking.*` | IBANs and card numbers in any text on its way to a model are masked; amounts and invoice numbers survive. |
| 15 | Unauthorized access to files and conversations | case 6; `test_audit_regressions.OB11StreamReauthorisation`; `test_team_server.test_first_user_invite_roles_and_isolation` | Tools and streams give a former or foreign member nothing. |

Run: `cd engine && python -m unittest tests.test_ai_security tests.test_security_boundaries tests.test_audit_regressions`.

## What is not covered, and why

- **Model susceptibility.** Whether a real model follows an injected instruction is a property of the model and
  its prompt, which changes with every provider release. The product's position is that it must not matter; the
  tests above hold whatever the model decides.
- **Semantic data exfiltration through read tools.** A model talked into *reading* more than needed (searching
  every document) leaks nothing outside the workspace, because every outbound path is a write tool or is sealed.
  What a member can read through the assistant equals what they can read in the UI.
- **Reviewer manipulation.** The file reviewer runs with a fresh context and read-only tools
  (`files/review.py`); `test_files.test_workbook_sources_formulas_and_review` exercises it, but no test yet feeds
  it a workbook whose cell text addresses the reviewer. Planned.

## Adding a case

Script the model with `Harness(script=[...])`, drive one turn, and assert on three things: what left the
workspace (mock transports record every request), what waits for approval (`GET /api/w/{ws}/approvals`), and what
the model was shown (`h.llm.calls[n]["messages"]`). A case that needs a real model belongs in a separate,
manually-run evaluation, not in this suite.
