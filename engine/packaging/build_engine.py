"""Builds the engine into a self-contained folder (dist/desk-engine) with PyInstaller.
Run on each target OS (the desktop installers bundle this folder):

    pip install -r requirements-dev.txt
    python packaging/build_engine.py
"""

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
subprocess.run(cmd, check=True)
