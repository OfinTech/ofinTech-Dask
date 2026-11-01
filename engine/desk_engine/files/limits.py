"""Resource limits for files that come from outside, applied before a parser sees them.

Office files are zip archives, and a parser inflates what the archive declares. A small file can declare gigabytes
(a "zip bomb"), so the declared sizes are checked first; Python's ``zipfile`` refuses to read past a declared size,
which makes the declaration a reliable cap. Images are checked by their header before any pixel is decoded, and
only the formats a scan can arrive in are decoded at all.
"""

from __future__ import annotations

import io
import zipfile
from typing import Any

from ..errors import Invalid

MAX_OFFICE_UNCOMPRESSED = 400 * 1024 * 1024   # bytes the members of one Office file may inflate to, in total
MAX_OFFICE_MEMBERS = 20_000                    # a workbook with 10,000 sheets is not a spreadsheet
MAX_OFFICE_RATIO = 200                         # inflated / compressed, over the whole archive, for files > 1 MB inflated
MAX_IMAGE_PIXELS = 60_000_000
IMAGE_FORMATS = ["PNG", "JPEG", "TIFF", "BMP", "GIF", "WEBP"]


def check_office_zip(data: bytes, *, what: str = "file") -> None:
    """Refuses an Office archive that would inflate beyond the limits. Raises ``Invalid`` (a 422, not a crash).
    Data that is not a zip at all is left to the caller's parser, which reports it in its own words."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            infos = zf.infolist()
    except zipfile.BadZipFile:
        return
    if len(infos) > MAX_OFFICE_MEMBERS:
        raise Invalid(f"This {what} has too many parts to be read here.", code="document_too_large")
    total = sum(max(i.file_size, 0) for i in infos)
    compressed = sum(max(i.compress_size, 0) for i in infos)
    if total > MAX_OFFICE_UNCOMPRESSED or (total > 1024 * 1024 and total > MAX_OFFICE_RATIO * max(compressed, 1)):
        raise Invalid(f"This {what} would need too much memory to open here. Split it into smaller files.",
                      code="document_too_large")


def open_image(data: bytes) -> Any:
    """A Pillow image for OCR: known raster formats only, size judged from the header before decoding."""
    from PIL import Image, UnidentifiedImageError

    try:
        im = Image.open(io.BytesIO(data), formats=IMAGE_FORMATS)
    except UnidentifiedImageError as exc:
        raise ValueError("not a supported image") from exc
    except Image.DecompressionBombError as exc:   # Pillow's own ceiling, above MAX_IMAGE_PIXELS
        raise ValueError("image too large") from exc
    if im.width * im.height > MAX_IMAGE_PIXELS:
        im.close()
        raise ValueError("image too large")
    return im
