#!/usr/bin/env python3
"""Generate the raster application icons (requires Pillow):

  assets/icon.ico, assets/icon.png            Windows exe / installer icon
  data/icons/hicolor/<N>x<N>/apps/<APP_ID>.png icon-theme PNGs (64, 128, 256)
  installer/wizard-small-<dpi>.bmp             Inno Setup wizard header image
  installer/wizard-large-<dpi>.bmp             Inno Setup finish-page image

Drawn at high resolution and downscaled, so every size stays crisp.
Design: white document with a folded corner on a blue rounded tile, a
title-block table at the foot of the sheet, and an orange badge carrying a
calendar page. The scalable version of the same design lives in
data/icons/hicolor/scalable/apps/<APP_ID>.svg (1 unit there = 8 px here).
"""

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
APP_ID = "io.github.daniel_g_carrasco.cartiglio"

S = 1024  # master canvas, downscaled at the end

BLUE = (53, 132, 228, 255)        # tile
BLUE_DARK = (26, 95, 180, 255)    # fold shadow, title block
WHITE = (255, 255, 255, 255)
PAPER_LINE = (154, 182, 217, 255)
ORANGE = (230, 97, 0, 255)        # badge


def render_master() -> Image.Image:
    """Return the icon as a 1024x1024 RGBA image."""
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # blue rounded tile
    d.rounded_rectangle((64, 64, S - 64, S - 64), radius=180, fill=BLUE)

    # document sheet with folded top-right corner
    sx0, sy0, sx1, sy1 = 280, 200, 744, 824
    fold = 120
    d.polygon(
        [(sx0, sy0), (sx1 - fold, sy0), (sx1, sy0 + fold),
         (sx1, sy1), (sx0, sy1)],
        fill=WHITE,
    )
    d.polygon(
        [(sx1 - fold, sy0), (sx1 - fold, sy0 + fold), (sx1, sy0 + fold)],
        fill=BLUE_DARK,
    )

    # two text lines
    d.rounded_rectangle((350, 330, 674, 360), radius=15, fill=PAPER_LINE)
    d.rounded_rectangle((350, 402, 614, 432), radius=15, fill=PAPER_LINE)

    # title block: a small table at the foot of the sheet
    w = 20
    d.rectangle((350, 500, 674, 750), outline=BLUE_DARK, width=w)
    d.line((350, 580, 674, 580), fill=BLUE_DARK, width=w)
    d.line((350, 660, 528, 660), fill=BLUE_DARK, width=w)
    d.line((470, 580, 470, 750), fill=BLUE_DARK, width=w)

    # badge with a calendar page
    cx, cy, r = 660, 760, 150
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=ORANGE)
    d.rectangle((588, 692, 732, 836), fill=WHITE)            # page
    d.rectangle((612, 740, 708, 812), fill=ORANGE)           # its window
    for x in (616, 680):                                     # rings
        d.rounded_rectangle((x, 668, x + 24, 712), radius=12, fill=WHITE)
    d.rectangle((628, 756, 664, 792), fill=WHITE)            # the day
    return img


def main() -> None:
    img = render_master()

    out = ROOT / "assets"
    out.mkdir(exist_ok=True)
    master = img.resize((256, 256), Image.LANCZOS)
    master.save(out / "icon.png")
    master.save(
        out / "icon.ico",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48),
               (64, 64), (128, 128), (256, 256)],
    )
    print(f"written: {out / 'icon.ico'} and icon.png")

    for size in (64, 128, 256):
        dest = ROOT / "data" / "icons" / "hicolor" / f"{size}x{size}" / "apps" / f"{APP_ID}.png"
        dest.parent.mkdir(parents=True, exist_ok=True)
        img.resize((size, size), Image.LANCZOS).save(dest)
        print(f"written: {dest.relative_to(ROOT)}")

    # Inno Setup wizard bitmaps (24-bit BMP, white background), one per DPI
    # scale; the sizes are the ones documented for WizardSmallImageFile and
    # WizardImageFile. Setup picks the closest match at run time.
    small = {100: (55, 58), 125: (64, 68), 150: (83, 80), 175: (92, 97),
             200: (110, 106), 225: (119, 123), 250: (138, 140)}
    large = {100: (164, 314), 125: (192, 386), 150: (246, 459), 175: (273, 556),
             200: (328, 604), 225: (355, 700), 250: (410, 797)}
    (ROOT / "installer").mkdir(exist_ok=True)
    for kind, sizes in (("small", small), ("large", large)):
        for dpi, (w, h) in sizes.items():
            canvas = Image.new("RGBA", (w, h), WHITE)
            side = min(w, h) - 6 if kind == "small" else int(w * 0.62)
            icon = img.resize((side, side), Image.LANCZOS)
            y = (h - side) // 2 if kind == "small" else int(h * 0.18)
            canvas.alpha_composite(icon, ((w - side) // 2, y))
            dest = ROOT / "installer" / f"wizard-{kind}-{dpi}.bmp"
            canvas.convert("RGB").save(dest, format="BMP")
        print(f"written: installer/wizard-{kind}-<dpi>.bmp ({len(sizes)} sizes)")


if __name__ == "__main__":
    main()
