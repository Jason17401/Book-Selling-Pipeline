"""Cut each book out of the set's FRONT photo, by looking at the picture instead of dividing it into equal cells.

How (OpenCV, no AI model needed):
  1. The photo is analysed at ~1000 px. The table is what the photo's outer border looks like; everything that differs
     clearly from it in colour, or has texture/edges (print, titles, cover art), is "book".
  2. GRID says how many rows and columns of books to expect. Between two rows / two neighbouring books we look, near
     where the grid says the split should be, for the best dividing line: a strip of table (a gap), or - when books
     touch - the straight edge where one cover ends and the next begins.
  3. In each cell, the book is the area of "book" pixels; its outline gives the box (and a small tilt, which is
     straightened).
  4. The box is cut from the FULL-resolution photo with a margin around the book (SEGMENT_MARGIN, e.g. 0.04 = 4% of the
     book's size on every side).
Book N is still the N-th book counting along the top row left to right, then the next row - the order you send the
barcode photos in. If a cell shows nothing book-like, the plain grid cell is used for it (found=False).
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from PIL import Image, ImageDraw

try:
    import cv2
    import numpy as np
except ImportError:  # pragma: no cover
    cv2 = None
    np = None

FULL = (0.0, 0.0, 1.0, 1.0)


@dataclass
class Segment:
    box: Tuple[int, int, int, int]    # the book itself (left, top, right, bottom), full-resolution pixels
    angle: float = 0.0                # degrees to turn the cut-out so the book stands straight
    found: bool = True                # False = nothing book-like seen: the plain grid cell is used


def available() -> bool:
    return cv2 is not None


# ---- analysis ------------------------------------------------------------------------------------
def _fit_background(lab: "np.ndarray", table: "np.ndarray") -> "np.ndarray":
    """A smooth model of the table's colour over the whole photo (a 2-D quadratic per channel, fitted to pixels that are
    table), so uneven light - a shadow, a brighter side - is not mistaken for a book."""
    h, w = table.shape
    ys, xs = np.nonzero(table)
    if len(xs) < 50:
        return np.broadcast_to(np.median(lab.reshape(-1, 3), axis=0), lab.shape).astype(np.float32)
    step = max(1, len(xs) // 6000)
    xs, ys = xs[::step], ys[::step]
    u, v = xs / w - 0.5, ys / h - 0.5
    A = np.stack([np.ones_like(u), u, v, u * u, v * v, u * v], axis=1)
    U, V = np.meshgrid(np.arange(w) / w - 0.5, np.arange(h) / h - 0.5)
    G = np.stack([np.ones_like(U), U, V, U * U, V * V, U * V], axis=-1)
    out = np.empty_like(lab)
    for ch in range(3):
        coef, *_ = np.linalg.lstsq(A, lab[ys, xs, ch], rcond=None)
        out[..., ch] = G @ coef
    return out


def _local_std(gray: "np.ndarray", k: int) -> "np.ndarray":
    g = gray.astype(np.float32)
    m = cv2.blur(g, (k, k))
    return np.sqrt(np.maximum(0.0, cv2.blur(g * g, (k, k)) - m * m))


def _shadow(lab: "np.ndarray", model: "np.ndarray", texture: "np.ndarray" = None,
            table: "np.ndarray" = None) -> "np.ndarray":
    """1 where the picture is the table in a book's shadow: darker than the table would be there, but the same colour
    (a shadow dims the table without changing its hue much), and - on a table with a texture (carpet, wood grain) -
    with that texture still showing through, only dimmer. Not counted as book, so the shadow a book casts to one side
    doesn't make its box wider on that side. A plain dark-grey or black cover has no carpet texture in it, and very
    dark pixels (< 30% of the table's lightness) are never called shadow."""
    dl = model[..., 0] - lab[..., 0]
    ab, mab = lab[..., 1:] - 128.0, model[..., 1:] - 128.0
    k = np.clip(lab[..., 0] / np.maximum(1.0, model[..., 0]), 0.0, 1.0)
    # a coloured table (wood) loses colour in the shadow along with light: compare with the table's colour scaled down
    dab = np.minimum(np.linalg.norm(ab - mab, axis=2), np.linalg.norm(ab - k[..., None] * mab, axis=2))
    out = (dl > 0) & (dab <= np.maximum(8.0, 0.2 * dl)) & (k >= 0.3)
    if texture is not None and table is not None and table.any():
        grain = float(np.median(texture[table]))
        if grain >= 2.0:                     # the table has a visible texture: it must show through the shadow
            out &= texture >= 0.35 * k * grain
    return out


def _book_mask(rgb: "np.ndarray") -> "np.ndarray":
    """1 where the picture looks like a book (differs from the table, or has print/edges), 0 for table."""
    h, w = rgb.shape[:2]
    lab = cv2.cvtColor(cv2.GaussianBlur(rgb, (5, 5), 0), cv2.COLOR_RGB2LAB).astype(np.float32)
    b = max(2, int(min(h, w) * 0.03))
    edge_px = np.zeros((h, w), bool)
    edge_px[:b], edge_px[-b:], edge_px[:, :b], edge_px[:, -b:] = True, True, True, True
    border = lab[edge_px]
    bg = np.median(border, axis=0)
    bd = np.linalg.norm(border - bg, axis=1)
    thr = max(12.0, 2.5 * float(np.percentile(bd, 75)))
    # first guess of the table: near the border colour; then a smooth fit over it, and measure again
    dist = np.linalg.norm(lab - bg, axis=2)
    for _ in range(2):
        model = _fit_background(lab, dist <= thr)
        dist = np.linalg.norm(lab - model, axis=2)
        table_d = dist[dist <= np.percentile(dist, 30)]
        thr = max(12.0, 3.0 * float(np.percentile(table_d, 90)) if len(table_d) else 12.0)
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    tex = _local_std(gray, max(5, int(min(h, w) * 0.008)) | 1)
    table = dist <= thr
    colour = (dist > thr) & ~_shadow(lab, model, tex, table)
    grain = float(np.median(tex[table])) if table.any() else 0.0
    if grain >= 6.0:
        # a strongly textured table (carpet): a smooth area is a cover, even one the same colour as the carpet
        colour |= cv2.blur((tex < 0.25 * grain).astype(np.float32), (5, 5)) > 0.8

    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    med = float(np.median(gray))
    edges = cv2.Canny(gray, int(max(10, 0.66 * med)), int(min(255, 1.33 * med + 20)))
    k = max(3, int(min(h, w) * 0.012)) | 1
    density = cv2.blur((edges > 0).astype(np.float32), (k, k))
    texture = density > max(0.12, float(np.percentile(density[~colour], 90)) * 1.5 if (~colour).any() else 0.12)

    mask = (colour | texture).astype(np.uint8)
    kc = max(3, int(min(h, w) * 0.015)) | 1
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (kc, kc)))
    ko = max(3, int(min(h, w) * 0.006)) | 1
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (ko, ko)))
    return mask


def _smooth(v: "np.ndarray", k: int) -> "np.ndarray":
    k = max(1, k)
    return np.convolve(v, np.ones(k) / k, mode="same")


def _best_split(fill: "np.ndarray", edge: "np.ndarray", expected: float, lo: int, hi: int) -> int:
    """Where to cut between two books along one axis: a gap of table (low `fill`), or where books touch, a strong
    straight edge (`edge`), preferring the spot the grid expects."""
    lo, hi = max(1, lo), min(len(fill) - 2, hi)
    if hi <= lo:
        return int(round(expected))
    f = fill[lo:hi + 1]
    e = edge[lo:hi + 1]
    fn = (f - f.min()) / (np.ptp(f) or 1.0)
    en = (e - e.min()) / (np.ptp(e) or 1.0)
    gap_score = 1.0 - fn
    has_gap = f.min() < 0.35 * max(1e-6, np.median(fill))          # a real strip of table exists here
    score = gap_score * (1.5 if has_gap else 0.6) + en * (0.3 if has_gap else 0.9)
    pos = np.arange(lo, hi + 1)
    score -= (0.35 if has_gap else 0.7) * np.abs(pos - expected) / max(1.0, (hi - lo) / 2)
    best = score.max()
    # the middle of the best plateau (a wide gap: cut through its centre)
    idx = np.where(score >= best - 0.02)[0]
    groups = np.split(idx, np.where(np.diff(idx) > 1)[0] + 1)
    g = max(groups, key=lambda grp: (score[grp].max(), len(grp)))
    return int(lo + g[len(g) // 2])


def _splits(mask, lines, start: int, end: int, n: int, axis: int) -> List[int]:
    """n parts between start and end along `axis` (0 = rows/y, 1 = columns/x): the cut positions (incl. both ends).
    `lines` marks edge pixels running ACROSS the cut direction; a long straight one (a book's edge) scores high."""
    if n <= 1:
        return [start, end]
    fill = mask.mean(axis=1) if axis == 0 else mask.mean(axis=0)
    edge = lines.mean(axis=1) if axis == 0 else lines.mean(axis=0)
    k = max(1, int(len(fill) * 0.006))
    fill, edge = _smooth(fill, k), _smooth(edge, max(1, k // 2))
    size = (end - start) / n
    cuts = [start]
    for i in range(1, n):
        expected = start + i * size
        lo, hi = int(expected - 0.35 * size), int(expected + 0.35 * size)
        lo = max(lo, cuts[-1] + int(0.4 * size))
        cuts.append(_best_split(fill, edge, expected, lo, hi))
    return cuts + [end]


def _extent(fill: "np.ndarray", start: int, end: int):
    """First and last position (start-based) where there is clearly book, with a little room."""
    if not len(fill) or fill.max() <= 0:
        return start, end
    on = np.where(_smooth(fill, max(1, len(fill) // 100)) > 0.2 * fill.max())[0]
    if not len(on):
        return start, end
    pad = max(2, int(len(fill) * 0.01))
    return max(start, start + int(on[0]) - pad), min(end, start + int(on[-1]) + 1 + pad)


def _trim(mask, box, most: float = 0.15):
    """Cut off thin bits along the sides of the box - a strip of rows/columns only sparsely "book" (bits of a shadow's
    soft edge, carpet fluff, the corner of a neighbour) - so they don't make the box wider on one side. At most `most`
    of the size per side; _snap then puts each side on the cover's real edge."""
    x0, y0, x1, y1 = (int(v) for v in box)
    m = mask[y0:y1, x0:x1].astype(np.float32)
    if m.shape[0] < 10 or m.shape[1] < 10:
        return box

    def cut(fill):
        n = len(fill)
        lo, hi, k = 0, n, int(most * n)
        lim = 0.5 * float(np.median(fill))
        while lo < k and fill[lo] < lim:
            lo += 1
        while n - hi < k and fill[hi - 1] < lim:
            hi -= 1
        return lo, hi
    cl, ch = cut(_smooth(m.mean(axis=0), 3))
    rl, rh = cut(_smooth(m.mean(axis=1), 3))
    return (x0 + cl, y0 + rl, x0 + ch, y0 + rh)


def _snap(box, cell, gx, gy):
    """Move each side of the box to the book's real edge: the strongest straight line near it (within the cell), if
    it clearly stands out from the table around it. Fixes a box that is a few pixels too big, or a cover whose bottom
    looks like the table."""
    x0, y0, x1, y1 = box
    cx0, cy0, cx1, cy1 = cell
    w, h = max(1, x1 - x0), max(1, y1 - y0)

    def best(profile, lo, hi, lo_limit, hi_limit, ref):
        lo, hi = max(lo_limit, lo), min(hi_limit, hi)
        if hi - lo < 2:
            return None
        seg = profile[lo:hi]
        strong = max(15.0, 2.2 * float(np.median(seg)), 0.6 * float(seg.max()))
        idx = np.where(seg >= strong)[0]
        if not len(idx):
            return None
        return lo + int(idx[np.argmin(np.abs(idx + lo - ref))])     # the strong line nearest the current side
    inward_x, inward_y = int(0.06 * w), int(0.06 * h)
    rows = slice(y0 + h // 6, y1 - h // 6)
    cols = slice(x0 + w // 6, x1 - w // 6)
    vx = gx[rows].mean(axis=0) if rows.stop > rows.start else None
    hy = gy[:, cols].mean(axis=1) if cols.stop > cols.start else None
    if vx is not None:
        nx0 = best(vx, x0 - int(0.08 * w), x0 + inward_x, cx0, cx1, x0)
        nx1 = best(vx, x1 - inward_x, x1 + int(0.08 * w) + 1, cx0, cx1, x1)
        x0 = nx0 if nx0 is not None else x0
        x1 = nx1 + 1 if nx1 is not None else x1
    if hy is not None:
        ny0 = best(hy, y0 - int(0.08 * h), y0 + inward_y, cy0, cy1, y0)
        ny1 = best(hy, y1 - inward_y, y1 + int(0.08 * h) + 1, cy0, cy1, y1)
        y0 = ny0 if ny0 is not None else y0
        y1 = ny1 + 1 if ny1 is not None else y1
    return (x0, y0, x1, y1) if x1 - x0 > 0.5 * w and y1 - y0 > 0.5 * h else box


def _book_in_cell(mask, x0, y0, x1, y1, canny=None):
    """(box, angle, found) of the book inside one cell of the analysis image."""
    cell = mask[y0:y1, x0:x1]
    area = cell.size
    if area == 0:
        return (x0, y0, x1, y1), 0.0, False
    n, labels, stats, _ = cv2.connectedComponentsWithStats(cell, connectivity=8)
    comps = [(i, stats[i]) for i in range(1, n) if stats[i][cv2.CC_STAT_AREA] >= 0.015 * area]
    if not comps:
        return (x0, y0, x1, y1), 0.0, False
    total = sum(s[cv2.CC_STAT_AREA] for _, s in comps)
    if total < 0.18 * area:
        return (x0, y0, x1, y1), 0.0, False
    bx0 = min(s[cv2.CC_STAT_LEFT] for _, s in comps)
    by0 = min(s[cv2.CC_STAT_TOP] for _, s in comps)
    bx1 = max(s[cv2.CC_STAT_LEFT] + s[cv2.CC_STAT_WIDTH] for _, s in comps)
    by1 = max(s[cv2.CC_STAT_TOP] + s[cv2.CC_STAT_HEIGHT] for _, s in comps)
    angle = _tilt(canny[y0:y1, x0:x1] if canny is not None else None, bx1 - bx0, by1 - by0)
    return (x0 + bx0, y0 + by0, x0 + bx1, y0 + by1), angle, True


def _tilt(edges, bw: int, bh: int) -> float:
    """How far the book is turned, from its long straight edges (the cover's sides are the longest lines in the cell).
    Positive = turned clockwise on screen; PIL's rotate(angle) with this value straightens it. 0 if level or unsure."""
    if edges is None or bw < 10 or bh < 10:
        return 0.0
    lines = cv2.HoughLinesP(edges, 1, np.pi / 360, threshold=max(20, int(0.25 * min(bw, bh))),
                            minLineLength=int(0.45 * min(bw, bh)), maxLineGap=max(3, int(0.03 * min(bw, bh))))
    if lines is None:
        return 0.0
    devs, weights = [], []
    for x1, y1, x2, y2 in lines.reshape(-1, 4):
        theta = math.degrees(math.atan2(y2 - y1, x2 - x1)) % 180
        dev = theta if theta < 45 else theta - 180 if theta > 135 else theta - 90
        if abs(dev) <= 20:
            devs.append(dev)
            weights.append(math.hypot(x2 - x1, y2 - y1))
    if len(devs) < 2:
        return 0.0
    order = np.argsort(devs)
    cum = np.cumsum(np.asarray(weights)[order])
    med = float(np.asarray(devs)[order][np.searchsorted(cum, cum[-1] / 2)])   # length-weighted median
    return med if 1.5 <= abs(med) <= 20 else 0.0


def segment_books(img: Image.Image, rows: int, cols: int, region: Sequence[float] = FULL,
                  analysis: int = 1000) -> List[Segment]:
    """One Segment per grid position (rows*cols of them), in book order. Without OpenCV: the plain grid cells."""
    W, H = img.size
    rx0, ry0, rx1, ry1 = (region[0] * W, region[1] * H, region[2] * W, region[3] * H)
    if cv2 is None:
        return _grid_segments(W, H, rows, cols, region)
    s = min(1.0, analysis / max(W, H))
    small = img.convert("RGB").resize((max(1, int(W * s)), max(1, int(H * s))), Image.BILINEAR)
    rgb = np.asarray(small)
    mask = _book_mask(rgb)
    g8 = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    gray = cv2.GaussianBlur(g8, (3, 3), 0).astype(np.float32)
    gy = np.abs(cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3))
    gx = np.abs(cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3))
    med = float(np.median(g8))
    canny = cv2.Canny(g8, int(max(10, 0.5 * med)), int(min(255, med + 30))) > 0
    hlines = (canny & (gy > gx)).astype(np.float32)       # edge pixels of horizontal lines (top/bottom of books)
    vlines = (canny & (gx >= gy)).astype(np.float32)      # ... of vertical lines (left/right of books)
    sx0, sy0, sx1, sy1 = int(rx0 * s), int(ry0 * s), int(rx1 * s), int(ry1 * s)
    # where the books are: the grid is spread over the books, not over the whole photo (empty table around them)
    rsx0, rsy0, rsx1, rsy1 = sx0, sy0, sx1, sy1             # the region: outer limit for a book's box
    sy0, sy1 = _extent(mask[sy0:sy1, sx0:sx1].mean(axis=1), sy0, sy1)
    sx0, sx1 = _extent(mask[sy0:sy1, sx0:sx1].mean(axis=0), sx0, sx1)
    sub = mask[sy0:sy1, sx0:sx1].astype(np.float32)
    row_cuts = [sy0 + c for c in _splits(sub, hlines[sy0:sy1, sx0:sx1], 0, sy1 - sy0, rows, axis=0)]
    found_boxes = []          # (index, box in analysis pixels, cell, angle, found)
    for r in range(rows):
        ya, yb = row_cuts[r], row_cuts[r + 1]
        band = mask[ya:yb, sx0:sx1].astype(np.float32)
        col_cuts = [sx0 + c for c in _splits(band, vlines[ya:yb, sx0:sx1], 0, sx1 - sx0, cols, axis=1)]
        for c in range(cols):
            xa, xb = col_cuts[c], col_cuts[c + 1]
            # the outermost books may reach past where the "book" pixels seemed to end: search up to the region's edge
            lim = (rsx0 if c == 0 else xa, rsy0 if r == 0 else ya, rsx1 if c == cols - 1 else xb,
                   rsy1 if r == rows - 1 else yb)
            (bx0, by0, bx1, by1), angle, found = _book_in_cell(mask, xa, ya, xb, yb, canny.astype(np.uint8) * 255)
            if found and not angle:
                bx0, by0, bx1, by1 = _snap(_trim(mask, (bx0, by0, bx1, by1)), lim, gx, gy)
            found_boxes.append([(bx0, by0, bx1, by1), lim, angle, found])
    _match_sizes(found_boxes, gx, gy)
    out: List[Segment] = []
    for (bx0, by0, bx1, by1), _cell, angle, found in found_boxes:
        box = (int(bx0 / s), int(by0 / s), int(math.ceil(bx1 / s)), int(math.ceil(by1 / s)))
        box = (max(0, box[0]), max(0, box[1]), min(W, box[2]), min(H, box[3]))
        out.append(Segment(box, angle, found))
    return out


def _match_sizes(items, gx, gy) -> None:
    """Books of one set are usually about the same size. A box clearly smaller than the others (part of a cover that
    looks like the table was missed) is grown to the strong edge line nearest to where a typical book would end."""
    ok = [b for b, _c, a, f in items if f and not a]
    if len(ok) < 3:
        return
    mw = float(np.median([b[2] - b[0] for b in ok]))
    mh = float(np.median([b[3] - b[1] for b in ok]))
    for it in items:
        (x0, y0, x1, y1), (cx0, cy0, cx1, cy1), angle, found = it
        if not found or angle:
            continue
        w, h = x1 - x0, y1 - y0

        def line_near(profile, target, lo, hi):
            lo, hi = max(0, int(lo)), min(len(profile), int(hi))
            if hi - lo < 3:
                return None
            seg = profile[lo:hi]
            strong = max(15.0, 2.2 * float(np.median(seg)))
            idx = np.where(seg >= strong)[0]
            return None if not len(idx) else lo + int(idx[np.argmin(np.abs(idx + lo - target))])
        if h < 0.93 * mh:
            hy = gy[:, x0 + w // 6: max(x0 + w // 6 + 1, x1 - w // 6)].mean(axis=1)
            down = line_near(hy, y0 + mh, y1, min(cy1, y0 + 1.15 * mh))
            up = line_near(hy, y1 - mh, max(cy0, y1 - 1.15 * mh), y0)
            cands = [(abs((d + 1 - y0) - mh), (y0, d + 1)) for d in [down] if d is not None]
            cands += [(abs((y1 - u) - mh), (u, y1)) for u in [up] if u is not None]
            if cands:
                y0, y1 = min(cands)[1]
        if w < 0.93 * mw:
            vx = gx[y0 + h // 6: max(y0 + h // 6 + 1, y1 - h // 6)].mean(axis=0)
            right = line_near(vx, x0 + mw, x1, min(cx1, x0 + 1.15 * mw))
            left = line_near(vx, x1 - mw, max(cx0, x1 - 1.15 * mw), x0)
            cands = [(abs((r + 1 - x0) - mw), (x0, r + 1)) for r in [right] if r is not None]
            cands += [(abs((x1 - lft) - mw), (lft, x1)) for lft in [left] if lft is not None]
            if cands:
                x0, x1 = min(cands)[1]
        it[0] = (x0, y0, x1, y1)


def _grid_segments(W, H, rows, cols, region) -> List[Segment]:
    x0, y0, x1, y1 = region[0] * W, region[1] * H, region[2] * W, region[3] * H
    cw, ch = (x1 - x0) / cols, (y1 - y0) / rows
    return [Segment((int(x0 + c * cw), int(y0 + r * ch), int(x0 + (c + 1) * cw), int(y0 + (r + 1) * ch)), 0.0, False)
            for r in range(rows) for c in range(cols)]


# ---- cutting -------------------------------------------------------------------------------------
def cut_book(img: Image.Image, seg: Segment, margin: float = 0.04, others: Sequence[Segment] = ()) -> Image.Image:
    """The book from the full-resolution photo, straightened, with `margin` (fraction of the book's size) of table
    around it on every side - the same on the left and the right, so the cover sits in the middle. `others` = the
    other books' segments: the margin never reaches past halfway to a neighbouring book, so no part of it shows."""
    x0, y0, x1, y1 = seg.box
    w, h = max(1, x1 - x0), max(1, y1 - y0)
    m = max(0.0, margin)
    if not seg.angle:
        mx, my = int(round(w * m)), int(round(h * m))
        left, top, right, bottom = x0 - mx, y0 - my, x1 + mx, y1 + my
        for o in others:
            if o is seg or not o.found:
                continue
            ox0, oy0, ox1, oy1 = o.box
            side_by_side = min(y1, oy1) - max(y0, oy0) > 0.3 * h
            stacked = min(x1, ox1) - max(x0, ox0) > 0.3 * w
            if side_by_side and ox0 >= x1 - 0.1 * w:          # a neighbour to the right
                right = min(right, max(x1, (x1 + ox0) // 2))
            if side_by_side and ox1 <= x0 + 0.1 * w:          # ... to the left
                left = max(left, min(x0, (x0 + ox1 + 1) // 2))
            if stacked and oy0 >= y1 - 0.1 * h:               # below
                bottom = min(bottom, max(y1, (y1 + oy0) // 2))
            if stacked and oy1 <= y0 + 0.1 * h:               # above
                top = max(top, min(y0, (y0 + oy1 + 1) // 2))
        # keep the cover centred: the same room on opposite sides (the smaller of the two)
        rx, ry = min(x0 - left, right - x1), min(y0 - top, bottom - y1)
        return img.crop((max(0, x0 - rx), max(0, y0 - ry), min(img.width, x1 + rx), min(img.height, y1 + ry)))
    # turned book: the box above is around the tilted cover. Straighten a generous area, then cut the cover's own size
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    a = math.radians(abs(seg.angle))
    # size of the upright cover inside its tilted bounding box
    cosa, sina = math.cos(a), math.sin(a)
    denom = cosa * cosa - sina * sina
    if abs(denom) > 0.2:
        bw, bh = (w * cosa - h * sina) / denom, (h * cosa - w * sina) / denom
        if bw <= 0 or bh <= 0:
            bw, bh = w, h
    else:
        bw, bh = w, h
    R = int(max(w, h) * (0.75 + m)) + 2
    area = img.crop((int(cx - R), int(cy - R), int(cx + R), int(cy + R)))
    area = area.rotate(seg.angle, resample=Image.BICUBIC, expand=False, fillcolor=(255, 255, 255))
    ow, oh = bw * (1 + 2 * m), bh * (1 + 2 * m)
    return area.crop((int(R - ow / 2), int(R - oh / 2), int(R + ow / 2), int(R + oh / 2)))


def draw_segments(img: Image.Image, segs: List[Segment]) -> Image.Image:
    """A preview: every book's box drawn and numbered (green = found, orange = fell back to the grid cell)."""
    from .grid import font
    out = img.convert("RGB").copy()
    d = ImageDraw.Draw(out)
    lw = max(2, int(max(out.size) / 400))
    f = font(max(18, int(max(out.size) / 40)))
    for n, sg in enumerate(segs, 1):
        colour = (0, 170, 60) if sg.found else (240, 140, 0)
        d.rectangle(sg.box, outline=colour, width=lw)
        d.text((sg.box[0] + 3 * lw, sg.box[1] + 2 * lw), f"{n}" + (f"  {sg.angle:+.0f}°" if sg.angle else ""),
               font=f, fill=colour)
    return out


def save_segments(path: Path, img_size, segs: List[Segment]) -> None:
    Path(path).write_text(json.dumps({"image_size": list(img_size), "books": [asdict(s) for s in segs]}),
                          encoding="utf-8")


def load_segments(path: Path, img_size=None) -> Optional[List[Segment]]:
    """The boxes saved for a front photo (None if missing, or made for a different-sized image)."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return None
    if img_size is not None and list(img_size) != list(data.get("image_size") or []):
        return None
    return [Segment(tuple(b["box"]), b.get("angle", 0.0), b.get("found", True)) for b in data.get("books") or []]


def find_books(img: Image.Image, grid, region=FULL, method: str = "auto") -> List[Segment]:
    """SEGMENT=auto: look at the photo (segment_books); SEGMENT=grid or no OpenCV: equal grid cells."""
    rows, cols = grid
    if method == "grid" or not available():
        return _grid_segments(img.width, img.height, rows, cols, region)
    try:
        return segment_books(img, rows, cols, region)
    except Exception:                       # never let an odd photo stop processing
        return _grid_segments(img.width, img.height, rows, cols, region)
