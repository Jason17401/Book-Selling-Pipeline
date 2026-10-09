"""Synthetic test photos: EAN-13 barcodes drawn from scratch (no extra packages), placed on fake book backs."""
from __future__ import annotations

import io
import random

from PIL import Image, ImageDraw, ImageFilter

_L = ["0001101", "0011001", "0010011", "0111101", "0100011", "0110001", "0101111", "0111011", "0110111", "0001011"]
_G = ["0100111", "0110011", "0011011", "0100001", "0011101", "0111001", "0000101", "0010001", "0001001", "0010111"]
_R = ["1110010", "1100110", "1101100", "1000010", "1011100", "1001110", "1010000", "1000100", "1001000", "1110100"]
_PARITY = ["LLLLLL", "LLGLGG", "LLGGLG", "LLGGGL", "LGLLGG", "LGGLLG", "LGGGLL", "LGLGLG", "LGLGGL", "LGGLGL"]


def with_check(d12: str) -> str:
    t = sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(d12))
    return d12 + str((10 - t % 10) % 10)


def ean13_bits(code: str) -> str:
    assert len(code) == 13 and code.isdigit()
    first, left, right = int(code[0]), code[1:7], code[7:]
    bits = "101"
    for p, c in zip(_PARITY[first], left):
        bits += (_L if p == "L" else _G)[int(c)]
    bits += "01010"
    for c in right:
        bits += _R[int(c)]
    return bits + "101"


_ADDON5_PARITY = ["GGLLL", "GLGLL", "GLLGL", "GLLLG", "LGGLL", "LLGGL", "LLLGG", "LGLGL", "LGLLG", "LLGLG"]


def ean5_bits(addon: str) -> str:
    """The 5-digit add-on (price barcode) printed to the right of an ISBN barcode."""
    d = [int(c) for c in addon]
    check = (3 * (d[0] + d[2] + d[4]) + 9 * (d[1] + d[3])) % 10
    bits = "1011"
    for i, (p, c) in enumerate(zip(_ADDON5_PARITY[check], d)):
        bits += ("01" if i else "") + (_L if p == "L" else _G)[c]
    return bits


def barcode_image(code: str, module: int = 4, height: int = 120, quiet: int = 11, addon: str = "") -> Image.Image:
    bits = ean13_bits(code)
    gap = 9
    extra = (gap + len(ean5_bits(addon))) if addon else 0
    w = (len(bits) + extra + 2 * quiet) * module
    img = Image.new("L", (w, height + 2 * module * 3), 255)
    d = ImageDraw.Draw(img)
    for i, b in enumerate(bits):
        if b == "1":
            x = (quiet + i) * module
            d.rectangle([x, module * 3, x + module - 1, module * 3 + height], fill=0)
    if addon:
        start = quiet + len(bits) + gap
        for i, b in enumerate(ean5_bits(addon)):
            if b == "1":
                x = (start + i) * module
                d.rectangle([x, module * 3 + height // 6, x + module - 1, module * 3 + height], fill=0)
    return img.convert("RGB")


def book_back(isbn: str, size=(1200, 1700), module: float = 3.0, seed: int = 0, price_code: str = "4710000123459",
              angle: float = 0.0, blur: float = 0.0, jpeg: int = 0, glare: bool = False, noise: int = 0,
              addon: str = "") -> Image.Image:
    """A fake back cover: coloured background, 'text' lines, the ISBN barcode and a Taiwan-style price barcode."""
    rnd = random.Random(seed)
    W, H = size
    img = Image.new("RGB", size, tuple(rnd.randint(150, 250) for _ in range(3)))
    d = ImageDraw.Draw(img)
    for y in range(80, int(H * 0.6), 40):  # fake paragraphs
        d.rectangle([80, y, rnd.randint(W // 2, W - 80), y + 14], fill=tuple(rnd.randint(20, 90) for _ in range(3)))
    mod = max(1, round(module))
    bc = barcode_image(isbn, module=mod, height=int(mod * 50), addon=addon)
    if module != mod:
        bc = bc.resize((int(bc.width * module / mod), int(bc.height * module / mod)), Image.BILINEAR)
    pc = barcode_image(price_code, module=mod, height=int(mod * 50)) if price_code else None
    x, y = int(W * 0.08), int(H * 0.72)
    img.paste(bc, (x, y))
    if pc is not None:
        pc = pc.resize((bc.width, bc.height))
        if x + bc.width + 20 + pc.width < W:
            img.paste(pc, (x + bc.width + 20, y))
        else:
            img.paste(pc, (x, y - bc.height - 30))
    if glare:
        g = Image.new("L", size, 0)
        ImageDraw.Draw(g).ellipse([x + bc.width * 0.3, y - 40, x + bc.width * 0.6, y + bc.height + 40], fill=110)
        img = Image.composite(Image.new("RGB", size, (255, 255, 255)), img, g.filter(ImageFilter.GaussianBlur(25)))
    if noise:
        px = img.load()
        for _ in range(W * H // 20):
            i, j = rnd.randrange(W), rnd.randrange(H)
            v = px[i, j]
            px[i, j] = tuple(max(0, min(255, c + rnd.randint(-noise, noise))) for c in v)
    if angle:
        img = img.rotate(angle, expand=True, fillcolor=(200, 200, 200), resample=Image.BICUBIC)
    if blur:
        img = img.filter(ImageFilter.GaussianBlur(blur))
    if jpeg:
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=jpeg)
        buf.seek(0)
        img = Image.open(buf).convert("RGB")
    return img


def book_front(n: int, size=(600, 850), seed: int = 0) -> Image.Image:
    rnd = random.Random(seed + 1000)
    img = Image.new("RGB", size, tuple(rnd.randint(40, 220) for _ in range(3)))
    d = ImageDraw.Draw(img)
    d.rectangle([40, 60, size[0] - 40, 200], fill=(250, 250, 250))
    d.text((60, 100), f"BOOK {n}", fill=(0, 0, 0))
    return img


def set_front(count: int = 10, grid=(2, 5), cell=(300, 420)) -> Image.Image:
    rows, cols = grid
    img = Image.new("RGB", (cols * cell[0], rows * cell[1]), (90, 70, 50))
    for i in range(count):
        r, c = divmod(i, cols)
        img.paste(book_front(i + 1, (cell[0] - 20, cell[1] - 20), seed=i), (c * cell[0] + 10, r * cell[1] + 10))
    return img


TW_ISBNS = [with_check(p) for p in (
    "978957137463", "978986137195", "978626310112", "978957081234", "978986479919",
    "978957320000", "978986510012", "978626702345", "978957133333", "978986555555",
    "978957444444", "978986666666", "978626777777", "978957888888", "978986999999",
    "978957000111", "978986222333", "978626444555", "978957666777", "978986888999",
)]


def cover(size, seed: int, light: bool = False) -> Image.Image:
    """A fake book cover: colour gradient, a picture block, title and author 'text' lines."""
    rnd = random.Random(seed)
    w, h = size
    base = (rnd.randint(230, 250),) * 3 if light else tuple(rnd.randint(30, 220) for _ in range(3))
    img = Image.new("RGB", size, base)
    d = ImageDraw.Draw(img)
    for y in range(h):                              # gentle vertical gradient
        t = y / max(1, h)
        d.line([(0, y), (w, y)], fill=tuple(int(c * (1 - 0.25 * t)) for c in base))
    d.rectangle([int(w * 0.1), int(h * 0.35), int(w * 0.9), int(h * 0.75)],
                fill=tuple(rnd.randint(0, 255) for _ in range(3)))
    for i in range(3):                              # title / author lines
        y = int(h * (0.08 + i * 0.07))
        d.rectangle([int(w * 0.12), y, int(w * rnd.uniform(0.5, 0.88)), y + max(3, h // 40)],
                    fill=(20, 20, 20) if light or sum(base) > 380 else (245, 245, 245))
    d.rectangle([0, 0, w - 1, h - 1], outline=tuple(max(0, c - 40) for c in base), width=2)
    return img


def table_set(rows=2, cols=5, book=(330, 470), seed: int = 0, touching: bool = False, tilt: float = 0.0,
              margin=(160, 140), light_cover: int = -1, shadow: int = 0, carpet: bool = False):
    """A realistic front photo: books of slightly different sizes lying on a textured table, not exactly on a grid,
    some a little turned. shadow=N: each book casts a soft shadow N px to its right (light from the left), like a
    photo taken by a window; carpet=True: a grey textured carpet instead of a wooden table.
    Returns (image, true boxes of each book in book order)."""
    rnd = random.Random(seed)
    gap = 0 if touching else 40
    W = margin[0] * 2 + cols * book[0] + (cols - 1) * gap
    H = margin[1] * 2 + rows * book[1] + (rows - 1) * (gap + 20)
    if carpet:
        import numpy as _np
        g = _np.random.default_rng(seed)
        base = _np.clip(125 + g.normal(0, 22, (H // 2 + 1, W // 2 + 1)), 0, 255)
        base = _np.kron(base, _np.ones((2, 2)))[:H, :W]
        light = _np.linspace(1.12, 0.9, H)[:, None]                 # brighter at the top, like daylight
        rgb = _np.stack([base * 0.97, base, base * 1.0], axis=2) * light[..., None]
        img = Image.fromarray(_np.clip(rgb, 0, 255).astype("uint8"))
    else:
        img = Image.new("RGB", (W, H), (150, 110, 75))
        d = ImageDraw.Draw(img)
        for y in range(0, H, 3):                        # wood grain
            v = rnd.randint(-12, 12)
            d.line([(0, y), (W, y + rnd.randint(-2, 2))], fill=(150 + v, 110 + v, 75 + v), width=3)
    places = []
    for i in range(rows * cols):
        r, c = divmod(i, cols)
        bw = int(book[0] * rnd.uniform(0.9, 1.0))
        bh = int(book[1] * rnd.uniform(0.9, 1.0))
        x = margin[0] + c * (book[0] + gap) + (0 if touching else rnd.randint(-10, 10)) + (book[0] - bw) // 2
        y = margin[1] + r * (book[1] + gap + 20) + (0 if touching else rnd.randint(-10, 10)) + (book[1] - bh) // 2
        if touching:
            x = margin[0] + c * book[0]
            bw = book[0]
        places.append((x, y, bw, bh))
    if shadow:
        from PIL import ImageFilter
        sm = Image.new("L", img.size, 0)
        sd = ImageDraw.Draw(sm)
        for x, y, bw, bh in places:
            sd.rectangle((x + shadow, y + shadow // 3, x + bw + shadow, y + bh + shadow // 3), fill=170)
        sm = sm.filter(ImageFilter.GaussianBlur(shadow / 3))
        dark = img.point(lambda v: int(v * 0.45))
        img = Image.composite(dark, img, sm)
    boxes = []
    for i, (x, y, bw, bh) in enumerate(places):
        cv_ = cover((bw, bh), seed * 100 + i, light=(i == light_cover))
        angle = tilt * (1 if i % 2 else -1) if tilt else 0.0
        if angle:
            rgba = cv_.convert("RGBA").rotate(angle, expand=True, resample=Image.BICUBIC)
            px, py = x + bw // 2 - rgba.width // 2, y + bh // 2 - rgba.height // 2
            img.paste(rgba, (px, py), rgba)
            boxes.append((px, py, px + rgba.width, py + rgba.height))
        else:
            img.paste(cv_, (x, y))
            boxes.append((x, y, x + bw, y + bh))
    return img, boxes
