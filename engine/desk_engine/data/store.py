"""Datasets: each one is a SQLite table ``ds_<id>`` with generic column keys (c0, c1, ...); the
user-facing names and types live in ``datasets.columns``. Column names from files are therefore
never used as SQL identifiers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..db import dumps, loads, new_id, now
from ..errors import Invalid, NotFound

if TYPE_CHECKING:
    from ..app import Engine

SQL_TYPES = {"number": "REAL", "date": "TEXT", "text": "TEXT"}


def table_name(dataset_id: str) -> str:
    if not dataset_id.replace("_", "").isalnum():
        raise Invalid("Bad dataset id")
    return f"ds_{dataset_id}"


def _create_table(conn: Any, name: str, columns: list[dict[str, str]]) -> None:
    defs = ", ".join(f"{c['key']} {SQL_TYPES[c['type']]}" for c in columns)
    conn.execute(f"CREATE TABLE {name} (_row INTEGER PRIMARY KEY, {defs})")


def _insert_rows(conn: Any, name: str, columns: list[dict[str, str]], rows: list[list[Any]]) -> None:
    keys = [c["key"] for c in columns]
    sql = f"INSERT INTO {name} ({', '.join(keys)}) VALUES ({', '.join('?' for _ in keys)})"
    conn.executemany(sql, rows)


def validate_columns(columns: list[dict[str, str]]) -> None:
    if not columns:
        raise Invalid("A dataset needs at least one column.")
    for i, c in enumerate(columns):
        if c["key"] != f"c{i}" or c["type"] not in SQL_TYPES:
            raise Invalid("Bad column definition.")


def create_dataset(engine: Engine, ws_id: str, *, name: str, source: str, columns: list[dict[str, str]],
                   rows: list[list[Any]], source_ref: dict[str, Any] | None = None,
                   created_by: str | None = None) -> dict[str, Any]:
    validate_columns(columns)
    did = new_id("")
    tname = table_name(did)
    ts = now()
    with engine.db.tx() as c:
        _create_table(c, tname, columns)
        _insert_rows(c, tname, columns, rows)
        c.execute(
            "INSERT INTO datasets (id, workspace_id, name, source, source_ref, columns, row_count, created_by, "
            "refreshed_at, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (did, ws_id, name[:120], source, dumps(source_ref or {}), dumps(columns), len(rows), created_by, ts, ts),
        )
    return get_dataset(engine, ws_id, did)


def replace_rows(engine: Engine, ws_id: str, dataset_id: str, columns: list[dict[str, str]],
                 rows: list[list[Any]]) -> dict[str, Any]:
    ds = get_dataset(engine, ws_id, dataset_id)
    validate_columns(columns)
    tname = table_name(ds["id"])
    with engine.db.tx() as c:
        c.execute(f"DROP TABLE IF EXISTS {tname}")
        _create_table(c, tname, columns)
        _insert_rows(c, tname, columns, rows)
        c.execute("UPDATE datasets SET columns = ?, row_count = ?, refreshed_at = ? WHERE id = ? AND workspace_id = ?",
                  (dumps(columns), len(rows), now(), ds["id"], ws_id))
    return get_dataset(engine, ws_id, dataset_id)


def upsert_named(engine: Engine, ws_id: str, *, name: str, source: str, source_ref: dict[str, Any],
                 columns: list[dict[str, str]], rows: list[list[Any]], created_by: str | None) -> dict[str, Any]:
    """Connector syncs: replace the dataset with the same name and source, or create it."""
    existing = engine.db.one("SELECT id FROM datasets WHERE workspace_id = ? AND name = ? AND source = ?",
                             (ws_id, name, source))
    if existing:
        engine.db.execute("UPDATE datasets SET source_ref = ? WHERE id = ?", (dumps(source_ref), existing["id"]))
        return replace_rows(engine, ws_id, existing["id"], columns, rows)
    return create_dataset(engine, ws_id, name=name, source=source, columns=columns, rows=rows, source_ref=source_ref,
                          created_by=created_by)


def get_dataset(engine: Engine, ws_id: str, dataset_id: str) -> dict[str, Any]:
    row = engine.db.one("SELECT * FROM datasets WHERE id = ? AND workspace_id = ?", (dataset_id, ws_id))
    if row is None:
        raise NotFound("Dataset not found.")
    row["columns"] = loads(row["columns"], [])
    row["source_ref"] = loads(row["source_ref"], {})
    return row


def find_dataset(engine: Engine, ws_id: str, ref: str) -> dict[str, Any]:
    """By id or (case-insensitive) name."""
    row = engine.db.one("SELECT id FROM datasets WHERE workspace_id = ? AND (id = ? OR lower(name) = lower(?)) "
                        "ORDER BY refreshed_at DESC LIMIT 1", (ws_id, ref, ref))
    if row is None:
        raise NotFound(f"No dataset called '{ref}'. Use list_datasets to see what exists.")
    return get_dataset(engine, ws_id, row["id"])


def list_datasets(engine: Engine, ws_id: str) -> list[dict[str, Any]]:
    rows = engine.db.all("SELECT * FROM datasets WHERE workspace_id = ? ORDER BY name", (ws_id,))
    for r in rows:
        r["columns"] = loads(r["columns"], [])
        r["source_ref"] = loads(r["source_ref"], {})
    return rows


def sample_rows(engine: Engine, ws_id: str, dataset_id: str, limit: int = 50, offset: int = 0) -> list[list[Any]]:
    ds = get_dataset(engine, ws_id, dataset_id)
    keys = ", ".join(c["key"] for c in ds["columns"])
    cur = engine.db.conn.execute(f"SELECT {keys} FROM {table_name(ds['id'])} ORDER BY _row LIMIT ? OFFSET ?",
                                 (min(limit, 1000), offset))
    return [list(r) for r in cur.fetchall()]


def delete_dataset(engine: Engine, ws_id: str, dataset_id: str) -> None:
    ds = get_dataset(engine, ws_id, dataset_id)
    with engine.db.tx() as c:
        c.execute(f"DROP TABLE IF EXISTS {table_name(ds['id'])}")
        c.execute("DELETE FROM datasets WHERE id = ? AND workspace_id = ?", (ds["id"], ws_id))


def public(ds: dict[str, Any]) -> dict[str, Any]:
    return {k: ds[k] for k in ("id", "name", "source", "columns", "row_count", "refreshed_at", "created_at",
                               "source_ref") if k in ds}
