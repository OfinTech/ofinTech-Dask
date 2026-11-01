"""Per-workspace storage usage and quota enforcement.

Usage is what the workspace keeps in file storage: knowledge documents (``documents.size``) and the stored outputs of
editable files (``file_versions.outputs[*].size``). Datasets live in the database and are not counted. The quota is
the owning account's ``storage_quota_mb`` (hosted edition only; 0 = unlimited). A full quota refuses *new* uploads —
nothing already stored is ever removed by the quota — and, by policy, may put the workspace into read-only after a
grace period. Retention after deletion and the configured storage provider are reported to the owner, not changed here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..db import loads
from ..errors import Forbidden

if TYPE_CHECKING:
    from ..app import Engine

MB = 1024 * 1024


def usage_bytes(engine: Engine, ws_id: str) -> dict[str, int]:
    docs = int(engine.db.scalar("SELECT COALESCE(SUM(size), 0) FROM documents WHERE workspace_id = ?", (ws_id,)) or 0)
    files = 0
    for row in engine.db.all("SELECT outputs FROM file_versions WHERE workspace_id = ?", (ws_id,)):
        for out in (loads(row["outputs"], {}) or {}).values():
            if isinstance(out, dict):
                files += int(out.get("size") or 0)
    return {"documents": docs, "files": files, "total": docs + files}


def quota_bytes(engine: Engine, ws_id: str) -> int:
    """0 means unlimited."""
    from .plans import workspace_entitlements

    if not engine.settings.hosted:
        return 0
    mb = int(workspace_entitlements(engine, ws_id)["storage_quota_mb"] or 0)
    return mb * MB


def summary(engine: Engine, ws_id: str) -> dict[str, Any]:
    used = usage_bytes(engine, ws_id)
    quota = quota_bytes(engine, ws_id)
    return {**used, "quota": quota, "percent": round(100 * used["total"] / quota, 1) if quota else None,
            "over": bool(quota) and used["total"] > quota}


def check_quota(engine: Engine, ws_id: str, extra_bytes: int) -> None:
    """Before storing ``extra_bytes`` more for this workspace."""
    from .settings import feature_flag, get_setting

    if not engine.settings.hosted:
        return
    from .plans import workspace_entitlements

    ent = workspace_entitlements(engine, ws_id)
    if not ent["features"].get("file_storage", False) or not feature_flag(engine, "file_storage"):
        raise Forbidden("Hosted file storage is not included in this workspace's plan.", code="feature_not_in_plan")
    max_file = get_setting(engine, "storage.max_file_mb")
    if max_file and extra_bytes > int(max_file) * MB:
        raise Forbidden(f"Files up to {max_file} MB can be stored.", code="file_too_large")
    quota = int(ent["storage_quota_mb"] or 0) * MB
    if not quota:
        return
    used = usage_bytes(engine, ws_id)["total"]
    if used + extra_bytes > quota:
        raise Forbidden(f"This workspace's storage quota ({quota // MB} MB) would be exceeded. Delete files or upgrade.",
                        code="storage_quota", details={"quota": quota, "used": used, "adding": extra_bytes})


def platform_totals(engine: Engine) -> dict[str, Any]:
    docs = int(engine.db.scalar("SELECT COALESCE(SUM(size), 0) FROM documents") or 0)
    files = 0
    for row in engine.db.all("SELECT outputs FROM file_versions"):
        for out in (loads(row["outputs"], {}) or {}).values():
            if isinstance(out, dict):
                files += int(out.get("size") or 0)
    return {"documents": docs, "files": files, "total": docs + files,
            "document_count": int(engine.db.scalar("SELECT COUNT(*) FROM documents") or 0),
            "file_count": int(engine.db.scalar("SELECT COUNT(*) FROM files") or 0),
            "provider": engine.storage.describe() if hasattr(engine.storage, "describe") else "local",
            "encrypted": bool(engine.settings.storage.get("encrypt", True))}


def per_workspace(engine: Engine, limit: int = 200) -> list[dict[str, Any]]:
    rows = engine.db.all("SELECT id, name, created_at FROM workspaces ORDER BY created_at DESC LIMIT ?", (limit,))
    out = []
    for w in rows:
        s = summary(engine, w["id"])
        out.append({"workspace_id": w["id"], "name": w["name"], **s})
    out.sort(key=lambda r: -r["total"])
    return out
