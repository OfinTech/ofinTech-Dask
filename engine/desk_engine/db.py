"""SQLite storage (WAL mode). One connection per thread; every query is parameterised.

The schema is created by ordered migrations in ``MIGRATIONS``; the applied version is kept in
``PRAGMA user_version``. Tenancy: every workspace-owned row carries ``workspace_id`` and every
repository query filters by it (see tests/test_isolation.py).
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

MIGRATIONS: list[str] = [
    # 1 — identity, workspaces, secrets, audit
    """
    CREATE TABLE users (
        id TEXT PRIMARY KEY,
        email TEXT NOT NULL UNIQUE COLLATE NOCASE,
        name TEXT NOT NULL,
        password_hash TEXT,
        is_local INTEGER NOT NULL DEFAULT 0,
        disabled INTEGER NOT NULL DEFAULT 0,
        created_at REAL NOT NULL
    );
    CREATE TABLE sessions (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        created_at REAL NOT NULL,
        expires_at REAL NOT NULL,
        last_seen REAL NOT NULL,
        user_agent TEXT
    );
    CREATE TABLE workspaces (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        base_currency TEXT NOT NULL DEFAULT 'USD',
        country TEXT NOT NULL DEFAULT '',
        fiscal_year_start_month INTEGER NOT NULL DEFAULT 1,
        accounting_framework TEXT NOT NULL DEFAULT 'IFRS',
        timezone TEXT NOT NULL DEFAULT 'UTC',
        default_provider_id TEXT,
        default_model TEXT,
        created_at REAL NOT NULL
    );
    CREATE TABLE members (
        workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        role TEXT NOT NULL,
        created_at REAL NOT NULL,
        PRIMARY KEY (workspace_id, user_id)
    );
    CREATE TABLE invites (
        id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
        email TEXT NOT NULL COLLATE NOCASE,
        role TEXT NOT NULL,
        token_hash TEXT NOT NULL UNIQUE,
        created_by TEXT NOT NULL,
        created_at REAL NOT NULL,
        expires_at REAL NOT NULL,
        accepted_at REAL
    );
    CREATE TABLE secrets (
        id TEXT PRIMARY KEY,
        workspace_id TEXT,
        purpose TEXT NOT NULL,
        ciphertext BLOB NOT NULL,
        created_at REAL NOT NULL
    );
    CREATE TABLE settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    CREATE TABLE audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        workspace_id TEXT,
        user_id TEXT,
        action TEXT NOT NULL,
        target TEXT,
        detail TEXT,
        created_at REAL NOT NULL
    );
    CREATE INDEX ix_audit_ws ON audit_log(workspace_id, id DESC);
    """,
    # 2 — models, chat, agent runs, approvals, usage
    """
    CREATE TABLE llm_providers (
        id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
        kind TEXT NOT NULL,
        label TEXT NOT NULL,
        base_url TEXT NOT NULL,
        secret_id TEXT,
        models TEXT NOT NULL DEFAULT '[]',
        options TEXT NOT NULL DEFAULT '{}',
        created_at REAL NOT NULL
    );
    CREATE TABLE threads (
        id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
        title TEXT NOT NULL,
        created_by TEXT NOT NULL,
        visibility TEXT NOT NULL DEFAULT 'workspace',
        provider_id TEXT,
        model TEXT,
        skills TEXT NOT NULL DEFAULT '[]',
        channel TEXT,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    );
    CREATE INDEX ix_threads_ws ON threads(workspace_id, updated_at DESC);
    CREATE TABLE messages (
        id TEXT PRIMARY KEY,
        thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
        role TEXT NOT NULL,
        author_id TEXT,
        content TEXT NOT NULL DEFAULT '',
        payload TEXT NOT NULL DEFAULT '{}',
        created_at REAL NOT NULL
    );
    CREATE INDEX ix_messages_thread ON messages(thread_id, created_at);
    CREATE TABLE agent_runs (
        id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
        thread_id TEXT REFERENCES threads(id) ON DELETE CASCADE,
        user_id TEXT NOT NULL,
        status TEXT NOT NULL,
        state TEXT NOT NULL DEFAULT '{}',
        usage TEXT NOT NULL DEFAULT '{}',
        error TEXT,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    );
    CREATE TABLE approvals (
        id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
        source TEXT NOT NULL,
        source_id TEXT NOT NULL,
        tool TEXT NOT NULL,
        arguments TEXT NOT NULL DEFAULT '{}',
        summary TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        requested_by TEXT,
        decided_by TEXT,
        decided_at REAL,
        result TEXT,
        created_at REAL NOT NULL
    );
    CREATE INDEX ix_approvals_ws ON approvals(workspace_id, status, created_at DESC);
    CREATE TABLE usage (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        workspace_id TEXT NOT NULL,
        provider_id TEXT,
        model TEXT,
        input_tokens INTEGER NOT NULL DEFAULT 0,
        output_tokens INTEGER NOT NULL DEFAULT 0,
        source TEXT,
        created_at REAL NOT NULL
    );
    CREATE INDEX ix_usage_ws ON usage(workspace_id, created_at);
    """,
    # 3 — skills, data, dashboards, connections, MCP
    """
    CREATE TABLE skills (
        id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        description TEXT NOT NULL,
        body TEXT NOT NULL,
        files TEXT NOT NULL DEFAULT '{}',
        tags TEXT NOT NULL DEFAULT '[]',
        version TEXT NOT NULL DEFAULT '1.0.0',
        enabled INTEGER NOT NULL DEFAULT 1,
        created_by TEXT,
        updated_at REAL NOT NULL,
        UNIQUE (workspace_id, name)
    );
    CREATE TABLE skill_settings (
        workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        enabled INTEGER NOT NULL,
        PRIMARY KEY (workspace_id, name)
    );
    CREATE TABLE datasets (
        id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        source TEXT NOT NULL,
        source_ref TEXT NOT NULL DEFAULT '{}',
        columns TEXT NOT NULL,
        row_count INTEGER NOT NULL DEFAULT 0,
        created_by TEXT,
        refreshed_at REAL NOT NULL,
        created_at REAL NOT NULL
    );
    CREATE INDEX ix_datasets_ws ON datasets(workspace_id, name);
    CREATE TABLE dashboards (
        id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        widgets TEXT NOT NULL DEFAULT '[]',
        filters TEXT NOT NULL DEFAULT '{}',
        created_by TEXT,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    );
    CREATE TABLE connections (
        id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
        provider TEXT NOT NULL,
        label TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        config TEXT NOT NULL DEFAULT '{}',
        secret_id TEXT,
        created_by TEXT,
        created_at REAL NOT NULL,
        last_sync_at REAL,
        last_error TEXT
    );
    CREATE INDEX ix_connections_ws ON connections(workspace_id, provider);
    CREATE TABLE oauth_states (
        state TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        provider TEXT NOT NULL,
        code_verifier TEXT NOT NULL,
        extra TEXT NOT NULL DEFAULT '{}',
        created_at REAL NOT NULL
    );
    CREATE TABLE mcp_servers (
        id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        transport TEXT NOT NULL,
        command TEXT,
        args TEXT NOT NULL DEFAULT '[]',
        url TEXT,
        secret_id TEXT,
        enabled INTEGER NOT NULL DEFAULT 1,
        default_policy TEXT NOT NULL DEFAULT 'ask',
        tool_policy TEXT NOT NULL DEFAULT '{}',
        tools_cache TEXT NOT NULL DEFAULT '[]',
        last_error TEXT,
        created_by TEXT,
        created_at REAL NOT NULL,
        UNIQUE (workspace_id, name)
    );
    """,
    # 4 — workflows, collaboration, channels
    """
    CREATE TABLE workflows (
        id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        definition TEXT NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 1,
        next_run_at REAL,
        created_by TEXT,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    );
    CREATE TABLE workflow_runs (
        id TEXT PRIMARY KEY,
        workflow_id TEXT NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
        workspace_id TEXT NOT NULL,
        trigger TEXT NOT NULL DEFAULT '{}',
        status TEXT NOT NULL,
        step_index INTEGER NOT NULL DEFAULT 0,
        context TEXT NOT NULL DEFAULT '{}',
        log TEXT NOT NULL DEFAULT '[]',
        error TEXT,
        started_by TEXT,
        started_at REAL NOT NULL,
        finished_at REAL
    );
    CREATE INDEX ix_runs_wf ON workflow_runs(workflow_id, started_at DESC);
    CREATE TABLE comments (
        id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
        target_type TEXT NOT NULL,
        target_id TEXT NOT NULL,
        author_id TEXT NOT NULL,
        body TEXT NOT NULL,
        mentions TEXT NOT NULL DEFAULT '[]',
        created_at REAL NOT NULL
    );
    CREATE INDEX ix_comments_target ON comments(workspace_id, target_type, target_id, created_at);
    CREATE TABLE notifications (
        id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
        user_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        title TEXT NOT NULL,
        link TEXT,
        read_at REAL,
        created_at REAL NOT NULL
    );
    CREATE INDEX ix_notifications_user ON notifications(user_id, read_at, created_at DESC);
    CREATE TABLE channel_links (
        id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
        connection_id TEXT NOT NULL REFERENCES connections(id) ON DELETE CASCADE,
        external_id TEXT NOT NULL,
        display_name TEXT,
        user_id TEXT NOT NULL,
        thread_id TEXT,
        created_at REAL NOT NULL,
        UNIQUE (connection_id, external_id)
    );
    CREATE TABLE mcp_keys (
        id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        token_hash TEXT NOT NULL UNIQUE,
        created_at REAL NOT NULL,
        last_used_at REAL
    );
    CREATE TABLE pairing_codes (
        code_hash TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL,
        connection_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        expires_at REAL NOT NULL
    );
    """,
    # 5 — document knowledge base (RAG)
    """
    ALTER TABLE workspaces ADD COLUMN embedding_provider_id TEXT;
    ALTER TABLE workspaces ADD COLUMN embedding_model TEXT;
    CREATE TABLE doc_collections (
        id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        sync_onyx INTEGER NOT NULL DEFAULT 0,
        created_by TEXT,
        created_at REAL NOT NULL,
        UNIQUE (workspace_id, name)
    );
    CREATE TABLE documents (
        id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
        collection_id TEXT REFERENCES doc_collections(id) ON DELETE SET NULL,
        name TEXT NOT NULL,
        mime TEXT NOT NULL,
        size INTEGER NOT NULL,
        sha256 TEXT NOT NULL,
        source TEXT NOT NULL DEFAULT 'upload',
        source_ref TEXT NOT NULL DEFAULT '{}',
        status TEXT NOT NULL,
        error TEXT,
        pages INTEGER NOT NULL DEFAULT 0,
        chunks INTEGER NOT NULL DEFAULT 0,
        embedded INTEGER NOT NULL DEFAULT 0,
        onyx_status TEXT,
        created_by TEXT,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    );
    CREATE INDEX ix_documents_ws ON documents(workspace_id, created_at DESC);
    CREATE TABLE doc_chunks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
        workspace_id TEXT NOT NULL,
        ord INTEGER NOT NULL,
        page INTEGER,
        heading TEXT,
        text TEXT NOT NULL,
        embedding BLOB,
        embedding_model TEXT
    );
    CREATE INDEX ix_chunks_doc ON doc_chunks(document_id, ord);
    CREATE INDEX ix_chunks_ws ON doc_chunks(workspace_id);
    CREATE VIRTUAL TABLE doc_chunks_fts USING fts5(text, heading, content='doc_chunks', content_rowid='id',
                                                   tokenize='porter unicode61 remove_diacritics 2');
    CREATE TRIGGER doc_chunks_ai AFTER INSERT ON doc_chunks BEGIN
        INSERT INTO doc_chunks_fts(rowid, text, heading) VALUES (new.id, new.text, coalesce(new.heading, ''));
    END;
    CREATE TRIGGER doc_chunks_ad AFTER DELETE ON doc_chunks BEGIN
        INSERT INTO doc_chunks_fts(doc_chunks_fts, rowid, text, heading)
        VALUES ('delete', old.id, old.text, coalesce(old.heading, ''));
    END;
    """,
    # 6 — evidence-linked files (workbooks and reports), reviewer findings, push connectors
    """
    ALTER TABLE workspaces ADD COLUMN reviewer_provider_id TEXT;
    ALTER TABLE workspaces ADD COLUMN reviewer_model TEXT;
    ALTER TABLE workspaces ADD COLUMN auto_review INTEGER NOT NULL DEFAULT 1;
    CREATE TABLE files (
        id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
        thread_id TEXT,
        name TEXT NOT NULL,
        kind TEXT NOT NULL,
        current_version INTEGER NOT NULL DEFAULT 1,
        review_status TEXT NOT NULL DEFAULT 'none',
        review_error TEXT,
        created_by TEXT,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    );
    CREATE INDEX ix_files_ws ON files(workspace_id, updated_at DESC);
    CREATE TABLE file_versions (
        id TEXT PRIMARY KEY,
        file_id TEXT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
        workspace_id TEXT NOT NULL,
        version INTEGER NOT NULL,
        spec TEXT NOT NULL,
        model TEXT NOT NULL,
        outputs TEXT NOT NULL DEFAULT '{}',
        note TEXT,
        created_by TEXT,
        created_at REAL NOT NULL,
        UNIQUE (file_id, version)
    );
    CREATE TABLE file_findings (
        id TEXT PRIMARY KEY,
        file_id TEXT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
        workspace_id TEXT NOT NULL,
        version INTEGER NOT NULL,
        origin TEXT NOT NULL,
        target TEXT NOT NULL,
        severity TEXT NOT NULL,
        title TEXT NOT NULL,
        body TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'open',
        decided_by TEXT,
        decided_at REAL,
        created_at REAL NOT NULL
    );
    CREATE INDEX ix_findings_file ON file_findings(file_id, version);
    CREATE TABLE ingest_keys (
        id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
        connection_id TEXT NOT NULL REFERENCES connections(id) ON DELETE CASCADE,
        key_hash TEXT NOT NULL UNIQUE,
        prefix TEXT NOT NULL,
        created_by TEXT,
        created_at REAL NOT NULL,
        last_used_at REAL
    );
    """,
    # 7 — PipesHub replaces Onyx as the external document engine; chat attachments
    """
    ALTER TABLE doc_collections RENAME COLUMN sync_onyx TO sync_pipeshub;
    ALTER TABLE documents RENAME COLUMN onyx_status TO pipeshub_status;
    ALTER TABLE documents ADD COLUMN pipeshub_record_id TEXT;
    CREATE INDEX ix_documents_pipeshub ON documents(pipeshub_record_id);
    UPDATE documents SET pipeshub_status = NULL;
    DELETE FROM secrets WHERE id IN (SELECT secret_id FROM connections WHERE provider = 'onyx');
    DELETE FROM connections WHERE provider = 'onyx';
    """,
    # 8 — sign-in with Google, password reset
    """
    ALTER TABLE users ADD COLUMN google_sub TEXT;
    CREATE UNIQUE INDEX ux_users_google ON users(google_sub) WHERE google_sub IS NOT NULL;
    CREATE TABLE password_resets (
        token_hash TEXT PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        created_at REAL NOT NULL,
        expires_at REAL NOT NULL,
        used_at REAL
    );
    CREATE TABLE login_states (
        state TEXT PRIMARY KEY,
        code_verifier TEXT NOT NULL,
        invite_token TEXT,
        created_at REAL NOT NULL
    );
    """,
    # 9 — memory: facts, preferences and policies the assistant may reuse, saved only with a person's approval
    """
    CREATE TABLE memories (
        id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
        user_id TEXT,
        scope TEXT NOT NULL,
        category TEXT NOT NULL DEFAULT 'fact',
        content TEXT NOT NULL,
        status TEXT NOT NULL,
        pinned INTEGER NOT NULL DEFAULT 0,
        source_type TEXT NOT NULL DEFAULT 'manual',
        thread_id TEXT,
        message_id TEXT,
        reason TEXT,
        created_by TEXT,
        approved_by TEXT,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        last_used_at REAL,
        use_count INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX ix_memories_ws ON memories(workspace_id, status);
    CREATE VIRTUAL TABLE memories_fts USING fts5(content, category, content='memories', content_rowid='rowid',
                                                 tokenize='porter unicode61 remove_diacritics 2');
    CREATE TRIGGER memories_ai AFTER INSERT ON memories BEGIN
        INSERT INTO memories_fts(rowid, content, category) VALUES (new.rowid, new.content, new.category);
    END;
    CREATE TRIGGER memories_ad AFTER DELETE ON memories BEGIN
        INSERT INTO memories_fts(memories_fts, rowid, content, category) VALUES ('delete', old.rowid, old.content, old.category);
    END;
    CREATE TRIGGER memories_au AFTER UPDATE OF content, category ON memories BEGIN
        INSERT INTO memories_fts(memories_fts, rowid, content, category) VALUES ('delete', old.rowid, old.content, old.category);
        INSERT INTO memories_fts(rowid, content, category) VALUES (new.rowid, new.content, new.category);
    END;
    """,
    # 10 — workspace search over conversations; collections that follow a Google Drive folder
    """
    CREATE VIRTUAL TABLE messages_fts USING fts5(content, content='messages', content_rowid='rowid',
                                                 tokenize='porter unicode61 remove_diacritics 2');
    INSERT INTO messages_fts(rowid, content) SELECT rowid, content FROM messages WHERE role IN ('user', 'assistant');
    CREATE TRIGGER messages_fts_ai AFTER INSERT ON messages WHEN new.role IN ('user', 'assistant') BEGIN
        INSERT INTO messages_fts(rowid, content) VALUES (new.rowid, new.content);
    END;
    CREATE TRIGGER messages_fts_ad AFTER DELETE ON messages WHEN old.role IN ('user', 'assistant') BEGIN
        INSERT INTO messages_fts(messages_fts, rowid, content) VALUES ('delete', old.rowid, old.content);
    END;
    ALTER TABLE doc_collections ADD COLUMN sync_source TEXT;
    """,
    # 11 — workflow webhooks; a cheaper "utility" model for small jobs; each person's language
    """
    ALTER TABLE workflows ADD COLUMN webhook_hash TEXT;
    CREATE UNIQUE INDEX ux_workflows_webhook ON workflows(webhook_hash) WHERE webhook_hash IS NOT NULL;
    ALTER TABLE workspaces ADD COLUMN utility_provider_id TEXT;
    ALTER TABLE workspaces ADD COLUMN utility_model TEXT;
    ALTER TABLE users ADD COLUMN language TEXT;
    ALTER TABLE usage ADD COLUMN cache_read INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE usage ADD COLUMN cache_write INTEGER NOT NULL DEFAULT 0;
    """,
    # 12 — Google sign-in from the desktop app: the browser hands the sign-in back to the app that started it
    """
    ALTER TABLE login_states ADD COLUMN desktop_challenge TEXT;
    CREATE TABLE desktop_handoffs (
        code_hash TEXT PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        challenge TEXT NOT NULL,
        created_at REAL NOT NULL,
        used_at REAL
    );
    """,
    # 13 — cost centres: rules over general-ledger lines, their history, and budget lines read from a file
    """
    CREATE TABLE cost_centres (
        id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        code TEXT,
        description TEXT,
        status TEXT NOT NULL DEFAULT 'active',
        dataset_id TEXT,
        start_date TEXT,
        end_date TEXT,
        rules TEXT NOT NULL,
        rules_version INTEGER NOT NULL DEFAULT 1,
        include_assets INTEGER NOT NULL DEFAULT 0,
        budget_amount REAL,
        burn_months INTEGER NOT NULL DEFAULT 3,
        budget_file_id TEXT,
        budget_file_version INTEGER,
        created_by TEXT,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    );
    CREATE UNIQUE INDEX ux_cost_centres_name ON cost_centres(workspace_id, lower(name));
    CREATE TABLE cost_centre_rule_versions (
        cost_centre_id TEXT NOT NULL REFERENCES cost_centres(id) ON DELETE CASCADE,
        version INTEGER NOT NULL,
        rules TEXT NOT NULL,
        note TEXT,
        changed_by TEXT,
        created_at REAL NOT NULL,
        PRIMARY KEY (cost_centre_id, version)
    );
    CREATE TABLE cost_centre_budget_lines (
        id TEXT PRIMARY KEY,
        cost_centre_id TEXT NOT NULL REFERENCES cost_centres(id) ON DELETE CASCADE,
        position INTEGER NOT NULL,
        label TEXT NOT NULL,
        amount REAL NOT NULL,
        accounts TEXT NOT NULL DEFAULT '[]',
        source TEXT NOT NULL DEFAULT '{}'
    );
    CREATE INDEX ix_cost_centre_budget ON cost_centre_budget_lines(cost_centre_id, position);
    """,
    # 14 — financial analysis: which ledger, account roles, KPI settings, forecasts
    """
    CREATE TABLE analysis_settings (
        workspace_id TEXT PRIMARY KEY REFERENCES workspaces(id) ON DELETE CASCADE,
        dataset_id TEXT,
        updated_by TEXT,
        updated_at REAL NOT NULL
    );
    CREATE TABLE account_roles (
        workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
        dataset_id TEXT NOT NULL,
        account_key TEXT NOT NULL,
        role TEXT NOT NULL,
        confirmed_by TEXT,
        updated_at REAL NOT NULL,
        PRIMARY KEY (workspace_id, dataset_id, account_key)
    );
    CREATE TABLE kpi_settings (
        workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
        kpi_id TEXT NOT NULL,
        name TEXT,
        category TEXT,
        formula TEXT,
        unit TEXT,
        direction TEXT,
        target REAL,
        warn REAL,
        enabled INTEGER NOT NULL DEFAULT 1,
        updated_by TEXT,
        updated_at REAL NOT NULL,
        PRIMARY KEY (workspace_id, kpi_id)
    );
    CREATE TABLE forecasts (
        id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
        dataset_id TEXT NOT NULL,
        group_id TEXT NOT NULL,
        name TEXT NOT NULL,
        scenario TEXT NOT NULL DEFAULT 'Base',
        version INTEGER NOT NULL DEFAULT 1,
        status TEXT NOT NULL DEFAULT 'draft',
        is_plan INTEGER NOT NULL DEFAULT 0,
        start_month TEXT NOT NULL,
        months INTEGER NOT NULL,
        assumptions TEXT NOT NULL,
        result TEXT,
        note TEXT,
        created_by TEXT,
        submitted_by TEXT,
        approved_by TEXT,
        approved_at REAL,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    );
    CREATE INDEX ix_forecasts_ws ON forecasts(workspace_id, group_id);
    """,
    # 15 — webhooks under Connections: incoming (other systems call us) and outgoing (we call other systems)
    """
    CREATE TABLE webhooks (
        id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
        direction TEXT NOT NULL,
        name TEXT NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 1,
        secret_id TEXT,
        secret_hash TEXT,
        secret_prefix TEXT,
        url TEXT,
        events TEXT NOT NULL DEFAULT '[]',
        workflow_id TEXT,
        created_by TEXT,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        last_delivery_at REAL,
        last_status TEXT
    );
    CREATE INDEX ix_webhooks_ws ON webhooks(workspace_id, direction);
    CREATE TABLE webhook_deliveries (
        id TEXT PRIMARY KEY,
        webhook_id TEXT NOT NULL REFERENCES webhooks(id) ON DELETE CASCADE,
        workspace_id TEXT NOT NULL,
        direction TEXT NOT NULL,
        event TEXT,
        status TEXT NOT NULL,
        http_status INTEGER,
        attempts INTEGER NOT NULL DEFAULT 0,
        payload TEXT,
        response TEXT,
        run_id TEXT,
        created_at REAL NOT NULL,
        next_attempt_at REAL,
        finished_at REAL
    );
    CREATE INDEX ix_webhook_deliveries ON webhook_deliveries(webhook_id, created_at);
    CREATE INDEX ix_webhook_pending ON webhook_deliveries(status, next_attempt_at) WHERE status = 'pending';
    """,
    # 16 — month-end close: one run per ledger and month, its proposed entries and review items
    """
    CREATE TABLE close_runs (
        id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
        dataset_id TEXT NOT NULL,
        month TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'open',
        settings TEXT NOT NULL DEFAULT '{}',
        result TEXT,
        statement TEXT,
        prepared_by TEXT,
        prepared_via TEXT,
        prepared_at REAL,
        closed_by TEXT,
        closed_at REAL,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        UNIQUE (workspace_id, dataset_id, month)
    );
    CREATE INDEX ix_close_runs_ws ON close_runs(workspace_id, month);
    CREATE TABLE close_items (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL REFERENCES close_runs(id) ON DELETE CASCADE,
        workspace_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        key TEXT NOT NULL,
        status TEXT NOT NULL,
        position INTEGER NOT NULL DEFAULT 0,
        data TEXT NOT NULL,
        note TEXT,
        decided_by TEXT,
        decided_at REAL,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        UNIQUE (run_id, key)
    );
    CREATE INDEX ix_close_items_run ON close_items(run_id, kind, position);
    """,
    # 17 — memory: a proposal can replace an outdated memory (the old one is archived when the new one is saved)
    """
    ALTER TABLE memories ADD COLUMN replaces_id TEXT;
    """,
    # 18 — a workspace can keep its data inside the organization: no outside tools, a model on the local network only
    """
    ALTER TABLE workspaces ADD COLUMN egress_policy TEXT NOT NULL DEFAULT 'open';
    """,
    # 19 — security hardening: approvals carry an expiry and a digest of what was approved and are consumed once;
    #      audit entries carry an event id, a correlation id, the decision, the approval and the outcome, and can
    #      neither be changed nor deleted through the database connection the engine uses
    """
    ALTER TABLE approvals ADD COLUMN expires_at REAL;
    ALTER TABLE approvals ADD COLUMN args_digest TEXT;
    ALTER TABLE approvals ADD COLUMN executed_at REAL;
    ALTER TABLE approvals ADD COLUMN executed_by TEXT;
    ALTER TABLE audit_log ADD COLUMN event_id TEXT;
    ALTER TABLE audit_log ADD COLUMN request_id TEXT;
    ALTER TABLE audit_log ADD COLUMN resource_type TEXT;
    ALTER TABLE audit_log ADD COLUMN decision TEXT;
    ALTER TABLE audit_log ADD COLUMN approval_id TEXT;
    ALTER TABLE audit_log ADD COLUMN result TEXT;
    CREATE INDEX ix_audit_event ON audit_log(event_id);
    CREATE TRIGGER audit_log_no_update BEFORE UPDATE ON audit_log
    BEGIN SELECT RAISE(ABORT, 'audit entries are immutable'); END;
    CREATE TRIGGER audit_log_no_delete BEFORE DELETE ON audit_log
    BEGIN SELECT RAISE(ABORT, 'audit entries are immutable'); END;
    """,
    # 20 — step-up authentication: when the person last proved who they are on this session
    """
    ALTER TABLE sessions ADD COLUMN reauth_at REAL;
    """,
    # 21 — embedded agent gateway: partner integrations bound to one workspace through a viewer service user,
    #      short-lived end-user sessions (hashes only), the Desk thread behind each partner thread, and usage per run
    """
    CREATE TABLE embedded_integrations (
        id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'active',
        service_user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        key_hash TEXT NOT NULL UNIQUE,
        key_prefix TEXT NOT NULL,
        allowed_origins TEXT NOT NULL DEFAULT '[]',
        capabilities TEXT NOT NULL DEFAULT '[]',
        limits TEXT NOT NULL DEFAULT '{}',
        created_by TEXT,
        created_at REAL NOT NULL,
        last_used_at REAL,
        rotated_at REAL,
        revoked_at REAL
    );
    CREATE INDEX ix_embedded_integrations_ws ON embedded_integrations(workspace_id, created_at DESC);
    CREATE TABLE embedded_sessions (
        id TEXT PRIMARY KEY,
        integration_id TEXT NOT NULL REFERENCES embedded_integrations(id) ON DELETE CASCADE,
        token_hash TEXT NOT NULL UNIQUE,
        subject_hash TEXT NOT NULL,
        subject_label TEXT,
        locale TEXT NOT NULL DEFAULT 'en',
        created_at REAL NOT NULL,
        expires_at REAL NOT NULL,
        revoked_at REAL,
        last_used_at REAL
    );
    CREATE INDEX ix_embedded_sessions_integration ON embedded_sessions(integration_id, created_at DESC);
    CREATE TABLE embedded_threads (
        integration_id TEXT NOT NULL REFERENCES embedded_integrations(id) ON DELETE CASCADE,
        subject_hash TEXT NOT NULL,
        client_thread_id TEXT NOT NULL,
        thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
        created_at REAL NOT NULL,
        PRIMARY KEY (integration_id, subject_hash, client_thread_id)
    );
    CREATE UNIQUE INDEX ux_embedded_threads_thread ON embedded_threads(thread_id);
    CREATE TABLE embedded_usage (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        integration_id TEXT NOT NULL,
        session_id TEXT,
        run_id TEXT,
        subject_hash TEXT,
        agent_id TEXT NOT NULL,
        status TEXT NOT NULL,
        input_tokens INTEGER NOT NULL DEFAULT 0,
        output_tokens INTEGER NOT NULL DEFAULT 0,
        tool_calls INTEGER NOT NULL DEFAULT 0,
        duration_ms INTEGER NOT NULL DEFAULT 0,
        created_at REAL NOT NULL
    );
    CREATE INDEX ix_embedded_usage_integration ON embedded_usage(integration_id, created_at);
    """,
    # 22 — PipesHub is gone: search, OCR and the knowledge base are built in, so the columns that tracked what had
    #      been uploaded to an external engine are dropped, with the connections that pointed at one
    """
    DROP INDEX IF EXISTS ix_documents_pipeshub;
    ALTER TABLE documents DROP COLUMN pipeshub_status;
    ALTER TABLE documents DROP COLUMN pipeshub_record_id;
    ALTER TABLE doc_collections DROP COLUMN sync_pipeshub;
    UPDATE documents SET status = 'failed', error = 'Not readable here; add a searchable copy.'
     WHERE status = 'remote';
    DELETE FROM secrets WHERE id IN (SELECT secret_id FROM connections WHERE provider = 'pipeshub');
    DELETE FROM connections WHERE provider = 'pipeshub';
    """,
]


def new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:20]}"


def now() -> float:
    return time.time()


def dumps(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), default=str)


def loads(value: str | bytes | None, default: Any = None) -> Any:
    if value in (None, ""):
        return default
    return json.loads(value)


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._write_lock = threading.RLock()
        self._all: list[sqlite3.Connection] = []

    # -- connections ---------------------------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    @property
    def conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._connect()
            self._local.conn = conn
            with self._write_lock:
                self._all.append(conn)
        return conn

    def migrate(self) -> int:
        with self._write_lock:
            current = self.conn.execute("PRAGMA user_version").fetchone()[0]
            for index, script in enumerate(MIGRATIONS[current:], start=current + 1):
                self.conn.executescript(f"BEGIN;\n{script}\nPRAGMA user_version={index};\nCOMMIT;")
            if self.path.exists():
                self.path.chmod(0o600)
            return len(MIGRATIONS)

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        """A write transaction (serialised inside this process)."""
        with self._write_lock:
            conn = self.conn
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            else:
                conn.execute("COMMIT")

    # -- helpers -------------------------------------------------------------------------
    def one(self, sql: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
        row = self.conn.execute(sql, params).fetchone()
        return dict(row) if row else None

    def all(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def scalar(self, sql: str, params: Sequence[Any] = ()) -> Any:
        row = self.conn.execute(sql, params).fetchone()
        return row[0] if row else None

    def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        with self.tx() as c:
            return c.execute(sql, params).rowcount

    def insert(self, table: str, values: dict[str, Any]) -> None:
        _check_identifier(table)
        cols = list(values)
        for col in cols:
            _check_identifier(col)
        sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})"
        with self.tx() as c:
            c.execute(sql, [values[k] for k in cols])

    def update(self, table: str, row_id: str, values: dict[str, Any], *, workspace_id: str | None = None,
               key: str = "id") -> int:
        _check_identifier(table)
        _check_identifier(key)
        for col in values:
            _check_identifier(col)
        sets = ", ".join(f"{k} = ?" for k in values)
        params: list[Any] = [*values.values(), row_id]
        sql = f"UPDATE {table} SET {sets} WHERE {key} = ?"
        if workspace_id is not None:
            sql += " AND workspace_id = ?"
            params.append(workspace_id)
        with self.tx() as c:
            return c.execute(sql, params).rowcount

    def checkpoint(self) -> None:
        """Folds the write-ahead log back into the database file. SQLite does this on its own when the log passes
        1,000 pages and no reader is in the way; a long-lived server with steady readers may never reach that quiet
        moment, so the scheduler asks for it once an hour."""
        with self._write_lock:
            try:
                self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.Error:  # pragma: no cover - a busy database is checkpointed on the next round
                pass

    def close(self) -> None:
        """Closes every connection opened by any thread (call at shutdown)."""
        with self._write_lock:
            for conn in self._all:
                try:
                    conn.close()
                except sqlite3.Error:  # pragma: no cover
                    pass
            self._all.clear()
        self._local = threading.local()


def _check_identifier(name: str) -> None:
    if not name.replace("_", "").isalnum():
        raise ValueError(f"invalid SQL identifier: {name!r}")
