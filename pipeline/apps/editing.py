"""Everything the review window does to your data, with no window code in it (so it can be tested and reused).

Rules: a save changes ONLY the fields you edited of ONE book, in the same data/books.csv as every other record; the file
is locked while it is read-modify-written, so the Telegram bot and this window can run at the same time safely.
"""

from __future__ import annotations

import hashlib
import io
from pathlib import Path

from PIL import Image, ImageOps

from ..core import store
from ..core.config import Config
from ..core.isbn import normalize
from ..core.validate import CONDITIONS, DONE, refresh_status, row_issues  # noqa: F401
from ..photos import decode as _decode  # noqa: F401  (HEIC support for thumbnails)

EDITABLE = (
    "isbn13",
    "title",
    "author",
    "publisher",
    "year",
    "pages",
    "genre",
    "condition",
    "price",
    "currency",
    "market_price",
    "market_currency",
    "market_source",
    "market_url",
    "market_match",
    "market_isbn",
    "notes",
    "price_basis",
)


def public(r: dict, cfg: Config) -> dict:
    """A book as the review window shows it: its fields, what is wrong, and which pictures to show."""
    issues = row_issues(r, cfg)
    pics = []
    whole = r.get("set_photo") or r.get("front_photo")
    if r.get("front_photo") and r["front_photo"] != whole:  # its own cover, cut out of the set photo
        pics.append({"label": "This book (front)", "path": r["front_photo"], "pos": 0})
    elif whole:  # older books: its grid cell of the set photo
        pics.append({"label": "This book (front)", "path": whole, "pos": int(r.get("position") or 0)})
    if r.get("back_photo"):
        pics.append({"label": "Back photo", "path": r["back_photo"], "pos": 0})
    if r.get("front_back_photo") and Path(r["front_back_photo"]).exists():
        pics.append({"label": "Front + back", "path": r["front_back_photo"], "pos": 0})
    if whole:  # with every book's number on it, and this book outlined
        pics.append(
            {"label": "Whole set", "path": whole, "pos": 0, "numbered": True, "mark": int(r.get("position") or 0)}
        )
    done = r.get("status") in DONE
    to_check = not issues and not done and r.get("status") != "validated"
    return {
        **{k: r.get(k, "") for k in store.COLUMNS},
        "issues": issues,
        "photos": pics,
        "to_check": to_check,  # complete, but you have not confirmed it yet
        "attention": not done and (bool(issues) or to_check),
    }


# ---- actions (also used by tests) -------------------------------------------------------------
def list_rows(cfg: Config, only_attention: bool = True) -> list:
    rows = [public(r, cfg) for r in store.read_rows(cfg.csv_path)]
    return [r for r in rows if r["attention"]] if only_attention else rows


def todo_text(cfg: Config, limit: int = 0) -> str:
    rows = list_rows(cfg)
    fix = [r for r in rows if r["issues"]]
    check = [r for r in rows if r["to_check"]]
    if not rows:
        return "Nothing to do - every book is checked."
    lines = []
    if fix:
        lines.append(f"{len(fix)} book(s) need fixing:")
        for r in fix[: limit or None]:
            where = f"{r['set_id']} #{r['position']}" if r["set_id"] else r["sku"]
            lines.append(
                f"  {where}  {r['title'] or r['isbn13'] or '(unknown book)'}: "
                + "; ".join(i["msg"].split(":")[0] for i in r["issues"])
            )
        if limit and len(fix) > limit:
            lines.append(f"  ... and {len(fix) - limit} more")
    if check:
        lines.append(f"{len(check)} book(s) are complete and just need a quick check.")
    lines.append("On the computer: double-click review.bat (or run: python -m pipeline review)")
    return "\n".join(lines)


def save_row(cfg: Config, sku: str, fields: dict) -> dict:
    """Change ONLY the given fields of ONE book. The file is re-read under the lock first, so rows the bot added or
    changed in the meantime are kept."""

    def change(rows):
        r = next((x for x in rows if x["sku"] == sku), None)
        if r is None:
            raise KeyError(f"book {sku} not found in {cfg.csv_path}")
        before = dict(r)
        changed = False
        for k, v in fields.items():
            if k not in EDITABLE:
                continue
            v = "" if v is None else str(v).strip()
            if k == "isbn13":
                v = normalize(v) or "".join(ch for ch in v if ch.isdigit() or ch in "Xx")  # keep a typo visible
            if k == "price":
                v = v.replace("$", "").replace(",", "").strip()
            if k == "genre":
                from ..sources.genre import join_genres, split_genres

                v = join_genres(split_genres(v))  # one way of writing them: 童書 > 冒險／驚悚小說
            if k in ("currency", "market_currency"):
                v = v.upper()
            if k == "market_price":
                v = v.replace(",", "").replace("NT$", "").replace("$", "").strip()
            if (r.get(k) or "") != v:
                r[k], changed = v, True
        if (
            "price" in fields
            and (r.get("price") or "") != (before.get("price") or "")
            and not fields.get("price_basis")
        ):
            r["price_basis"] = "manual"  # you typed it: never overwritten by automatic pricing
        if r.get("market_price") and not r.get("market_currency"):
            r["market_currency"] = "TWD"
        if r.get("price") and not r.get("currency"):
            r["currency"] = cfg.default_currency
        if changed and "manual" not in (r.get("book_source") or ""):
            r["book_source"] = "+".join(x for x in (r.get("book_source"), "manual") if x)
        refresh_status(r, cfg, confirm=True)  # saving in the review window = you checked it
        return r

    return public(store.update_rows(cfg.csv_path, change), cfg)


def fill_set(cfg: Config, batch: str, set_id: str, condition: str = "", price: str = "", currency: str = "") -> int:
    """Condition / price for every book of a set where it is still EMPTY. Returns how many fields were filled."""
    price = str(price or "").replace("$", "").strip()
    currency = (currency or cfg.default_currency).upper()

    def change(rows):
        n = 0
        for r in rows:
            if r.get("batch") != batch or r.get("set_id") != set_id or r.get("status") in DONE:
                continue
            if condition and not r.get("condition"):
                r["condition"], n = condition, n + 1
            if price and not r.get("price"):
                r["price"], r["currency"], r["price_basis"], n = price, currency, "manual", n + 1
            refresh_status(r, cfg)
        return n

    return store.update_rows(cfg.csv_path, change)


def lookup_fields(cfg: Config, isbn: str, title: str = "") -> dict:
    """Fetch title/author/... AND the market price of a new copy for a typed ISBN. Nothing is saved until Save."""
    from ..sources.lookup import FIELDS, lookup_book

    n = normalize(isbn or "")
    if not n:
        return {"error": "That is not a valid ISBN - check the digits."}
    meta = lookup_book(n, cfg)
    got = {k: meta.get(k, "") for k in FIELDS if meta.get(k)}
    market = market_for(cfg, n, got.get("title") or title, author=got.get("author", ""))
    if not got and not market.get("fields"):
        return {
            "error": "No book found for this ISBN in any source. Type the details in by hand.",
            "problems": meta.get("_errors", []),
        }
    return {
        "isbn13": n,
        "fields": {**got, **market.get("fields", {})},
        "source": meta.get("source", ""),
        "market_note": market.get("note", ""),
        "problems": meta.get("_errors", []) + market.get("problems", []),
    }


def market_for(cfg: Config, isbn: str, title: str, use_cache: bool = True, author: str = "") -> dict:
    """Market price of a new copy + the suggested price, for the review window. Nothing is saved."""
    from ..sources.market import MARKET_FIELDS, find_market_price, suggest_price

    got = find_market_price(isbn, title, cfg, use_cache, author=author)
    problems = got.pop("_errors", [])
    if not got:
        return {"fields": {}, "note": "No market price found (eslite / books.com.tw / NCL).", "problems": problems}
    fields = {k: got[k] for k in MARKET_FIELDS if got.get(k)}
    note = f"New copy: {got['market_price']} {got['market_currency']} via {got['market_source']}"
    if got.get("market_match") == "similar":
        note += " - ANOTHER EDITION (same title and author), check it"
    elif got.get("market_match") == "ebook":
        note += " - the E-BOOK's price (same ISBN), check it"
    s = suggest_price(got["market_price"], got["market_currency"], cfg, got.get("market_match", ""))
    if s:
        fields["suggested_price"], fields["suggested_currency"], fields["price_basis"] = (
            s["price"],
            s["currency"],
            s["price_basis"],
        )
        note += f" -> suggested {s['price']} {s['currency']} ({s['price_basis']})"
    return {"fields": fields, "note": note, "problems": problems}


def market_from_link(cfg: Config, url: str, isbn: str) -> dict:
    """A shop link you pasted -> market price fields + suggested price (same shape as market_for). Nothing is saved."""
    from ..sources.market import price_from_link, suggest_price

    try:
        got = price_from_link(url, isbn, cfg)
    except ValueError as exc:
        return {"fields": {}, "note": str(exc), "problems": []}
    fields = dict(got)
    s = suggest_price(got["market_price"], got["market_currency"], cfg)
    if s:
        fields["suggested_price"], fields["suggested_currency"], fields["price_basis"] = (
            s["price"],
            s["currency"],
            s["price_basis"],
        )
    return {
        "fields": fields,
        "note": f"New copy: {got['market_price']} {got['market_currency']} from your link",
        "problems": [],
    }


def suggestion(cfg: Config, market_price: str, market_currency: str) -> dict:
    """Your price from a market price you typed or corrected: {"price", "currency", "price_basis"} or {"error"}."""
    from ..sources.market import suggest_price

    s = suggest_price(market_price, market_currency or "TWD", cfg)
    return s or {"error": "Type a market price first (a number)."}


# ---- products (the 'Sell' window) -----------------------------------------------------------------
def list_products(cfg: Config) -> list:
    """Every product as the Sell window shows it: data/products.csv brought up to date, plus its Shopee listing and
    its books."""
    from .products import books_of, refresh_products

    sets = refresh_products(cfg)
    listings = store.read_table(cfg.listings_path)
    books = store.read_rows(cfg.csv_path)
    out = []
    for st in sets:
        li = next((x for x in listings if x["product_code"] == st["product_code"] and x["platform"] == "shopee"), {})
        group = books_of(cfg, st["product_code"], books)
        out.append(
            {
                **st,
                "shopee": dict(li),
                "books": [
                    {
                        "position": b.get("position"),
                        "title": b.get("title", ""),
                        "price": b.get("price", ""),
                        "status": b.get("status", ""),
                    }
                    for b in group
                ],
            }
        )
    return out


def set_sell_mode(cfg: Config, batch: str, set_id: str, mode: str) -> list:
    """The review window's 'Sell this set': "set" = one product, "book" = each book its own product."""
    from .products import set_sell_mode as change

    return change(cfg, batch, set_id, mode)


def save_product(cfg: Config, code: str, fields: dict) -> dict:
    from .products import save_product_fields

    return save_product_fields(cfg, code, fields)


def product_status(cfg: Config, code: str, status: str) -> list:
    from .products import change_product_status

    return change_product_status(cfg, code, status)


def shopee_link(cfg: Config, code: str, url: str) -> dict:
    from .shopee import set_link

    return set_link(cfg, code, url)


def shopee_upload(cfg: Config, redo=()) -> dict:
    """The Sell window's 'Make Shopee upload file' (and 'Make a new upload file for it' = redo): reads any Shopee
    report waiting in the import folder, then makes the upload file. {"text": what to show, "file": the file or None}"""
    from .shopee import ShopeeError, run

    try:
        out = run(cfg, redo=redo)
    except ShopeeError as exc:
        return {"text": f"Not done: {exc}", "file": None}
    return {"text": "\n".join(out["text"]), "file": (out.get("export") or {}).get("file")}


def shopee_read(cfg: Config, files: list) -> dict:
    """The Sell window's 'Read Shopee report...': the order report / product list you chose (left where it is)."""
    from .shopee import ShopeeError, run

    try:
        out = run(cfg, files=files, do_export=False)
    except ShopeeError as exc:
        return {"text": f"Not done: {exc}", "file": None}
    return {"text": "\n".join(out["text"]) or "Nothing in it for any product.", "file": None}


# ---- photos -----------------------------------------------------------------------------------
def thumbnail_bytes(cfg: Config, path: str, width: int, pos: int = 0, numbered: bool = False, mark: int = 0) -> bytes:
    """A small JPEG of a photo (turned upright, HEIC ok), cached in data/cache/thumbs so big photos load once.
    pos > 0: only book number `pos`, cut out of the front photo using GRID (nothing is stored for this).
    numbered: the set photo with every book's number on it (where the books were found, segments.json), and book
    `mark` outlined."""
    from ..photos.grid import crop_book

    p = Path(path)
    st = p.stat()
    seg = p.with_name("segments.json")  # books found again ('covers --redo'): numbers move with them
    seg_time = seg.stat().st_mtime_ns if numbered and seg.exists() else 0
    key = hashlib.sha1(
        f"{p.resolve()}|{st.st_mtime_ns}|{width}|{pos}|{numbered}|{mark}|{seg_time}|{cfg.grid}|{cfg.region}".encode(
            "utf-8"
        )
    ).hexdigest()
    cached = cfg.cache_dir / "thumbs" / f"{key}.jpg"
    if cached.exists():
        return cached.read_bytes()
    with Image.open(p) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        if pos:
            im = crop_book(im, pos, cfg.grid, cfg.region) or im
        elif numbered:
            im = numbered_photo(cfg, p, im, mark)
        im.thumbnail((width, width * 2))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=88)
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_bytes(buf.getvalue())
    return buf.getvalue()


def numbered_photo(cfg: Config, path: Path, im: Image.Image, mark: int = 0) -> Image.Image:
    """The set photo with each book's number (as on the review sheet and the listing photo), book `mark` outlined."""
    from PIL import ImageDraw

    from ..photos.grid import cell_box, numbered, position_to_cell
    from ..photos.segment import load_segments

    segs = load_segments(path.with_name("segments.json"), im.size)
    boxes = [sg.box for sg in segs] if segs else None
    out = numbered(im, *cfg.grid, region=cfg.region, boxes=boxes)
    rows, cols = cfg.grid
    if 1 <= mark <= rows * cols:
        box = (
            boxes[mark - 1]
            if boxes and mark <= len(boxes)
            else cell_box(*position_to_cell(mark, rows, cols), rows, cols, im.size, region=cfg.region)
        )
        ImageDraw.Draw(out).rectangle(box, outline=(37, 99, 235), width=max(4, int(max(im.size) / 250)))
    return out
