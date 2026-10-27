"""``{{ path.to.value }}`` substitution for workflow steps. Only lookups, no expressions or code."""

from __future__ import annotations

import json
import re
from typing import Any

_VAR = re.compile(r"\{\{\s*([a-zA-Z0-9_.\-]+)\s*\}\}")


def lookup(context: dict[str, Any], path: str) -> Any:
    value: Any = context
    for part in path.split("."):
        if isinstance(value, dict):
            value = value.get(part)
        elif isinstance(value, list) and part.isdigit() and int(part) < len(value):
            value = value[int(part)]
        else:
            return None
    return value


def render(template: Any, context: dict[str, Any]) -> Any:
    """Renders strings; walks lists and dicts. A string that is exactly one variable keeps the
    variable's type (so numbers and lists can be passed to tool arguments)."""
    if isinstance(template, str):
        whole = _VAR.fullmatch(template.strip())
        if whole:
            return lookup(context, whole.group(1))

        def sub(m: re.Match[str]) -> str:
            v = lookup(context, m.group(1))
            if v is None:
                return ""
            if isinstance(v, (dict, list)):
                return json.dumps(v, default=str)[:20000]
            return str(v)

        return _VAR.sub(sub, template)
    if isinstance(template, list):
        return [render(x, context) for x in template]
    if isinstance(template, dict):
        return {k: render(v, context) for k, v in template.items()}
    return template
