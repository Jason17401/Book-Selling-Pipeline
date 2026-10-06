"""The front photo of a set is a GRID of books (GRID=rows x cols, e.g. 2x5). Book N is the N-th cell counting along the
top row left to right, then the next row. This module does the geometry, draws numbers on the photo, and makes the
review sheet. Nothing here is stored in books.csv: crops are made on the fly from the front photo when needed."""
from __future__ import annotations

import math
from pathlib import Path
from typing import Optional, Tuple

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .decode import load_image, rotate_cw

RED = (200, 0, 0)
FULL = (0.0, 0.0, 1.0, 1.0)
CJK_FONTS = ("msjh.ttc", "msjhbd.ttc", "mingliu.ttc", "msyh.ttc", "simsun.ttc",          # Windows
             "PingFang.ttc", "Hiragino Sans GB.ttc", "STHeiti Medium.ttc",              # macOS
             "NotoSansCJK-Regular.ttc", "NotoSansCJKtc-Regular.otf", "wqy-microhei.ttc", "DroidSansFallbackFull.ttf",
             "C:/Windows/Fonts/msjh.ttc", "/System/Library/Fonts/PingFang.ttc",
             "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")


def font(size: int, cjk: bool = False):
    """cjk=True: a font that has Chinese characters (titles of Taiwanese books), falling back to the normal one."""
    for name in (CJK_FONTS if cjk else ()) + ("DejaVuSans-Bold.ttf", "arialbd.ttf", "Arial Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # old Pillow
        return ImageFont.load_default()


def parse_grid(text: str) -> Tuple[int, int]:
    r, c = text.lower().split("x")
    return int(r), int(c)


def load_upright(path, rotate: int) -> Image.Image:
    """EXIF orientation first, then the ROTATE setting for phones/apps that lose the orientation tag."""
    return rotate_cw(load_image(path), rotate)


# ---- geometry ------------------------------------------------------------------------------------
def position_to_cell(pos: int, rows: int, cols: int) -> Tuple[int, int]:
    return divmod(pos - 1, cols)


def cell_box(r: int, c: int, rows: int, cols: int, size, pad: float = 0.0, region=FULL):
    W, H = size
    x0, y0, x1, y1 = region[0] * W, region[1] * H, region[2] * W, region[3] * H
    cw, ch = (x1 - x0) / cols, (y1 - y0) / rows
    return (max(0, int(x0 + (c - pad) * cw)), max(0, int(y0 + (r - pad) * ch)),
            min(W, int(x0 + (c + 1 + pad) * cw)), min(H, int(y0 + (r + 1 + pad) * ch)))


def crop_book(front: Image.Image, pos: int, grid, region=FULL) -> Optional[Image.Image]:
    """Book number `pos` cut out of the front photo, or None if pos is outside the grid."""
    rows, cols = grid
    if not 1 <= pos <= rows * cols:
        return None
    r, c = position_to_cell(pos, rows, cols)
    return front.crop(cell_box(r, c, rows, cols, front.size, region=region))


def cell_aspect(size, rows: int, cols: int, region=FULL) -> float:
    W, H = size
    return ((region[2] - region[0]) * W / cols) / ((region[3] - region[1]) * H / rows)


def suggest_grid(size, n: int, region=FULL) -> Tuple[int, int]:
    """The rows x cols (rows*cols == n) whose cells look most like a book (0.7 wide:tall upright, 1.4 on its side)."""
    best = None
    for r in range(1, n + 1):
        if n % r:
            continue
        a = cell_aspect(size, r, n // r, region)
        err = min(abs(math.log(a / 0.7)), abs(math.log(a / 1.43)))
        if best is None or err < best[0]:
            best = (err, r, n // r)
    return best[1], best[2]


def orientation_warning(img: Image.Image, rows: int, cols: int, label: str, region=FULL) -> Optional[str]:
    a = cell_aspect(img.size, rows, cols, region)
    if 0.4 <= a <= 2.5:
        return None
    r, c = suggest_grid(img.size, rows * cols, region)
    return (f"{label} photo does not fit a {rows}x{cols} grid (rows x columns): each cell would be {a:.2f} times as wide "
            "as tall, but books are about 0.7 or 1.4. Either the photo is sideways - run: python -m pipeline orient "
            f"<photo> and set ROTATE in .env - or your books are laid out the other way round: try GRID={r}x{c}")


# ---- drawing -------------------------------------------------------------------------------------
def _badge(draw: ImageDraw.ImageDraw, x: int, y: int, n: int, r: int) -> None:
    size = int(r * (1.3 if n < 10 else 0.95))  # two digits need a smaller font to fit the circle
    draw.ellipse([x, y, x + 2 * r, y + 2 * r], fill=(255, 255, 255), outline=RED, width=max(2, r // 8))
    try:
        draw.text((x + r, y + r), str(n), font=font(size), fill=RED, anchor="mm")
    except Exception:
        draw.text((x + r // 2, y + r // 2), str(n), font=font(size), fill=RED)


def numbered(img: Image.Image, rows: int, cols: int, region=FULL) -> Image.Image:
    """A copy of the front photo with a number badge in the corner of every book's cell."""
    out = img.copy()
    d = ImageDraw.Draw(out)
    box = cell_box(0, 0, rows, cols, out.size, region=region)
    r = max(14, int(min(box[2] - box[0], box[3] - box[1]) * 0.11))
    for n in range(1, rows * cols + 1):
        x0, y0, x1, y1 = cell_box(*position_to_cell(n, rows, cols), rows, cols, out.size, region=region)
        _badge(d, int(x0 + (x1 - x0) * 0.04), int(y0 + (y1 - y0) * 0.04), n, r)
    return out


def stamp_number(src, dst, n: int) -> None:
    img = ImageOps.exif_transpose(Image.open(src)).convert("RGB")
    r = max(20, int(min(img.size) * 0.07))
    _badge(ImageDraw.Draw(img), int(img.width * 0.02), int(img.height * 0.02), n, r)
    img.save(dst, quality=92)


def make_orient_sheet(img: Image.Image, dst, thumb: int = 420) -> None:
    """Four copies of the photo turned 0/90/180/270 degrees clockwise. Pick the upright one."""
    f = font(34)
    cell_w, cell_h = thumb + 20, thumb + 60
    sheet = Image.new("RGB", (cell_w * 2, cell_h * 2), "white")
    d = ImageDraw.Draw(sheet)
    for i, deg in enumerate((0, 90, 180, 270)):
        im = rotate_cw(img, deg).copy()
        im.thumbnail((thumb, thumb))
        x, y = (i % 2) * cell_w + 10, (i // 2) * cell_h + 50
        d.text((x, y - 44), f"ROTATE={deg}", font=f, fill=RED)
        sheet.paste(im, (x, y))
    sheet.save(dst, quality=90)


def make_review_sheet(front: Optional[Image.Image], rows: list, grid, region, dst: Path, thumb_h: int = 160) -> None:
    """One picture to eyeball a set: the numbered front photo on top, then per book:
    that book cut from the front photo | its barcode photo | number, ISBN, title, author.
    If the two pictures on a row are not the same book, the photos were sent in the wrong order."""
    W = 1000
    top = None
    if front is not None:
        top = numbered(front, *grid, region=region)
        top.thumbnail((W - 20, 700))
    row_h = thumb_h + 12
    H = (top.height + 20 if top else 0) + row_h * len(rows) + 10
    sheet = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(sheet)
    y = 10
    if top:
        sheet.paste(top, ((W - top.width) // 2, y))
        y += top.height + 10
    big, small = font(24), font(17, cjk=True)
    for r in rows:
        x = 10
        pos = int(r["position"])
        pics = [crop_book(front, pos, grid, region) if front is not None else None]
        try:
            pics.append(load_image(r["barcode_photo"]))
        except Exception:
            pics.append(None)
        for im in pics:
            if im is not None:
                im = im.copy()
                im.thumbnail((int(thumb_h * 1.2), thumb_h))
                sheet.paste(im, (x, y))
            x += int(thumb_h * 1.2) + 10
        d.text((x + 10, y + 8), f"#{pos}", font=big, fill=RED)
        d.text((x + 85, y + 8), r.get("isbn13") or "UNREADABLE BARCODE", font=big, fill="black")
        d.text((x + 10, y + 52), (r.get("title") or "")[:40], font=small, fill=(60, 60, 60))
        d.text((x + 10, y + 80), (r.get("author") or "")[:40], font=small, fill=(110, 110, 110))
        d.line([(0, y + row_h - 6), (W, y + row_h - 6)], fill=(220, 220, 220))
        y += row_h
    sheet.save(dst, quality=90)
