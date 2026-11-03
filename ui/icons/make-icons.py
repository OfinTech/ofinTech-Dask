"""Regenerates the app's installed-icon set from the brand mark.

Run after the mark changes:  python ui/icons/make-icons.py

Three shapes are needed and they are not the same picture. `icon-192` and `icon-512` are the mark as it is, and
the launcher draws them whole. `icon-maskable-512` is for platforms that crop an icon to their own shape -- a
circle on one, a squircle on another -- which cuts roughly the outer tenth away on every side. The mark's tile has
rounded, transparent corners, so cropping it directly would show gaps where the corner should be; the maskable
version therefore sits on an opaque tile and is scaled into the safe area, so the crop takes background and
nothing else. `apple-touch-icon` is 180px and opaque, because iOS composites a transparent icon onto black.
"""

from __future__ import annotations

import pathlib

from PIL import Image

HERE = pathlib.Path(__file__).resolve().parent
SOURCE = HERE.parent.parent / "site" / "assets" / "ofintech-icon-512.png"
TILE = (11, 15, 20, 255)          # the mark's own tile colour, so the fill is invisible
SAFE = 0.80                       # the share of the canvas a maskable icon may rely on


def plain(size: int, name: str) -> None:
    Image.open(SOURCE).convert("RGBA").resize((size, size), Image.LANCZOS).save(HERE / name)


def on_tile(size: int, name: str, scale: float) -> None:
    canvas = Image.new("RGBA", (size, size), TILE)
    inner = int(size * scale)
    mark = Image.open(SOURCE).convert("RGBA").resize((inner, inner), Image.LANCZOS)
    offset = (size - inner) // 2
    canvas.alpha_composite(mark, (offset, offset))
    canvas.save(HERE / name)


if __name__ == "__main__":
    plain(192, "icon-192.png")
    plain(512, "icon-512.png")
    on_tile(512, "icon-maskable-512.png", SAFE)
    on_tile(180, "apple-touch-icon.png", 1.0)
    for f in sorted(HERE.glob("*.png")):
        print(f"{f.name:28} {Image.open(f).size}")
