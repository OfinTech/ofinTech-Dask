"""scripts/install.sh, run for real against a stand-in docker.

The script's whole job is to be right on a machine nobody here can see, so what is checked is what it writes and
what it asks docker to do -- with `docker` and `curl` replaced by shell stubs that record their arguments. The
last check is the one that matters most: the key the script makes is fed to the engine's own `_decode_key`, so
the script and the engine cannot drift apart without this failing.

Usage: python e2e/install_sh.py [out-dir]
"""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "install.sh"
checks: list[dict] = []


def check(name: str, expected: object, actual: object) -> None:
    ok = expected == actual
    checks.append({"check": name, "expected": expected, "actual": actual, "ok": ok})
    print(("PASS " if ok else "FAIL ") + name)
    if not ok:
        print(f"   expected {expected!r}\n   actual   {actual!r}")


# Everything the script calls that is not a shell builtin. PATH is this and nothing else, so "a machine with no
# docker" really has none -- the first version of this test put the stubs in front of /usr/bin, where the runner's
# own docker answered instead and the no-docker case passed for the wrong reason.
NEEDS = ("mkdir", "dd", "base64", "tr", "wc", "cat", "sed", "grep", "mktemp", "rm", "uname", "sleep",
         "tail", "head", "openssl")


def stubs(where: Path, *, docker: bool = True, running: bool = True, compose: bool = True,
          healthy: bool = True, up: bool = True) -> Path:
    """A directory to be the whole of PATH, holding the commands the script leans on."""
    bin_ = where / "bin"
    bin_.mkdir(parents=True, exist_ok=True)
    for name in NEEDS:
        found = next((d / name for d in (Path("/usr/bin"), Path("/bin"), Path("/usr/sbin")) if (d / name).exists()), None)
        if found and not (bin_ / name).exists():
            (bin_ / name).symlink_to(found)
    log = where / "calls.log"
    if docker:
        (bin_ / "docker").write_text(f"""#!/bin/sh
printf 'docker %s\\n' "$*" >> {log}
case "$1 $2" in
  "version "*)  {"exit 0" if running else "echo 'Cannot connect to the Docker daemon' >&2; exit 1"} ;;
  "compose version") {"exit 0" if compose else "echo 'docker: unknown command: compose' >&2; exit 1"} ;;
  "compose pull") exit 0 ;;
  "compose up") {"exit 0" if up else "echo 'port is already allocated' >&2; exit 1"} ;;
esac
exit 0
""")
        (bin_ / "docker").chmod(0o755)
    (bin_ / "curl").write_text(f"""#!/bin/sh
printf 'curl %s\\n' "$*" >> {log}
exit {0 if healthy else 7}
""")
    (bin_ / "curl").chmod(0o755)
    return bin_


def run(home: Path, bin_: Path, **env: str) -> subprocess.CompletedProcess[str]:
    e = {**os.environ, "HOME": str(home), "PATH": str(bin_), "OFINTECH_WAIT": "2", **env}
    return subprocess.run(["/bin/sh", str(SCRIPT)], capture_output=True, text=True, env=e, timeout=120, check=False)


def main(out: Path | None) -> int:
    # ---- a machine with no docker, and one where it is not running: refuse, and write nothing
    for name, kw, says in (("no docker at all", {"docker": False}, "has no docker command"),
                           ("docker installed but not running", {"running": False}, "not running"),
                           ("docker without the compose plugin", {"compose": False}, "no 'compose' command")):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            home.mkdir()
            r = run(home, stubs(Path(tmp), **kw))
            check(f"{name}: refuses, says why, and leaves no directory behind",
                  [1, True, False], [r.returncode, says in r.stderr, (home / "ofintech-desk").exists()])

    # ---- a port that is not a number is refused before anything is done
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp) / "home"
        home.mkdir()
        r = run(home, stubs(Path(tmp)), OFINTECH_PORT="eighty")
        check("a port that is not a number is refused", [1, True], [r.returncode, "must be a number" in r.stderr])

    # ---- a fresh install
    with tempfile.TemporaryDirectory() as tmp:
        home, where = Path(tmp) / "home", Path(tmp)
        home.mkdir()
        bin_ = stubs(where)
        r = run(home, bin_)
        dir_ = home / "ofintech-desk"
        env_text = (dir_ / ".env").read_text() if (dir_ / ".env").exists() else ""
        compose = (dir_ / "compose.yml").read_text() if (dir_ / "compose.yml").exists() else ""
        calls = (where / "calls.log").read_text()
        key = next((l.split("=", 1)[1] for l in env_text.splitlines() if l.startswith("DESK_MASTER_KEY=")), "")

        check("a fresh install succeeds and says where Desk is",
              [0, True], [r.returncode, "Desk is running at http://localhost:8700" in r.stdout])
        check("it writes .env and compose.yml and nothing else",
              ["compose.yml"], sorted(p.name for p in dir_.iterdir() if not p.name.startswith(".")))
        check("the .env is readable by its owner only", "600",
              oct(stat.S_IMODE((dir_ / ".env").stat().st_mode))[2:])
        check("the key is base64 of 32 bytes, urlsafe, unpadded",
              [43, True, True], [len(key), bool(re.fullmatch(r"[A-Za-z0-9_-]+", key)), "=" not in key])
        check("the engine's own _decode_key accepts it", 32, len(decode_key(key)))
        ports = next((l.strip() for l in compose.splitlines() if l.strip().startswith("ports:")), "")
        check("it binds the loopback address, not every address",
              'ports: ["127.0.0.1:${OFINTECH_PORT:?}:8700"]', ports)
        check("plain HTTP is allowed and the public URL matches the port", [True, True],
              ['DESK_ALLOW_HTTP: "1"' in compose, "http://localhost:${OFINTECH_PORT:?}" in compose])
        check("the master key stays in .env and is not written into the compose file",
              [True, False], ["DESK_MASTER_KEY: ${DESK_MASTER_KEY:?}" in compose, key in compose])
        check("it pulled, then started, then asked /health",
              ["docker compose pull", "docker compose up", "curl"],
              [" ".join(l.split()[:3]).rstrip(" -fsSo") for l in calls.splitlines()
               if l.startswith(("docker compose pull", "docker compose up", "curl"))][:3])
        check("the data lives in a named volume, so `down` does not delete it", True,
              "desk_data:/var/lib/ofintech-desk" in compose)

        # ---- running it again is the upgrade, and must not touch the key
        before = (dir_ / ".env").read_text()
        (dir_ / "compose.yml").write_text("# edited by hand\n")
        r2 = run(home, bin_)
        check("running it again keeps the key and the rest of the .env exactly",
              [0, True, True], [r2.returncode, before == (dir_ / ".env").read_text(),
                                "keeping the key" in r2.stdout])
        check("running it again rewrites the compose file", True,
              "# edited by hand" not in (dir_ / "compose.yml").read_text())

        # ---- a new port on an upgrade is written back and used in what it prints
        r3 = run(home, bin_, OFINTECH_PORT="8899")
        env3 = (dir_ / ".env").read_text()
        check("a new port on an upgrade is stored once and printed",
              [1, True, True], [env3.count("OFINTECH_PORT="), "OFINTECH_PORT=8899" in env3,
                                "http://localhost:8899" in r3.stdout])
        check("and the key is still the one from the first run", True,
              f"DESK_MASTER_KEY={key}" in env3)

        # ---- the image can be pointed at a mirror, and pinned to a version
        r4 = run(home, bin_, OFINTECH_IMAGE="mirror.example.test/ofintech0/ofintech-desk", OFINTECH_TAG="1.4.0")
        env4 = (dir_ / ".env").read_text()
        check("the registry and tag can be changed on an upgrade", [True, True, True],
              ["OFINTECH_IMAGE=mirror.example.test/ofintech0/ofintech-desk" in env4, "OFINTECH_TAG=1.4.0" in env4,
               "mirror.example.test/ofintech0/ofintech-desk:1.4.0" in r4.stdout])

    # ---- docker refusing to start it, and an engine that never answers, are both reported with the next step
    with tempfile.TemporaryDirectory() as tmp:
        home, where = Path(tmp) / "home", Path(tmp)
        home.mkdir()
        r = run(home, stubs(where, up=False))
        check("a port already in use is reported with the setting that fixes it",
              [1, True], [r.returncode, "OFINTECH_PORT=8800" in r.stderr])

    with tempfile.TemporaryDirectory() as tmp:
        home, where = Path(tmp) / "home", Path(tmp)
        home.mkdir()
        r = run(home, stubs(where, healthy=False))
        check("an engine that never answers is reported, with where to look",
              [1, True], [r.returncode, "logs" in r.stderr])

    if out:
        out.mkdir(parents=True, exist_ok=True)
        (out / "install-sh-evidence.json").write_text(json.dumps(checks, indent=1))
    bad = [c for c in checks if not c["ok"]]
    print(f"{len(checks) - len(bad)}/{len(checks)} checks passed")
    return 1 if bad else 0


def decode_key(raw: str) -> bytes:
    # Imported here and not at the top: everything above runs the script with a PATH of its own and has no
    # business importing the engine.
    sys.path.insert(0, str(ROOT / "engine"))
    from desk_engine.config import _decode_key
    return _decode_key(raw)


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1]) if len(sys.argv) > 1 else None))
