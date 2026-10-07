"""One marketplace post per set: the numbered front photo (+ optionally each barcode photo) and a text listing every book
by its number. Made only for sets where every book is 'validated'."""
from __future__ import annotations

import json
import shutil
from collections import OrderedDict

from PIL import Image, ImageOps

from ..photos import decode  # noqa: F401  (registers HEIC support)
from ..core.config import Config
from ..photos.grid import numbered, parse_grid, stamp_number  # noqa: F401  (parse_grid re-exported for the CLI)

from ..core.validate import CONDITION_ZH

COND_LABEL = {**CONDITION_ZH, "acceptable": CONDITION_ZH["fair"]}   # listings are for Taiwanese buyers: 近全新 ...


def make_label_sheet(dst, count: int = 10, per_row: int = 5) -> None:
    """Printable A4 sheet of big numbers to cut out and place beside each book (physical labels)."""
    from PIL import ImageDraw
    from ..photos.grid import RED, font
    W, H = 1240, 1754  # A4 @150dpi
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    cell = (W - 100) // per_row
    for i in range(count):
        x, y = 50 + (i % per_row) * cell, 50 + (i // per_row) * cell
        d.rectangle([x + 8, y + 8, x + cell - 8, y + cell - 8], outline="black", width=3)
        d.text((x + cell // 2, y + cell // 2), str(i + 1), font=font(int(cell * 0.6)), fill=RED, anchor="mm")
    img.save(dst)


def set_groups(rows: list) -> "OrderedDict":
    g: OrderedDict = OrderedDict()
    for r in rows:
        if r.get("set_id"):
            g.setdefault((r["batch"], r["set_id"]), []).append(r)
    for k in g:
        g[k].sort(key=lambda r: int(r["position"]))
    return g


def money(amount: float, currency: str) -> str:
    return f"{amount:.2f} {currency}".replace(".00 ", " ")


def price_text(b: dict) -> str:
    try:
        return money(float(b["price"]), b.get("currency") or "") if b.get("price") else ""
    except ValueError:
        return ""


def totals(books: list) -> dict:
    """{currency: sum of prices} over the books that have a price."""
    out: dict = {}
    for b in books:
        if b.get("price"):
            cur = b.get("currency") or ""
            out[cur] = out.get(cur, 0.0) + float(b["price"])
    return out


def make_set_title(books: list, limit: int = 80) -> str:
    title = f"Bundle of {len(books)} books: " + ", ".join(b["title"].split(":")[0] for b in books)
    return title if len(title) <= limit else title[: limit - 3].rstrip(", ") + "..."


def make_set_description(books: list, set_price: str = "", show_market: bool = False) -> str:
    lines = [f"Bundle of {len(books)} books. Numbers match the numbers on the photo."]
    if set_price:
        lines.append(f"Whole bundle: {set_price}")
    lines.append("")
    for b in books:
        meta = " | ".join(x for x in (", ".join(y for y in (b.get("publisher"), b.get("year")) if y),
                                       COND_LABEL.get(b.get("condition"), b.get("condition") or ""),
                                       (f"new {b['market_price']} {b.get('market_currency') or ''}".strip()
                                        if show_market and b.get("market_price") else ""),
                                       f"ISBN {b['isbn13']}") if x)
        each = f"  ({price_text(b)})" if not set_price and price_text(b) else ""
        lines += [f"{b['position']}. {b['title']} - {b['author']}{each}", f"    {meta}"]
    return "\n".join(lines)


def build_set_listings(rows: list, cfg: Config, grid=None, set_price=None, set_currency: str = "",
                       with_backs=False, force=False, numbers: bool = True) -> dict:
    """set_price given: one price for the whole bundle. Otherwise each book's own price is shown (if it has one)."""
    out = {"made": [], "skipped": []}
    for (batch, set_id), books in set_groups(rows).items():
        key = f"{batch}-{set_id}"
        bad = [str(b["position"]) for b in books if b["status"] not in ("validated", "listed", "sold")]
        if bad:
            out["skipped"].append((key, f"book(s) {', '.join(bad)} not complete yet"))
            continue
        front = books[0].get("front_photo")
        if not front:
            out["skipped"].append((key, "no front photo for this set"))
            continue
        d = cfg.listings_dir / "sets" / key
        if d.exists() and not force:
            out["skipped"].append((key, "already made (use --force to redo)"))
            continue
        d.mkdir(parents=True, exist_ok=True)
        try:
            img = ImageOps.exif_transpose(Image.open(front)).convert("RGB")
            (numbered(img, *(grid or cfg.grid), region=cfg.region) if numbers else img).save(d / "01_front.jpg",
                                                                                              quality=92)
        except Exception as exc:
            shutil.rmtree(d, ignore_errors=True)
            out["skipped"].append((key, f"front photo unreadable: {exc}"))
            continue
        files = ["01_front.jpg"]
        if with_backs:
            for b in books:
                dst = d / f"{len(files) + 1:02d}_book{int(b['position']):02d}_barcode.jpg"
                stamp_number(b["barcode_photo"], dst, int(b["position"]))
                files.append(dst.name)
        bundle = money(set_price, set_currency or cfg.default_currency) if set_price is not None else ""
        sums = totals(books)
        listing = {"key": key, "title": make_set_title(books), "description": make_set_description(books, bundle, cfg.listing_show_market),
                   "photos": files, "set_price": bundle,
                   "sum_of_book_prices": {cur: round(v, 2) for cur, v in sums.items()}}
        if bundle:
            price_line = f"Price: {bundle}"
        elif sums:
            price_line = "Price (sum of the books): " + " + ".join(money(v, cur) for cur, v in sums.items())
        else:
            price_line = "Price: not set"
        (d / "listing.json").write_text(json.dumps(listing, indent=2, ensure_ascii=False), encoding="utf-8")
        (d / "listing.txt").write_text(f"{listing['title']}\n\n{price_line}\n\n{listing['description']}\n",
                                       encoding="utf-8")
        out["made"].append(key)
    return out
