"""Harbor Line Logistics: a company a person can look at before they have books of their own.

A new workspace shows "No general ledger yet" and nothing else. The promise the product is sold on -- every figure
traced to its source -- cannot be seen until someone has exported their own ledger and imported it, which is a
long way to walk on trust. These are the three invented files the README has always pointed at, loaded in one
click into a workspace that has nothing.

They go in through the same reader a person's own upload goes through (``data.importer``), not by a private path
that writes rows straight into a table: if the shape of the demo ledger stopped matching what an imported ledger
has to look like, that would be a fact worth finding out here rather than a fact hidden by a shortcut. The
general ledger's columns are ``connectors.accounting.GL_COLUMNS`` exactly, which is what makes
``costcentres.is_ledger`` accept it, and what lets the overview, the statements and the analysis fill in from one
import and gives the close and the cost centres a ledger to work on.

Every dataset loaded here carries ``source_ref["demo"] = True``. That one mark is what names it as demo data on
the screen and what ``remove`` deletes, so a workspace can be put back exactly as it was.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .data import importer, store
from .errors import Invalid

if TYPE_CHECKING:
    from .engine import Engine

#: The company's name as it appears on the screen, in front of every dataset this loads.
COMPANY = "Harbor Line (demo)"

#: The files, in the order they are loaded, and what each is for. The general ledger is first and is the one that
#: matters: the other two are what Cost centres and Month-end close read, and are no use without it.
FILES: tuple[tuple[str, str], ...] = (
    ("HarborLine_General_Ledger.xlsx", "General ledger"),
    ("EWF_Project_Budget.xlsx", "Project budget"),
    ("HarborLine_Bank_Statement_2026-10.csv", "Bank statement"),
)


def directory() -> Path | None:
    """Where the files are, or None when this build does not carry them.

    Four places, in order: what the operator set (``DESK_DEMO_DIR``), beside this package, which is where the
    desktop app's bundle carries them (``packaging/build_engine.py``), where the server image puts them
    (``server/Dockerfile``), and the checkout a developer runs from. A build without them is not broken -- it
    simply has no demo to offer, and the routes say so rather than failing halfway through a load.
    """
    here = Path(__file__).resolve()
    candidates = [
        Path(os.environ["DESK_DEMO_DIR"]) if os.environ.get("DESK_DEMO_DIR") else None,
        here.parent / "demo_company",
        Path("/app/demo"),
        *(p / "examples/demo-company/HarborLine-demo/ofintech-desk" for p in here.parents[:5]),
    ]
    for c in candidates:
        if c and (c / FILES[0][0]).is_file():
            return c
    return None


def available() -> bool:
    return directory() is not None


def loaded(engine: Engine, ws_id: str) -> list[dict[str, Any]]:
    """The demo datasets in this workspace, in the order of ``FILES``. Empty when none were loaded.

    The order is the file order, not the name order the store returns, so that loading a workspace that already
    holds the demo answers with the same list in the same order as the load that created it -- and so that the
    general ledger, the one that matters, is always first.
    """
    rank = {filename: i for i, (filename, _) in enumerate(FILES)}
    mine = [d for d in store.list_datasets(engine, ws_id) if (d.get("source_ref") or {}).get("demo")]
    return sorted(mine, key=lambda d: (rank.get((d.get("source_ref") or {}).get("filename"), len(FILES)), d["name"]))


def load(engine: Engine, ws_id: str, user_id: str) -> list[dict[str, Any]]:
    """Read every demo file into the workspace as a dataset. Returns what was created.

    Already loaded is not an error and not a second copy: the datasets that are there are returned as they are.
    """
    existing = loaded(engine, ws_id)
    if existing:
        return existing
    folder = directory()
    if folder is None:
        raise Invalid("This build does not carry the demo company.", code="no_demo")

    created: list[dict[str, Any]] = []
    try:
        for filename, label in FILES:
            data = (folder / filename).read_bytes()
            tables = importer.read_workbook(data, filename)
            if not tables:
                raise Invalid(f"{filename} holds no table.", code="bad_demo")
            shaped = importer.shape(tables[0])
            if not shaped.rows:
                raise Invalid(f"{filename} holds no rows.", code="bad_demo")
            created.append(store.create_dataset(
                engine, ws_id, name=f"{COMPANY} · {label}",
                source="csv" if filename.lower().endswith(".csv") else "excel",
                columns=[c.to_dict() for c in shaped.columns], rows=shaped.rows,
                source_ref={"filename": filename, "sheet": shaped.name, "demo": True, "company": COMPANY},
                created_by=user_id))
    except Exception:
        # A workspace left holding two of three files is worse than one holding none: the overview would show
        # figures from a ledger whose budget never arrived, and nobody would know which half was missing.
        for ds in created:
            try:
                store.delete_dataset(engine, ws_id, ds["id"])
            except Exception:                      # noqa: BLE001 -- the first failure is the one worth raising
                pass
        raise
    return created


def remove(engine: Engine, ws_id: str) -> int:
    """Delete every dataset this loaded, and nothing else. Returns how many went."""
    gone = 0
    for ds in loaded(engine, ws_id):
        store.delete_dataset(engine, ws_id, ds["id"])
        gone += 1
    return gone
