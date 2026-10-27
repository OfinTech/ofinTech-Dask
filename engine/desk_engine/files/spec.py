"""What the assistant (or a person) asks for: a workbook of tables, or a report with narrative and tables.

Tables take their rows either from a dataset query (exact provenance) or from literal rows (each number is then
matched against the conversation's tool results and messages). Computed columns, totals and KPIs are formulas in
the small language of ``expr.py`` and become live Excel formulas."""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from ..data.query import QuerySpec

Format = Literal["text", "number", "integer", "money", "percent", "date"]
TotalKind = Literal["sum", "avg", "min", "max", "count", "formula", "none"]
SHEET_BAD = re.compile(r"[\[\]:*?/\\]")
KEY_RE = re.compile(r"^[a-z_][a-z0-9_]{0,40}$")


def slug(label: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", label.lower().replace("%", " pct ")).strip("_") or "col"
    return s if not s[0].isdigit() else f"c_{s}"


class ColumnSpec(BaseModel):
    label: str = Field(min_length=1, max_length=80)
    key: str | None = None
    format: Format | None = None
    formula: str | None = Field(default=None, max_length=600)
    total: TotalKind | None = None
    width: int | None = Field(default=None, ge=4, le=80)

    @field_validator("key")
    @classmethod
    def _key(cls, v: str | None) -> str | None:
        if v is not None and not KEY_RE.match(v):
            raise ValueError("keys use lower-case letters, digits and underscores")
        return v

    @property
    def k(self) -> str:
        return self.key or slug(self.label)


class Pivot(BaseModel):
    column: str = Field(description="a group_by column of the query whose values become columns")
    row_total: bool = True


class TableSpec(BaseModel):
    type: Literal["table"] = "table"
    id: str = Field(min_length=1, max_length=30)
    title: str | None = Field(default=None, max_length=160)
    query: QuerySpec | None = None
    rows: list[list[str | float | int | None]] | None = Field(default=None, max_length=2000)
    columns: list[ColumnSpec] = Field(default_factory=list, max_length=40)
    pivot: Pivot | None = None
    total_row: bool = True
    total_label: str = "Total"

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        if not re.match(r"^[A-Za-z][A-Za-z0-9_]*$", v):
            raise ValueError("table ids start with a letter and use letters, digits and underscores")
        return v

    @model_validator(mode="after")
    def _source(self) -> TableSpec:
        if (self.query is None) == (self.rows is None):
            raise ValueError(f"table '{self.id}' needs either a query or rows (not both)")
        if self.pivot and self.query is None:
            raise ValueError("pivot works with query tables")
        if self.rows is not None:
            base = [c for c in self.columns if not c.formula]
            width = max((len(r) for r in self.rows), default=0)
            if self.rows and len(base) != width:
                raise ValueError(f"table '{self.id}': rows have {width} values but {len(base)} columns without a "
                                 "formula are listed")
        keys = [c.k for c in self.columns]
        dup = {k for k in keys if keys.count(k) > 1}
        if dup:
            raise ValueError(f"table '{self.id}': duplicate column keys {sorted(dup)}; set 'key' explicitly")
        return self


class TextSpec(BaseModel):
    type: Literal["heading", "text", "note"]
    text: str = Field(min_length=1, max_length=4000)


class KpiItem(BaseModel):
    label: str = Field(min_length=1, max_length=80)
    value: str = Field(min_length=1, max_length=600, description="formula, e.g. [pl:total:amount]")
    format: Format = "money"


class KpiSpec(BaseModel):
    type: Literal["kpis"]
    items: list[KpiItem] = Field(min_length=1, max_length=12)


Block = TableSpec | TextSpec | KpiSpec


class SheetSpec(BaseModel):
    name: str = Field(min_length=1, max_length=31)
    blocks: list[Annotated[TableSpec | TextSpec | KpiSpec, Field(discriminator="type")]] = Field(min_length=1,
                                                                                                 max_length=30)

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        if SHEET_BAD.search(v) or v.strip("'") != v or v.lower() in {"sources", "review", "history"}:
            raise ValueError(f"'{v}' cannot be used as a sheet name")
        return v


class ParagraphSpec(BaseModel):
    type: Literal["paragraph"]
    text: str = Field(min_length=1, max_length=6000)


class BulletsSpec(BaseModel):
    type: Literal["bullets"]
    items: list[str] = Field(min_length=1, max_length=40)


class TableRef(BaseModel):
    type: Literal["table"]
    table: str


class SectionSpec(BaseModel):
    heading: str = Field(min_length=1, max_length=160)
    blocks: list[Annotated[ParagraphSpec | BulletsSpec | TableRef, Field(discriminator="type")]] = Field(
        min_length=1, max_length=40)


class FileSpec(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    subtitle: str | None = Field(default=None, max_length=300)
    sheets: list[SheetSpec] = Field(default_factory=list, max_length=12)
    sections: list[SectionSpec] = Field(default_factory=list, max_length=40)

    @model_validator(mode="after")
    def _check(self) -> FileSpec:
        if not self.sheets:
            raise ValueError("add at least one sheet with a table")
        names = [s.name.lower() for s in self.sheets]
        if len(set(names)) != len(names):
            raise ValueError("sheet names must be unique")
        ids = [b.id for s in self.sheets for b in s.blocks if isinstance(b, TableSpec)]
        if len(set(ids)) != len(ids):
            raise ValueError("table ids must be unique across the file")
        for sec in self.sections:
            for b in sec.blocks:
                if isinstance(b, TableRef) and b.table not in ids:
                    raise ValueError(f"section '{sec.heading}' shows table '{b.table}', which is not defined")
        return self

    @property
    def kind(self) -> str:
        return "report" if self.sections else "workbook"
