"""Find ISBN barcodes in photos.

Engines (set DECODERS in .env, tried in that order, first ISBN wins):
  zxing   - zxing-cpp (pip install zxing-cpp). Best all-rounder.
  zbar    - pyzbar (pip install pyzbar). Different algorithm: often reads what zxing misses (blur, glare).
  opencv  - OpenCV's barcode module (pip install opencv-python-headless). Weakest, but another opinion.
Engines that are not installed are skipped silently.

For a photo of ONE book (a barcode photo) `decode_isbn_image` tries the picture several ways
(sizes, contrast, sharpening, small tilts, tiles) until something reads. DECODE_EFFORT=fast|normal|max sets how hard it tries.

Taiwanese books usually carry two EAN-13 barcodes: the ISBN (978/979...) and a price/product code (often 471...).
Only 978/979 codes that pass the ISBN checksum are accepted, so the price barcode is ignored.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional

from PIL import Image, ImageFilter, ImageOps

from ..core.isbn import normalize

try:  # iPhone HEIC support
    import pillow_heif

    pillow_heif.register_heif_opener()
except Exception:  # pragma: no cover
    pass

try:
    import zxingcpp
except ImportError:  # pragma: no cover
    zxingcpp = None
try:
    import cv2
    import numpy as np
except ImportError:  # pragma: no cover
    cv2 = None
try:
    from pyzbar import pyzbar
except Exception:  # pragma: no cover  (ImportError, or the zbar DLL is missing)
    pyzbar = None

EAN13_MODULES = 95  # bars + spaces across one EAN-13 barcode, excluding quiet zones

# ---- settings (set from .env by configure()) ---------------------------------------------------
_SETTINGS = {"decoders": ("zxing", "zbar", "opencv"), "effort": "normal"}


def configure(decoders=None, effort=None) -> None:
    if decoders:
        _SETTINGS["decoders"] = tuple(d.strip().lower() for d in decoders if d.strip())
    if effort:
        _SETTINGS["effort"] = effort.strip().lower()


def available_engines() -> List[str]:
    have = {"zxing": zxingcpp is not None, "zbar": pyzbar is not None, "opencv": cv2 is not None}
    return [d for d in _SETTINGS["decoders"] if have.get(d)]


@dataclass
class Detection:
    isbn: str
    cx: Optional[float] = None       # centre of the barcode, in image pixels
    cy: Optional[float] = None
    width: Optional[float] = None    # barcode length in pixels

    @property
    def px_per_module(self) -> Optional[float]:
        return self.width / EAN13_MODULES if self.width else None


def load_image(path) -> Image.Image:
    img = Image.open(path)
    img = ImageOps.exif_transpose(img)
    return img.convert("RGB")


_load = load_image  # backwards compatible name


def rotate_cw(img: Image.Image, degrees: int) -> Image.Image:
    """Turn the picture clockwise by 0/90/180/270 degrees."""
    d = degrees % 360
    return img if d == 0 else img.rotate(-d, expand=True)


def exif_orientation(path):
    """The EXIF Orientation tag (1 = already upright, 6/8 = sideways, 3 = upside down) or None if the file has none."""
    try:
        with Image.open(path) as im:
            return im.getexif().get(0x0112)
    except Exception:
        return None


# ---- engines -----------------------------------------------------------------------------------
ADDONS: dict = {}   # ISBN -> the 5-digit add-on barcode next to it (price on many English books), when read


def split_addon(text: str):
    """The ISBN and the small add-on barcode next to it, whatever way the reader joined them:
    '9781862305717 50399' or '978186230571750399' -> ('9781862305717', '50399'); no add-on -> (text, '')."""
    digits = re.findall(r"\d+", str(text or ""))
    if len(digits) >= 2 and len(digits[0]) == 13 and len(digits[1]) in (2, 5):
        return digits[0], digits[1]
    if len(digits) == 1 and len(digits[0]) in (15, 18) and normalize(digits[0][:13]):
        return digits[0][:13], digits[0][13:]
    return str(text or ""), ""


def _zxing(img: Image.Image, binarizer: str = "") -> List[Detection]:
    kw = {}
    try:
        kw["formats"] = zxingcpp.BarcodeFormat.EAN13
        if binarizer:
            kw["binarizer"] = getattr(zxingcpp.Binarizer, binarizer)
        results = list(zxingcpp.read_barcodes(img, **kw))     # 1) the ISBN alone - exactly as before
        if results:                                           # 2) only then: also try to read the price add-on
            try:
                with_addon = zxingcpp.read_barcodes(img, ean_add_on_symbol=zxingcpp.EanAddOnSymbol.Read, **kw)
                for r in with_addon:
                    main, addon = split_addon(r.text)
                    if normalize(main) and len(addon) == 5:
                        ADDONS[normalize(main)] = addon
            except (TypeError, AttributeError):
                pass
    except (TypeError, AttributeError):  # older zxing-cpp without these options
        results = zxingcpp.read_barcodes(img)
    out = []
    for r in results:
        main, addon = split_addon(r.text)
        isbn = normalize(main)
        if not isbn:
            continue
        if len(addon) == 5:
            ADDONS[isbn] = addon
        try:
            p = r.position
            pts = [(p.top_left.x, p.top_left.y), (p.top_right.x, p.top_right.y),
                   (p.bottom_right.x, p.bottom_right.y), (p.bottom_left.x, p.bottom_left.y)]
            cx = sum(x for x, _ in pts) / 4
            cy = sum(y for _, y in pts) / 4
            width = max(math.dist(pts[0], pts[1]), math.dist(pts[1], pts[2]))
        except Exception:
            cx = cy = width = None
        out.append(Detection(isbn, cx, cy, width))
    return out


def _zbar(img: Image.Image) -> List[Detection]:
    out = []
    try:
        symbols = [pyzbar.ZBarSymbol.EAN13, pyzbar.ZBarSymbol.ISBN13]
        results = pyzbar.decode(img.convert("L"), symbols=symbols)
    except Exception:
        results = pyzbar.decode(img.convert("L"))
    for r in results:
        isbn = normalize(r.data.decode("ascii", "ignore") if isinstance(r.data, bytes) else str(r.data))
        if not isbn:
            continue
        rc = r.rect
        out.append(Detection(isbn, rc.left + rc.width / 2, rc.top + rc.height / 2, float(max(rc.width, rc.height)) or None))
    return out


_CV_DETECTOR = None


def _opencv(img: Image.Image) -> List[Detection]:
    global _CV_DETECTOR
    if _CV_DETECTOR is None:
        _CV_DETECTOR = cv2.barcode.BarcodeDetector()
    arr = np.asarray(img.convert("L"))
    res = _CV_DETECTOR.detectAndDecodeWithType(arr)
    infos, pts = (res[1] if len(res) > 1 else ()), (res[3] if len(res) > 3 else None)
    out = []
    for i, text in enumerate(infos or ()):
        isbn = normalize(text) if text else None
        if not isbn:
            continue
        cx = cy = width = None
        if pts is not None and i < len(pts):
            q = np.asarray(pts[i]).reshape(-1, 2)
            cx, cy = float(q[:, 0].mean()), float(q[:, 1].mean())
            width = float(max(np.ptp(q[:, 0]), np.ptp(q[:, 1])))
        out.append(Detection(isbn, cx, cy, width))
    return out


def _run(name: str, img: Image.Image, **kw) -> List[Detection]:
    try:
        if name == "zxing":
            return _zxing(img, **kw)
        if name == "zbar":
            return _zbar(img)
        if name == "opencv":
            return _opencv(img)
    except Exception:  # one engine crashing on an odd image must not stop the others
        return []
    return []


def _read_detections(img: Image.Image, engines: Optional[Iterable[str]] = None, first_only: bool = False) -> List[Detection]:
    names = list(engines) if engines is not None else available_engines()
    if not names:
        raise RuntimeError("No barcode reader installed. Run: pip install zxing-cpp  (and optionally pyzbar)")
    out: List[Detection] = []
    for name in names:
        got = _run(name, img)
        out += got
        if got and first_only:
            break
    return out


# ---- single-book photo: try hard -------------------------------------------------------------
def _scaled(img: Image.Image, longest: int) -> Image.Image:
    m = max(img.size)
    if m <= longest:
        return img
    s = longest / m
    return img.resize((max(1, int(img.width * s)), max(1, int(img.height * s))), Image.LANCZOS)


def _enhanced(img: Image.Image) -> Image.Image:
    g = ImageOps.autocontrast(img.convert("L"), cutoff=1)
    return g.filter(ImageFilter.UnsharpMask(radius=2, percent=160, threshold=2)).convert("RGB")


def _tiles(img: Image.Image, n: int, overlap: float = 0.25):
    """n x n overlapping tiles, each enlarged so a small barcode gets more pixels per bar."""
    W, H = img.size
    tw, th = W / n, H / n
    for r in range(n):
        for c in range(n):
            box = (int(max(0, (c - overlap) * tw)), int(max(0, (r - overlap) * th)),
                   int(min(W, (c + 1 + overlap) * tw)), int(min(H, (r + 1 + overlap) * th)))
            t = img.crop(box)
            if max(t.size) < 1600:
                t = t.resize((t.width * 2, t.height * 2), Image.LANCZOS)
            yield t


def _located_crops(img: Image.Image) -> Iterable[Image.Image]:
    """Use OpenCV to FIND barcode-shaped areas (even ones it cannot read), then cut each one out, turn it level and
    enlarge it, so the readers get a straight, big barcode. This is what rescues tilted or small barcodes."""
    if cv2 is None:
        return
    small = _scaled(img, 1600)
    s = img.width / small.width
    try:
        ok, pts = cv2.barcode.BarcodeDetector().detect(np.asarray(small.convert("L")))
    except Exception:
        return
    if not ok or pts is None:
        return
    full = np.asarray(img.convert("RGB"))
    for quad in np.asarray(pts).reshape(-1, 4, 2):
        quad = quad.astype("float32") * s
        (cx, cy), (w, h), ang = cv2.minAreaRect(quad)
        if w < h:  # make the long side horizontal
            w, h, ang = h, w, ang + 90
        w, h = w * 1.35, h * 1.6  # keep the quiet zone around the bars
        if w < 10 or h < 5:
            continue
        box = cv2.boxPoints(((cx, cy), (w, h), ang)).astype("float32")
        # order the corners: top-left, top-right, bottom-right, bottom-left
        sm, df = box.sum(axis=1), np.diff(box, axis=1).ravel()
        src = np.array([box[np.argmin(sm)], box[np.argmin(df)], box[np.argmax(sm)], box[np.argmax(df)]], dtype="float32")
        scale = max(1.0, 1100 / w)
        W, H = int(w * scale), int(h * scale)
        dst = np.array([[0, 0], [W - 1, 0], [W - 1, H - 1], [0, H - 1]], dtype="float32")
        warped = cv2.warpPerspective(full, cv2.getPerspectiveTransform(src, dst), (W, H), flags=cv2.INTER_CUBIC,
                                     borderValue=(255, 255, 255))
        crop = Image.fromarray(warped)
        yield crop
        yield _enhanced(crop)


def _variants(img: Image.Image, effort: str = "normal") -> Iterable[Image.Image]:
    """Cheap, likely-to-work versions first."""
    yield img
    for longest in (2000, 1400, 1000):
        if max(img.size) > longest * 1.2:
            yield _scaled(img, longest)
    mid = _scaled(img, 1600)
    yield _enhanced(mid)
    yield mid.filter(ImageFilter.GaussianBlur(1.2))          # removes JPEG noise / moire on glossy covers
    if max(img.size) < 1400:                                   # small photo (Telegram 'Photo'): enlarge it
        yield img.resize((img.width * 2, img.height * 2), Image.LANCZOS)
    if effort == "fast":
        return
    yield from _located_crops(img)
    big = _scaled(img, 2600)
    for angle in (8, -8, 16, -16):                             # tilted shots: 1D readers scan lines, so a tilt hurts
        yield big.rotate(angle, expand=True, fillcolor=(255, 255, 255), resample=Image.BICUBIC)
    yield from _tiles(_scaled(img, 3000), 2)
    if effort == "max":
        for angle in (24, -24, 32, -32):
            yield big.rotate(angle, expand=True, fillcolor=(255, 255, 255), resample=Image.BICUBIC)
        yield from _tiles(_scaled(img, 4000), 3)
        yield _enhanced(img)


def _find(img: Image.Image, effort: str):
    """(the first Detection, the version of the photo it was found in) or (None, None)."""
    engines = available_engines()
    if not engines:
        raise RuntimeError("No barcode reader installed. Run: pip install zxing-cpp  (and optionally pyzbar)")
    for variant in _variants(img, effort):
        for name in engines:
            got = _run(name, variant)
            if got:
                return got[0], variant
        if "zxing" in engines and effort != "fast" and variant is img:
            for b in ("GlobalHistogram", "FixedThreshold"):   # other ways of deciding black vs white
                v = _scaled(img, 2000)
                got = _run("zxing", v, binarizer=b)
                if got:
                    return got[0], v
    return None, None


def decode_isbn_image(img: Image.Image, effort: Optional[str] = None, addon: bool = True) -> Optional[str]:
    """The ISBN in a barcode photo (None if unreadable). With addon=True it then also tries hard to read the small
    5-digit PRICE barcode next to it (stored in ADDONS[isbn]) - that price is the first choice for the market price."""
    effort = effort or _SETTINGS["effort"]
    det, variant = _find(img, effort)
    if det is None:
        return None
    if addon and det.isbn not in ADDONS:
        read_addon(variant, det.isbn, det, effort)
    return det.isbn


# ---- the price add-on (EAN-5) -------------------------------------------------------------------
def addon_crops(img: Image.Image, det: Optional[Detection]) -> List[Image.Image]:
    """The area around the ISBN barcode, widened to the RIGHT where the add-on sits (about half a barcode wide),
    enlarged so each bar gets several pixels."""
    out = []
    if det is not None and det.cx is not None and det.width:
        w = det.width
        box = (int(max(0, det.cx - 0.75 * w)), int(max(0, det.cy - 0.6 * w)),
               int(min(img.width, det.cx + 1.6 * w)), int(min(img.height, det.cy + 0.6 * w)))
        if box[2] - box[0] > 20 and box[3] - box[1] > 10:
            crop = img.crop(box)
            scale = max(1.0, 1800 / crop.width)
            if scale > 1.05:
                crop = crop.resize((int(crop.width * scale), int(crop.height * scale)), Image.LANCZOS)
            out.append(crop)
    return out


def _addon_zxing(img: Image.Image, isbn: str) -> str:
    if zxingcpp is None:
        return ""
    for mode in ("Require", "Read"):
        try:
            res = zxingcpp.read_barcodes(img, formats=zxingcpp.BarcodeFormat.EAN13,
                                         ean_add_on_symbol=getattr(zxingcpp.EanAddOnSymbol, mode))
        except (TypeError, AttributeError):
            return ""
        except Exception:
            continue
        for r in res:
            main, addon = split_addon(r.text)
            if normalize(main) == isbn and len(addon) == 5:
                return addon
    return ""


def _addon_zbar(img: Image.Image) -> str:
    """zbar can read the 5-digit add-on as a barcode of its own (when built with EAN-5 support)."""
    sym = getattr(getattr(pyzbar, "ZBarSymbol", None), "EAN5", None) if pyzbar is not None else None
    if sym is None:
        return ""
    try:
        res = pyzbar.decode(img.convert("L"), symbols=[sym])
    except Exception:
        return ""
    codes = {r.data.decode("ascii", "ignore") if isinstance(r.data, bytes) else str(r.data) for r in res}
    codes = {c for c in codes if len(c) == 5 and c.isdigit()}
    return codes.pop() if len(codes) == 1 else ""


def read_addon(img: Image.Image, isbn: str, det: Optional[Detection] = None, effort: Optional[str] = None) -> str:
    """Try hard to read the price add-on next to `isbn`'s barcode: the cut-out around the barcode (sharpened,
    thresholded, slightly turned), then the whole photo at a few sizes. Remembers the answer in ADDONS. '' if none."""
    if ADDONS.get(isbn):
        return ADDONS[isbn]
    effort = effort or _SETTINGS["effort"]
    tries: List[Image.Image] = []
    for crop in addon_crops(img, det):
        g = ImageOps.autocontrast(crop.convert("L"), cutoff=1)
        tries += [crop, _enhanced(crop), g.point(lambda v: 255 if v > 128 else 0).convert("RGB")]
        if effort != "fast":
            tries += [crop.rotate(a, expand=True, fillcolor=(255, 255, 255), resample=Image.BICUBIC) for a in (3, -3)]
    tries += [img, _scaled(img, 2000), _enhanced(_scaled(img, 1600))]
    if effort == "max":
        tries += [img.resize((img.width * 2, img.height * 2), Image.LANCZOS)] if max(img.size) < 2000 else []
    for t in tries:
        addon = _addon_zxing(t, isbn) or _addon_zbar(t)
        if addon:
            ADDONS[isbn] = addon
            return addon
    return ""


def decode_isbn(path) -> Optional[str]:
    """Return one ISBN-13 found in the photo, or None."""
    try:
        img = load_image(Path(path))
    except Exception:
        return None
    return decode_isbn_image(img)
