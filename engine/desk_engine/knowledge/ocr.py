"""Local OCR with the Tesseract command-line tool, for scanned PDFs and images.

Tesseract (Apache-2.0) is an optional system package (``tesseract-ocr`` plus language packs such as ``-ara``, ``-spa``,
``-fra``); the Team Server image installs it. Pillow reads and normalises the images for it and is a requirement of
the engine, so ``available()`` checks for both rather than letting a scan fail part-way. Without it, such files are
refused with a clear message. DESK_OCR=off turns local OCR off; DESK_OCR_LANGS chooses languages
(default: every installed one of eng, ara, spa, fra).
"""

from __future__ import annotations

import functools
import importlib.util
import io
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

WANTED = ["eng", "ara", "spa", "fra"]
MAX_OCR_PAGES = 200


@functools.lru_cache(maxsize=1)
def tesseract() -> str | None:
    if os.environ.get("DESK_OCR", "auto").lower() in {"off", "0", "false", "no"}:
        return None
    return shutil.which(os.environ.get("DESK_TESSERACT", "tesseract"))


@functools.lru_cache(maxsize=1)
def languages() -> str:
    exe = tesseract()
    if not exe:
        return ""
    wanted = [x for x in os.environ.get("DESK_OCR_LANGS", "+".join(WANTED)).split("+") if x]
    try:
        out = subprocess.run([exe, "--list-langs"], capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    have = {line.strip() for line in out.splitlines()[1:]}
    langs = [x for x in wanted if x in have] or (["eng"] if "eng" in have else [])
    return "+".join(langs)


@functools.lru_cache(maxsize=1)
def pillow() -> bool:
    """Every image is normalised with Pillow before Tesseract reads it, so OCR needs both."""
    return importlib.util.find_spec("PIL") is not None


def available() -> bool:
    return bool(tesseract() and languages()) and pillow()


def image_text(data: bytes) -> str:
    """Text of one image (PNG, JPEG, TIFF…). Images are normalised with Pillow first, which also refuses non-images."""
    from ..files.limits import open_image

    exe = tesseract()
    if not exe:
        return ""
    with open_image(data) as im:   # known raster formats only; the pixel count is checked before decoding
        im.load()
        frame = im.convert("L")
        if max(frame.size) < 1200:  # small scans read better when enlarged
            scale = 1200 / max(frame.size)
            frame = frame.resize((int(frame.width * scale), int(frame.height * scale)))
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "page.png"
            frame.save(src)
            result = subprocess.run([exe, str(src), "stdout", "-l", languages(), "--psm", "3"], capture_output=True,
                                    text=True, timeout=120)
    return result.stdout if result.returncode == 0 else ""


def pdf_page_images(data: bytes) -> list[list[bytes]]:
    """The embedded images of each page (a scanned PDF has one image per page)."""
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    pages: list[list[bytes]] = []
    for page in reader.pages[:MAX_OCR_PAGES]:
        imgs = []
        try:
            for img in page.images:
                imgs.append(img.data)
        except Exception:  # an unreadable image should not stop the other pages
            pass
        pages.append(imgs)
    return pages
