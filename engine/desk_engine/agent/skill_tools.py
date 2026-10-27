"""Tools that let the agent read skills on demand."""

from __future__ import annotations

from typing import Any

from ..skills.loader import enabled_index, get_skill
from .tools import ToolContext, ToolOutput, registry, schema


@registry.tool("load_skill", "Read the full instructions of a skill from the skill index. Load the skill before "
               "doing a task it covers.", schema({"name": {"type": "string"}}, ["name"]), category="skills")
async def _load(ctx: ToolContext, a: dict[str, Any]) -> ToolOutput:
    enabled = {s["name"] for s in enabled_index(ctx.engine, ctx.ws_id)}
    if a["name"] not in enabled:
        return ToolOutput(f"Skill '{a['name']}' is not enabled in this workspace. Enabled: {', '.join(sorted(enabled))}")
    skill = get_skill(ctx.engine, ctx.ws_id, a["name"])
    extra = ""
    if skill["files"]:
        extra = "\n\nReference files (read with read_skill_file): " + ", ".join(sorted(skill["files"]))
    return ToolOutput(f"# Skill: {skill['name']}\n\n{skill['body']}{extra}",
                      [{"type": "skill", "name": skill["name"], "description": skill["description"]}])


@registry.tool("read_skill_file", "Read a reference file that belongs to a skill.",
               schema({"skill": {"type": "string"}, "file": {"type": "string"}}, ["skill", "file"]),
               category="skills")
async def _file(ctx: ToolContext, a: dict[str, Any]) -> ToolOutput:
    skill = get_skill(ctx.engine, ctx.ws_id, a["skill"])
    content = skill["files"].get(a["file"])
    if content is None:
        return ToolOutput(f"No file '{a['file']}'. Files: {', '.join(sorted(skill['files'])) or 'none'}")
    return ToolOutput(content)
