"""Where the real corpus is allowed to live, and where it is not.

The documents this benchmark scores are client bank statements and client closes. They carry names, IBANs and
counterparties, and the repository rule for this product is that production data and real financial statements never
enter it. A rule that depends on remembering it gets broken, so every command here resolves its data root through
`data_root()`, which refuses a directory inside a git work tree. That is the one mechanical guarantee: the corpus
cannot be committed by accident because it cannot be written next to a `.git` directory in the first place.

The root comes from `OFIN_BENCH_ROOT`, or from `--root` on any command. There is deliberately no default inside the
repository and no fallback: a missing root is an error, not a reason to pick somewhere convenient.
"""

from __future__ import annotations

import os
import pathlib

ENV_VAR = "OFIN_BENCH_ROOT"
KINDS = ("bank-statement", "monthly-close")


class RootError(Exception):
    """The data root is missing, or sits somewhere the corpus must not be written."""


def _git_work_tree(path: pathlib.Path) -> pathlib.Path | None:
    """The nearest ancestor holding a `.git`, or None. A `.git` file counts: that is how a worktree or submodule looks."""
    for candidate in (path, *path.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def data_root(explicit: str | None = None, *, create: bool = False) -> pathlib.Path:
    """Resolve the corpus root, refusing anything a commit could reach.

    `explicit` wins over the environment variable. The directory must exist unless `create` is set, and must not sit
    inside a git work tree at any depth.
    """
    raw = explicit or os.environ.get(ENV_VAR)
    if not raw:
        raise RootError(
            f"No data root. Set {ENV_VAR} to a directory outside any git repository, or pass --root.\n"
            "The corpus holds client data and must never sit inside a checkout."
        )
    root = pathlib.Path(raw).expanduser()
    if root.exists():
        root = root.resolve()
    else:
        if not create:
            raise RootError(f"Data root does not exist: {root}")
        root = root.absolute()

    tree = _git_work_tree(root)
    if tree is not None:
        raise RootError(
            f"Refusing to use {root}: it is inside the git repository at {tree}.\n"
            "Client statements must not live in a checkout. Choose a directory outside it."
        )
    if create:
        root.mkdir(parents=True, exist_ok=True)
        for kind in KINDS:
            (root / "cases" / kind).mkdir(parents=True, exist_ok=True)
        for sub in ("items", "runs", "keys"):
            (root / sub).mkdir(parents=True, exist_ok=True)
    return root


def case_dir(root: pathlib.Path, kind: str, case_id: str) -> pathlib.Path:
    if kind not in KINDS:
        raise RootError(f"Unknown case kind {kind!r}; expected one of {', '.join(KINDS)}")
    if not case_id or "/" in case_id or case_id.startswith("."):
        raise RootError(f"Bad case id {case_id!r}")
    return root / "cases" / kind / case_id


def iter_cases(root: pathlib.Path, kind: str | None = None) -> list[tuple[str, pathlib.Path]]:
    """Every case folder holding a truth.json, as (kind, path), sorted so a run is reproducible."""
    found: list[tuple[str, pathlib.Path]] = []
    for k in KINDS if kind is None else (kind,):
        base = root / "cases" / k
        if not base.is_dir():
            continue
        for path in sorted(base.iterdir()):
            if path.is_dir() and (path / "truth.json").is_file():
                found.append((k, path))
    return found
