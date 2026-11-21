"""Loading and removing the Harbor Line demo company (``demo.py``).

Editor, not admin: putting invented figures into a workspace and taking them out again is ordinary data work, and
asking for an administrator would put the first useful thing a new person can do behind the one role a trial
workspace often has only one of.

Reading the files and reshaping the ledger's rows is CPU work, so it runs off the event loop exactly as an
uploaded file does.
"""

from __future__ import annotations

import asyncio
from typing import Any

from .. import demo
from ..web import Ctx, Router

router = Router()


def _dataset(ds: dict[str, Any]) -> dict[str, Any]:
    return {"id": ds["id"], "name": ds["name"], "row_count": ds.get("row_count"), "source": ds.get("source")}


@router.get("/api/w/{ws}/demo", role="viewer")
async def state(ctx: Ctx) -> dict[str, Any]:
    """Whether this build carries the demo, and what of it is already in this workspace."""
    datasets = await asyncio.to_thread(demo.loaded, ctx.engine, ctx.ws_id)
    return {"available": demo.available(), "company": demo.COMPANY,
            "loaded": [_dataset(d) for d in datasets]}


@router.post("/api/w/{ws}/demo", role="editor")
async def load(ctx: Ctx) -> dict[str, Any]:
    datasets = await asyncio.to_thread(demo.load, ctx.engine, ctx.ws_id, ctx.user_id)
    return {"company": demo.COMPANY, "loaded": [_dataset(d) for d in datasets]}


@router.delete("/api/w/{ws}/demo", role="editor", fresh=True)
async def remove(ctx: Ctx) -> dict[str, Any]:
    removed = await asyncio.to_thread(demo.remove, ctx.engine, ctx.ws_id)
    return {"removed": removed}
