"""Generate the application icon.

A teal tile with a white "L" - the app's initial, drawn as a monogram so it
stays legible at 16 px in the taskbar and on the Start Menu.

Usage:  python tools/make_icon.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
SIZES = (16, 24, 32, 48, 64, 128, 256)

TEAL = (15, 138, 106, 255)
WHITE = (255, 255, 255, 255)


def draw_master(size: int = 1024) -> Image.Image:
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    radius = int(size * 0.22)
    draw.rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=TEAL)

    stem_width = int(size * 0.155)
    left = int(size * 0.315)
    top = int(size * 0.225)
    bottom = int(size * 0.775)
    arm = int(size * 0.44)
    stroke_radius = int(size * 0.035)

    draw.rounded_rectangle(
        (left, top, left + stem_width, bottom), radius=stroke_radius, fill=WHITE
    )
    draw.rounded_rectangle(
        (left, bottom - stem_width, left + arm, bottom),
        radius=stroke_radius,
        fill=WHITE,
    )
    return image


def main() -> int:
    ASSETS.mkdir(parents=True, exist_ok=True)
    master = draw_master()

    png_path = ASSETS / "ledger-256.png"
    master.resize((256, 256), Image.LANCZOS).save(png_path, "PNG")

    ico_path = ASSETS / "ledger.ico"
    frames = [master.resize((s, s), Image.LANCZOS) for s in SIZES]
    frames[-1].save(ico_path, format="ICO", sizes=[(s, s) for s in SIZES])

    print(f"wrote {png_path.name} and {ico_path.name} ({len(SIZES)} sizes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
