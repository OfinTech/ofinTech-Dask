"""Builds the engine into a self-contained folder (dist/desk-engine) with PyInstaller.
Run on each target OS (the desktop installers bundle this folder):

    pip install -r requirements-dev.txt
    python packaging/build_engine.py
"""

import os
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
sep = ";" if sys.platform == "win32" else ":"
cmd = [
    sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--name", "desk-engine",
    "--distpath", str(root / "dist"), "--workpath", str(root / "build"), "--specpath", str(root / "build"),
    "--paths", str(root),
    "--add-data", f"{root / 'desk_engine' / 'skills' / 'builtin'}{sep}desk_engine/skills/builtin",
    "--collect-submodules", "desk_engine",
    "--collect-submodules", "uvicorn",
    "--collect-all", "mcp",
    "--collect-all", "mcp_types",
    "--collect-submodules", "httpx2",
    "--collect-data", "docx",
    "--collect-submodules", "pypdf",
    str(root / "packaging" / "entry.py"),
]
print(" ".join(cmd))
# desk_engine is not installed by requirements-dev.txt, and running this file by path puts packaging/ on
# sys.path rather than the engine root. --paths covers PyInstaller's own analysis, but --collect-submodules
# and --collect-all import the package for real, in isolated subprocesses that go by the interpreter's own
# sys.path. Those inherit PYTHONPATH, so the root belongs there too.
env = dict(os.environ)
env["PYTHONPATH"] = os.pathsep.join([str(root), *([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])])
subprocess.run(cmd, check=True, env=env)
