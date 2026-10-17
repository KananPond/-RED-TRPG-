"""Build the AI theatre's static assets.

Two sources are used:

1. **Official free art** from the local ``.reference/Cyberpunk_RED_Easy_Mode_CN.pdf``
   (the same free Easy Mode booklet whose text the module already adapts).  The
   pregen renders and a few scene illustrations are cropped, re-graded and
   half-toned so the theatre reads as one coherent, worn dossier.
2. **Procedurally painted sets** drawn here with Pillow for the scenes the
   booklet does not illustrate (safehouse, back room, warehouse, dawn).

Everything is written into ``trpg/web/static/cinema/`` so the game stays offline.
This script is deterministic; re-run it after replacing the source PDF.

Usage::

    python tools/build_cinema_assets.py [--pdf PATH] [--out DIR]
"""

from __future__ import annotations

import argparse
import math
import os
import random
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageOps

W, H = 1920, 1080
OUT = Path("trpg/web/static/cinema")
PDF = Path(".reference/Cyberpunk_RED_Easy_Mode_CN.pdf")

# 300 dpi crop boxes in page fractions: (page, name, x0, x1, y0, y1)
PREGEN_PAGES = {
    41: ("forty", (0.07, 0.98, 0.00, 0.40), (0.32, 0.96, 0.00, 0.62)),
    43: ("mover", (0.02, 0.98, 0.03, 0.28), (0.06, 0.97, 0.00, 0.60)),
    45: ("torch", (0.04, 0.98, 0.03, 0.28), (0.08, 0.98, 0.00, 0.60)),
    47: ("redtail", (0.03, 0.99, 0.02, 0.28), (0.07, 0.98, 0.00, 0.60)),
    49: ("media", (0.03, 0.99, 0.02, 0.27), (0.07, 0.98, 0.00, 0.60)),
}
# page -> name -> (x0, x1, y0, y1) on a 300dpi render
SCENE_PAGES = {
    1: ("title", (0.00, 1.00, 0.28, 0.93)),
    4: ("ambush", (0.00, 0.92, 0.57, 0.91)),
    18: ("concert", (0.00, 0.96, 0.65, 0.93)),
    51: ("street", (0.00, 1.00, 0.31, 0.83)),
}


# ---------------------------------------------------------------------------
# generic helpers
# ---------------------------------------------------------------------------
def _rng(seed: int = 7) -> random.Random:
    return random.Random(seed)


def _canvas(top: tuple[int, int, int], bottom: tuple[int, int, int]) -> Image.Image:
    im = Image.new("RGB", (W, H), top)
    d = ImageDraw.Draw(im)
    for y in range(H):
        t = y / (H - 1)
        d.line([(0, y), (W, y)],
               fill=tuple(int(top[i] * (1 - t) + bottom[i] * t) for i in range(3)))
    return im


def _grain(im: Image.Image, amount: float = 7.0, seed: int = 11) -> Image.Image:
    a = np.asarray(im).astype(np.int16)
    n = np.random.default_rng(seed).normal(0, amount, (a.shape[0], a.shape[1], 1))
    return Image.fromarray(np.clip(a + n, 0, 255).astype(np.uint8))


def _vignette(im: Image.Image, strength: float = 0.9) -> Image.Image:
    w, h = im.size
    yy, xx = np.mgrid[0:h, 0:w]
    r = np.sqrt(((xx - w / 2) / (w * 0.62)) ** 2 + ((yy - h / 2) / (h * 0.62)) ** 2)
    v = np.clip(1 - strength * np.clip(r - 0.18, 0, None) ** 1.55, 0, 1)
    a = np.asarray(im).astype(np.float32) * v[..., None]
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))


def _rain(im: Image.Image, density: int = 700, slant: float = -0.12,
          alpha: int = 30, seed: int = 5) -> Image.Image:
    rng = _rng(seed)
    layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    w, h = im.size
    for _ in range(density):
        x, y = rng.randrange(-100, w + 100), rng.randrange(-100, h)
        length = rng.uniform(8, 58)
        d.line([(x, y), (x + slant * length, y + length)],
               fill=(198, 218, 222, rng.randint(3, alpha)), width=1)
    return Image.alpha_composite(im.convert("RGBA"), layer).convert("RGB")


def _brush(im: Image.Image, palette: list[tuple[int, int, int]], n: int = 300,
           alpha: int = 16, seed: int = 3) -> Image.Image:
    rng = _rng(seed)
    layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    w, h = im.size
    for _ in range(n):
        x, y = rng.randrange(-120, w + 120), rng.randrange(-120, h + 120)
        ang = rng.uniform(-0.22, 0.22) + rng.choice([0, math.pi / 2, math.pi / 2])
        length = rng.uniform(18, 180)
        width = rng.uniform(2, 10)
        d.line([(x, y), (x + math.cos(ang) * length, y + math.sin(ang) * length)],
               fill=rng.choice(palette) + (alpha,), width=int(width))
    layer = layer.filter(ImageFilter.GaussianBlur(1.1))
    return Image.alpha_composite(im.convert("RGBA"), layer).convert("RGB")


def _grade(im: Image.Image, *, desat: float = 0.42, brightness: float = 0.82,
           contrast: float = 1.06, shadow=(11, 22, 28), high=(238, 184, 116),
           grain: float = 6.0, vignette: float = 0.85, seed: int = 9) -> Image.Image:
    """Unify photo/render/vector sources into one bruised, filmic palette."""
    im = im.convert("RGB")
    gray = ImageOps.grayscale(im)
    colored = Image.blend(Image.merge("RGB", (gray, gray, gray)), im, desat)
    colored = ImageEnhance.Brightness(colored).enhance(brightness)
    colored = ImageEnhance.Contrast(colored).enhance(contrast)
    a = np.asarray(colored).astype(np.float32) / 255.0
    lum = a.mean(axis=2, keepdims=True)
    sh = np.array(shadow, dtype=np.float32) / 255.0
    hi = np.array(high, dtype=np.float32) / 255.0
    a = a * (1 - lum * 0.45) + (sh * (1 - lum) + hi * lum) * 0.18
    colored = Image.fromarray(np.clip(a * 255, 0, 255).astype(np.uint8))
    colored = _grain(colored, grain, seed)
    return _vignette(colored, vignette)


def _fit(im: Image.Image, w: int = W, h: int = H) -> Image.Image:
    """Cover-fit without distortion, then center crop."""
    src_ratio = im.width / im.height
    dst_ratio = w / h
    if src_ratio > dst_ratio:
        nh = h
        nw = int(h * src_ratio)
    else:
        nw = w
        nh = int(w / src_ratio)
    im = im.resize((nw, nh), Image.Resampling.LANCZOS)
    x = max(0, (nw - w) // 2)
    y = max(0, (nh - h) // 2)
    return im.crop((x, y, x + w, y + h))


def _save(im: Image.Image, name: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / name
    im.save(path, quality=90, optimize=True)
    print(f"  {path}  {path.stat().st_size // 1024} KB")


# ---------------------------------------------------------------------------
# procedural sets
# ---------------------------------------------------------------------------
def paint_loft() -> None:
    im = _canvas((9, 15, 20), (20, 25, 27))
    d = ImageDraw.Draw(im, "RGBA")
    d.rectangle([90, 60, 690, 570], fill=(17, 48, 55, 255))
    d.rectangle([90, 60, 690, 570], outline=(4, 7, 9, 255), width=11)
    for x in range(180, 690, 125):
        d.line([(x, 65), (x, 565)], fill=(4, 8, 10, 255), width=8)
    for y in range(175, 565, 122):
        d.line([(95, y), (685, y)], fill=(4, 8, 10, 255), width=8)
    for x, y, w, h, c in [
        (130, 130, 56, 10, (255, 70, 95, 150)),
        (480, 105, 92, 9, (60, 210, 220, 135)),
        (285, 335, 24, 72, (235, 160, 70, 90)),
        (560, 290, 18, 128, (245, 70, 80, 75)),
    ]:
        d.rectangle([x, y, x + w, y + h], fill=c)
    # table and bottles
    d.polygon([(380, 615), (1210, 615), (1330, 900), (250, 900)],
              fill=(36, 30, 25, 255))
    d.polygon([(380, 615), (1210, 615), (1185, 675), (400, 675)],
              fill=(58, 48, 37, 255))
    for x in (520, 590, 660):
        d.rounded_rectangle([x, 520, x + 25, 625], radius=7, fill=(54, 65, 54, 230))
        d.rectangle([x + 6, 500, x + 19, 530], fill=(48, 56, 47, 230))
    # tipped chair and a wall of cables
    d.line([(150, 0), (150, 210), (240, 290)], fill=(5, 7, 8, 255), width=7)
    d.line([(1430, 0), (1430, 320), (1250, 320)], fill=(6, 8, 9, 255), width=5)
    # TV static
    d.rectangle([1110, 360, 1460, 570], fill=(8, 9, 11, 255))
    d.rectangle([1126, 376, 1444, 554], fill=(68, 96, 94, 185))
    rng = _rng(21)
    for _ in range(160):
        x, y = rng.randrange(280, 1340), rng.randrange(590, 900)
        d.ellipse([x, y, x + rng.randint(12, 60), y + rng.randint(3, 12)],
                  fill=(18, 16, 14, 36))
    # hanging bulb glow
    d.line([(980, 0), (980, 220)], fill=(7, 7, 7, 255), width=3)
    d.ellipse([962, 220, 1000, 258], fill=(255, 212, 135, 255))
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    for r in range(320, 10, -16):
        gd.ellipse([980 - r, 235 - r * .35, 980 + r, 235 + r * .65],
                   fill=(255, 186, 98, max(1, int((320 - r) / 320 * 18))))
    im = Image.alpha_composite(im.convert("RGBA"),
                               glow.filter(ImageFilter.GaussianBlur(32))).convert("RGB")
    im = _brush(im, [(220, 170, 110), (30, 40, 45), (10, 20, 24), (80, 120, 110)], 340, 14)
    im = _rain(im, 230, alpha=12)
    _save(_grade(im, brightness=1.35, contrast=1.02, shadow=(8, 18, 22),
                 high=(235, 176, 104), vignette=.65), "bg_loft.jpg")


def paint_alley() -> None:
    # If the official combat illustration exists we use it; this is the fallback.
    im = _canvas((8, 17, 19), (17, 24, 25))
    d = ImageDraw.Draw(im, "RGBA")
    d.polygon([(0, 0), (430, 0), (620, 300), (620, 900), (0, 900)], fill=(18, 24, 24, 255))
    d.polygon([(W, 0), (1170, 0), (980, 300), (980, 900), (W, 900)], fill=(15, 22, 24, 255))
    d.polygon([(520, 80), (1080, 80), (1150, 900), (450, 900)], fill=(9, 29, 34, 255))
    for side, x in ((0, 380), (1, 1215)):
        for y in range(160, 700, 130):
            d.line([(x - 80 if side == 0 else x, y), (x + 90 if side == 0 else x, y)],
                   fill=(8, 12, 13, 255), width=8)
            for i in range(6):
                x2 = x - 70 + i * 30 if side == 0 else x + i * 15
                d.line([(x2, y), (x2, y + 65)], fill=(8, 12, 13, 255), width=4)
    d.line([(400, 0), (400, 260), (620, 260)], fill=(8, 11, 12, 255), width=18)
    d.line([(1200, 0), (1200, 360), (980, 360)], fill=(8, 11, 12, 255), width=12)
    d.rounded_rectangle([660, 620, 1010, 820], radius=8, fill=(23, 34, 32, 255))
    d.rectangle([640, 590, 1030, 640], fill=(33, 44, 39, 255))
    for y in range(700, 900, 35):
        d.rectangle([450, y, 1150, y + 2], fill=(255, 85, 100, 30))
        d.rectangle([450, y + 8, 1050, y + 10], fill=(60, 210, 220, 24))
    im = _brush(im, [(55, 75, 70), (240, 80, 90), (70, 190, 200), (20, 30, 30)], 380, 17)
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.rectangle([1020, 130, 1220, 155], fill=(255, 60, 90, 220))
    gd.rectangle([1040, 200, 1190, 210], fill=(255, 160, 80, 150))
    im = Image.alpha_composite(im.convert("RGBA"),
                               glow.filter(ImageFilter.GaussianBlur(16))).convert("RGB")
    im = _rain(im, 900, alpha=34)
    _save(_grade(im, brightness=.95, contrast=1.05, shadow=(7, 18, 22),
                 high=(238, 110, 90), vignette=.9), "bg_alley.jpg")


def paint_room() -> None:
    im = _canvas((14, 11, 12), (25, 18, 17))
    d = ImageDraw.Draw(im, "RGBA")
    for x in range(0, W, 120):
        d.line([(x, 0), (x, H)], fill=(8, 7, 8, 80), width=2)
    for y in range(0, H, 90):
        d.line([(0, y), (W, y)], fill=(8, 7, 8, 60), width=2)
    d.line([(800, 0), (800, 250)], fill=(5, 4, 4, 255), width=4)
    d.polygon([(740, 250), (860, 250), (900, 320), (700, 320)], fill=(18, 13, 10, 255))
    d.ellipse([770, 300, 830, 335], fill=(255, 210, 140, 255))
    d.rectangle([640, 440, 930, 490], fill=(23, 17, 15, 255))
    d.rectangle([650, 480, 930, 520], fill=(28, 20, 16, 255))
    d.line([(680, 520), (660, 800)], fill=(18, 13, 11, 255), width=16)
    d.line([(900, 520), (920, 800)], fill=(18, 13, 11, 255), width=16)
    d.ellipse([600, 620, 950, 690], fill=(55, 6, 10, 180))
    d.ellipse([720, 600, 860, 650], fill=(74, 8, 12, 160))
    rng = _rng(33)
    for _ in range(60):
        x, y = rng.randrange(560, 980), rng.randrange(600, 760)
        d.ellipse([x, y, x + rng.randint(2, 14), y + rng.randint(2, 8)],
                  fill=(62, 8, 10, rng.randint(30, 120)))
    d.rectangle([120, 180, 420, 760], fill=(9, 8, 8, 255))
    d.rectangle([130, 190, 410, 750], outline=(60, 45, 38, 150), width=2)
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.polygon([(700, 320), (900, 320), (1200, 900), (400, 900)],
               fill=(255, 180, 100, 26))
    for r in range(300, 10, -16):
        gd.ellipse([800 - r, 300 - r * .3, 800 + r, 300 + r * .4],
                   fill=(255, 190, 120, max(1, int((300 - r) / 300 * 18))))
    im = Image.alpha_composite(im.convert("RGBA"),
                               glow.filter(ImageFilter.GaussianBlur(28))).convert("RGB")
    im = _brush(im, [(120, 45, 35), (255, 180, 110), (30, 24, 22), (80, 70, 60)], 300, 19)
    _save(_grade(im, brightness=1.55, contrast=1.04, shadow=(18, 8, 9),
                 high=(240, 168, 100), vignette=.7), "bg_room.jpg")


def paint_warehouse() -> None:
    im = _canvas((6, 12, 20), (15, 19, 22))
    d = ImageDraw.Draw(im, "RGBA")
    for x in range(0, W, 240):
        d.line([(x, 0), (x + 120, 210), (x + 240, 0)], fill=(8, 10, 14, 255), width=8)
    d.line([(0, 210), (W, 210)], fill=(7, 10, 14, 255), width=12)
    d.rectangle([1000, 220, 1600, 650], fill=(14, 42, 58, 255))
    d.rectangle([1000, 220, 1600, 650], outline=(5, 8, 10, 255), width=14)
    for x, y, w, h, c in [(80, 480, 380, 260, (38, 58, 55)),
                          (480, 430, 280, 330, (55, 38, 34)),
                          (800, 520, 260, 240, (42, 48, 58)),
                          (250, 260, 260, 190, (52, 55, 38))]:
        d.rectangle([x, y, x + w, y + h], fill=c + (255,))
        d.rectangle([x + 10, y + 10, x + w - 10, y + h - 10],
                    outline=(8, 10, 12, 180), width=4)
        for gx in range(x + 18, x + w - 10, 36):
            d.line([(gx, y), (gx, y + h)], fill=(8, 10, 12, 90), width=2)
    # police strobes
    for x, c in ((1180, (255, 50, 70)), (1260, (70, 110, 255))):
        glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        gd = ImageDraw.Draw(glow)
        gd.ellipse([x - 170, 520 - 170, x + 170, 520 + 170], fill=c + (80,))
        im = Image.alpha_composite(im.convert("RGBA"),
                                   glow.filter(ImageFilter.GaussianBlur(52))).convert("RGB")
    im = _brush(im, [(30, 45, 45), (180, 70, 50), (40, 50, 70), (90, 100, 80)], 420, 17)
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.polygon([(1020, 240), (1540, 240), (1200, 900), (500, 900)],
               fill=(70, 150, 170, 18))
    im = Image.alpha_composite(im.convert("RGBA"),
                               glow.filter(ImageFilter.GaussianBlur(30))).convert("RGB")
    # light rain drifting in from the open bay
    im = _rain(im, 520, alpha=18)
    _save(_grade(im, brightness=1.6, contrast=1.04, shadow=(7, 18, 25),
                 high=(235, 170, 100), vignette=.68), "bg_warehouse.jpg")


def paint_dawn() -> None:
    im = _canvas((52, 31, 36), (226, 126, 66))
    d = ImageDraw.Draw(im, "RGBA")
    for y in range(H // 2, H):
        t = (y - H // 2) / (H / 2)
        d.line([(0, y), (W, y)],
               fill=(int(30 + 20 * t), int(38 + 18 * t), int(48 + 10 * t), 255))
    d.ellipse([1080, 430, 1260, 610], fill=(255, 190, 110, 255))
    d.rectangle([0, 600, W, H], fill=(11, 17, 24, 255))
    rng = _rng(12)
    for x in range(0, W, 45):
        h = rng.randint(30, 240)
        d.rectangle([x, 600 - h, x + 42, 610], fill=(17, 18, 24, 255))
        if rng.random() < .3:
            d.rectangle([x + 8, 600 - h - 25, x + 12, 600 - h], fill=(20, 20, 25, 255))
    for x in (250, 520, 900, 1300):
        d.line([(x, 610), (x, 310)], fill=(10, 12, 16, 255), width=7)
        d.line([(x - 120, 330), (x + 180, 330)], fill=(10, 12, 16, 255), width=6)
        d.line([(x, 350), (x - 100, 330)], fill=(10, 12, 16, 255), width=3)
        d.line([(x, 350), (x + 160, 330)], fill=(10, 12, 16, 255), width=3)
    for _ in range(160):
        x, y = rng.randrange(W), rng.randrange(610, H)
        c = rng.choice([(255, 150, 80), (70, 110, 150), (255, 90, 80)])
        d.line([(x, y), (x + rng.randint(10, 80), y)], fill=c + (rng.randint(6, 30),), width=2)
    im = _brush(im, [(240, 150, 90), (30, 35, 45), (120, 70, 70), (40, 90, 110)], 340, 18)
    _save(_grade(im, brightness=1.02, contrast=1.05, shadow=(18, 12, 18),
                 high=(245, 170, 105), vignette=.85), "bg_dawn.jpg")


# ---------------------------------------------------------------------------
# official art extraction
# ---------------------------------------------------------------------------
def _render_page(doc, page_no: int, dpi: int = 300) -> Image.Image:
    page = doc[page_no - 1]
    import pymupdf
    pix = page.get_pixmap(matrix=pymupdf.Matrix(dpi / 72, dpi / 72), alpha=False)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def _crop_fraction(im: Image.Image, box: tuple[float, float, float, float]) -> Image.Image:
    x0, x1, y0, y1 = box
    w, h = im.size
    return im.crop((int(w * x0), int(h * y0), int(w * x1), int(h * y1)))


def _paper(im: Image.Image, size=(620, 900), *, seed: int = 4) -> Image.Image:
    """Put a white-background render on aged, ink-washed paper via multiply."""
    paper = Image.new("RGB", size, (196, 184, 163))
    # fiber + stains
    rng = _rng(seed)
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for _ in range(260):
        x, y = rng.randrange(-20, size[0] + 20), rng.randrange(-20, size[1] + 20)
        d.line([(x, y), (x + rng.randint(-3, 3), y + rng.randint(8, 70))],
               fill=(80, 64, 42, rng.randint(3, 12)), width=1)
    for _ in range(16):
        x, y = rng.randrange(size[0]), rng.randrange(size[1])
        r = rng.randint(20, 100)
        d.ellipse([x - r, y - r, x + r, y + r], fill=(92, 62, 34, rng.randint(2, 8)))
    paper = Image.alpha_composite(paper.convert("RGBA"), layer).convert("RGB")
    paper = _grain(paper, 5, seed + 1)

    subj = ImageOps.fit(im.convert("RGB"), size, Image.Resampling.LANCZOS,
                        centering=(.5, .32))
    subj = ImageEnhance.Color(subj).enhance(.55)
    subj = ImageEnhance.Contrast(subj).enhance(1.08)
    # white becomes paper, ink stays ink; the silver/red palette survives
    a = np.asarray(subj).astype(np.float32) / 255.0
    p = np.asarray(paper).astype(np.float32) / 255.0
    out = a * p
    # split-tone: shadows cold, highlights warm
    lum = out.mean(axis=2, keepdims=True)
    out = out * (0.86 + 0.18 * lum) + np.array([.02, .015, .00], dtype=np.float32) * (1 - lum)
    out = np.clip(out * 255, 0, 255).astype(np.uint8)
    # halftone screen
    out_img = Image.fromarray(out)
    dots = Image.new("RGBA", size, (0, 0, 0, 0))
    dd = ImageDraw.Draw(dots)
    for y in range(0, size[1], 4):
        for x in range(0, size[0], 4):
            if (x + y) % 8 == 0:
                dd.point((x, y), fill=(20, 18, 15, 28))
    out_img = Image.alpha_composite(out_img.convert("RGBA"), dots).convert("RGB")
    # scratches and a stamped edge
    scr = Image.new("RGBA", size, (0, 0, 0, 0))
    sd = ImageDraw.Draw(scr)
    for _ in range(28):
        x, y = rng.randrange(size[0]), rng.randrange(size[1])
        sd.line([(x, y), (x + rng.randint(-40, 40), y + rng.randint(-12, 12))],
                fill=(225, 215, 190, rng.randint(18, 70)), width=1)
    out_img = Image.alpha_composite(out_img.convert("RGBA"), scr).convert("RGB")
    return _vignette(out_img, .65)


def build_from_pdf(pdf: Path) -> None:
    import pymupdf
    doc = pymupdf.open(str(pdf))
    print("[official] extracting scene plates")
    for page, (name, box) in SCENE_PAGES.items():
        im = _render_page(doc, page)
        im = _crop_fraction(im, box)
        if name == "concert":
            # only serve it as the end-credit plate
            im = _fit(im)
            im = _rain(im, 700, alpha=28)
            _save(_grade(im, brightness=.7, contrast=1.05, shadow=(12, 18, 26),
                         high=(255, 190, 105), vignette=.94), "bg_concert.jpg")
            continue
        if name == "title":
            im = _fit(im)
            # keep the city but push the foreground rider into shadow
            _save(_grade(im, brightness=.78, contrast=1.06, shadow=(10, 16, 25),
                         high=(255, 178, 110), vignette=.9), "bg_title.jpg")
            _save(_grade(im, brightness=.9, contrast=1.03, shadow=(17, 12, 17),
                         high=(255, 160, 90), vignette=.88), "bg_dawn.jpg")
            continue
        im = _fit(im)
        if name == "street":
            _save(_grade(im, brightness=.75, contrast=1.08, shadow=(7, 18, 28),
                         high=(255, 176, 105), vignette=.9), "bg_street.jpg")
        elif name == "ambush":
            im = _rain(im, 900, alpha=34)
            _save(_grade(im, brightness=.72, contrast=1.13, shadow=(7, 16, 20),
                         high=(255, 130, 95), vignette=.95), "bg_alley.jpg")
            _save(_grade(im, brightness=.62, contrast=1.12, shadow=(6, 14, 23),
                         high=(255, 145, 90), vignette=.96), "bg_ambush.jpg")
    print("[official] cutting pregen portraits")
    for page, (name, body_box, face_box) in PREGEN_PAGES.items():
        im = _render_page(doc, page)
        im = _crop_fraction(im, body_box)
        im = im.transpose(Image.Transpose.ROTATE_270)
        w, h = im.size
        # face/upper-body crop is expressed relative to the rotated plate
        fx0, fx1, fy0, fy1 = face_box
        crop = im.crop((int(w * fx0), int(h * fy0), int(w * fx1), int(h * fy1)))
        _save(_paper(crop, seed=sum(ord(c) for c in name) % 97), f"portrait_{name}.jpg")
    doc.close()


# ---------------------------------------------------------------------------
def main() -> None:
    global OUT
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", default=str(PDF))
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()
    OUT = Path(args.out)
    OUT.mkdir(parents=True, exist_ok=True)
    print(f"writing to {OUT}")
    paint_loft()
    paint_alley()
    paint_room()
    paint_warehouse()
    paint_dawn()
    pdf = Path(args.pdf)
    if pdf.exists():
        build_from_pdf(pdf)
    else:
        print(f"[warn] PDF not found: {pdf}; procedural plates only")
    print("done")


if __name__ == "__main__":
    main()
