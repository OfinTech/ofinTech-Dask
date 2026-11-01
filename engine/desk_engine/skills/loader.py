"""Skills: packaged finance know-how the agent loads on demand.

Format (compatible with the common ``SKILL.md`` convention)::

    ---
    name: month-end-close
    description: One or two sentences saying when to use the skill.
    version: 1.0.0
    tags: [close, controls]
    ---
    Markdown instructions ...

A skill may include extra reference files (Markdown, text, CSV). Skills are instructions only:
the engine never executes code from a skill, so scripts in an imported archive are ignored.

The agent sees each enabled skill's name and description; it calls ``load_skill`` to read one in
full when the conversation needs it (progressive disclosure keeps prompts short).
"""

from __future__ import annotations

import io
import re
import zipfile
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from ..db import dumps, loads, new_id, now
from ..errors import Conflict, Invalid, NotFound

if TYPE_CHECKING:
    from ..app import Engine

BUILTIN_DIR = Path(__file__).parent / "builtin"
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")
TEXT_EXTENSIONS = {".md", ".txt", ".csv", ".json"}
MAX_SKILL_BYTES = 1_000_000


def parse_skill_md(text: str) -> dict[str, Any]:
    text = text.lstrip("﻿")
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.S)
    if not m:
        raise Invalid("SKILL.md must start with a '---' front-matter block containing name and description.",
                      code="bad_skill")
    try:
        meta = yaml.safe_load(m[1]) or {}
    except yaml.YAMLError as exc:
        raise Invalid(f"The front matter is not valid YAML: {exc}", code="bad_skill") from exc
    if not isinstance(meta, dict):
        raise Invalid("The front matter must be a mapping.", code="bad_skill")
    name = str(meta.get("name", "")).strip()
    description = str(meta.get("description", "")).strip()
    if not NAME_RE.match(name):
        raise Invalid("Skill name must be 2–64 lowercase letters, digits or hyphens.", code="bad_skill")
    if not 10 <= len(description) <= 1024:
        raise Invalid("Skill description must be 10–1024 characters.", code="bad_skill")
    body = m[2].strip()
    if not body:
        raise Invalid("The skill has no instructions.", code="bad_skill")
    tags = meta.get("tags") or []
    return {
        "name": name,
        "description": description,
        "version": str(meta.get("version", "1.0.0")),
        "tags": [str(t) for t in tags][:12] if isinstance(tags, list) else [],
        "body": body,
    }


def render_skill_md(skill: dict[str, Any]) -> str:
    meta = {"name": skill["name"], "description": skill["description"], "version": skill.get("version", "1.0.0"),
            "tags": skill.get("tags", [])}
    return "---\n" + yaml.safe_dump(meta, sort_keys=False, allow_unicode=True).strip() + "\n---\n\n" + skill["body"] + "\n"


def parse_archive(data: bytes) -> tuple[dict[str, Any], dict[str, str], list[str]]:
    """Returns (skill, extra files, ignored file names)."""
    if len(data) > 5 * MAX_SKILL_BYTES:
        raise Invalid("Skill archive is larger than 5 MB.", code="bad_skill")
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise Invalid("Not a valid .zip archive.", code="bad_skill") from exc
    names = [n for n in zf.namelist() if not n.endswith("/") and "__MACOSX" not in n]
    skill_md = [n for n in names if n.rsplit("/", 1)[-1] == "SKILL.md"]
    if len(skill_md) != 1:
        raise Invalid("The archive must contain exactly one SKILL.md.", code="bad_skill")
    root = skill_md[0][: -len("SKILL.md")]
    total = 0
    files: dict[str, str] = {}
    ignored: list[str] = []
    skill = None
    for n in names:
        info = zf.getinfo(n)
        total += info.file_size
        if total > MAX_SKILL_BYTES:
            raise Invalid("Skill files are larger than 1 MB in total.", code="bad_skill")
        rel = n[len(root):] if n.startswith(root) else n
        if ".." in rel.split("/") or rel.startswith("/"):
            raise Invalid("Archive contains an unsafe path.", code="bad_skill")
        if n == skill_md[0]:
            skill = parse_skill_md(zf.read(n).decode("utf-8", errors="replace"))
            continue
        if Path(rel).suffix.lower() in TEXT_EXTENSIONS and len(files) < 20:
            files[rel] = zf.read(n).decode("utf-8", errors="replace")
        else:
            ignored.append(rel)
    assert skill is not None
    return skill, files, ignored


@lru_cache(maxsize=1)
def builtin_skills() -> dict[str, dict[str, Any]]:
    out = {}
    for path in sorted(BUILTIN_DIR.glob("*/SKILL.md")):
        skill = parse_skill_md(path.read_text(encoding="utf-8"))
        files = {p.name: p.read_text(encoding="utf-8") for p in path.parent.iterdir()
                 if p.name != "SKILL.md" and p.suffix in TEXT_EXTENSIONS}
        out[skill["name"]] = {**skill, "files": files, "source": "builtin"}
    return out


def list_skills(engine: Engine, ws_id: str) -> list[dict[str, Any]]:
    settings = {r["name"]: bool(r["enabled"]) for r in
                engine.db.all("SELECT name, enabled FROM skill_settings WHERE workspace_id = ?", (ws_id,))}
    out = []
    for name, s in builtin_skills().items():
        out.append({"id": f"builtin:{name}", "name": name, "description": s["description"], "tags": s["tags"],
                    "version": s["version"], "source": "builtin", "enabled": settings.get(name, True),
                    "files": sorted(s["files"])})
    for r in engine.db.all("SELECT * FROM skills WHERE workspace_id = ? ORDER BY name", (ws_id,)):
        out.append({"id": r["id"], "name": r["name"], "description": r["description"], "tags": loads(r["tags"], []),
                    "version": r["version"], "source": "custom", "enabled": bool(r["enabled"]),
                    "files": sorted(loads(r["files"], {})), "updated_at": r["updated_at"]})
    return out


def enabled_index(engine: Engine, ws_id: str) -> list[dict[str, Any]]:
    # a custom skill can no longer take a built-in's name (save_custom refuses it); rows that did so before that
    # rule do not shadow the built-in either
    seen: dict[str, dict[str, Any]] = {}
    for s in list_skills(engine, ws_id):
        if s["enabled"] and s["name"] not in seen:
            seen[s["name"]] = s
    return list(seen.values())


def get_skill(engine: Engine, ws_id: str, name: str) -> dict[str, Any]:
    row = engine.db.one("SELECT * FROM skills WHERE workspace_id = ? AND name = ?", (ws_id, name))
    if row:
        return {"name": row["name"], "description": row["description"], "body": row["body"],
                "tags": loads(row["tags"], []), "version": row["version"], "files": loads(row["files"], {}),
                "source": "custom", "id": row["id"], "enabled": bool(row["enabled"])}
    builtin = builtin_skills().get(name)
    if builtin:
        return {**builtin, "id": f"builtin:{name}"}
    raise NotFound(f"No skill called '{name}'.")


def save_custom(engine: Engine, ws_id: str, skill: dict[str, Any], files: dict[str, str] | None, *,
                user_id: str, skill_id: str | None = None, enable: bool = True) -> dict[str, Any]:
    """Saves a custom skill. A skill's text goes into the assistant's instructions, so a new one is switched on
    only when ``enable`` is true (the caller passes whether the author is an admin); otherwise an admin turns it on
    from the Skills page. A built-in's name cannot be taken, so no custom text can stand in for a built-in."""
    size = len(skill["body"]) + sum(len(v) for v in (files or {}).values())
    if size > MAX_SKILL_BYTES:
        raise Invalid("Skill is larger than 1 MB.", code="bad_skill")
    if skill["name"] in builtin_skills():
        raise Conflict(f"'{skill['name']}' is a built-in skill. Choose another name for your own version.",
                       code="builtin_name")
    values = {"name": skill["name"], "description": skill["description"], "body": skill["body"],
              "tags": dumps(skill.get("tags", [])), "version": skill.get("version", "1.0.0"),
              "files": dumps(files or {}), "updated_at": now()}
    clash = engine.db.one("SELECT id FROM skills WHERE workspace_id = ? AND name = ?", (ws_id, skill["name"]))
    if skill_id:
        if clash and clash["id"] != skill_id:
            raise Conflict(f"Another skill is already called '{skill['name']}'.")
        if not engine.db.update("skills", skill_id, values, workspace_id=ws_id):
            raise NotFound("Skill not found.")
    else:
        if clash:
            raise Conflict(f"A skill called '{skill['name']}' already exists. Edit it or choose another name.")
        skill_id = new_id("sk_")
        engine.db.insert("skills", {"id": skill_id, "workspace_id": ws_id, "created_by": user_id,
                                    "enabled": int(bool(enable)), **values})
    return get_skill(engine, ws_id, skill["name"])


def set_enabled(engine: Engine, ws_id: str, skill_id: str, enabled: bool) -> None:
    if skill_id.startswith("builtin:"):
        name = skill_id.split(":", 1)[1]
        if name not in builtin_skills():
            raise NotFound("Skill not found.")
        with engine.db.tx() as c:
            c.execute("INSERT INTO skill_settings (workspace_id, name, enabled) VALUES (?,?,?) "
                      "ON CONFLICT(workspace_id, name) DO UPDATE SET enabled = excluded.enabled",
                      (ws_id, name, int(enabled)))
        return
    if not engine.db.update("skills", skill_id, {"enabled": int(enabled)}, workspace_id=ws_id):
        raise NotFound("Skill not found.")
