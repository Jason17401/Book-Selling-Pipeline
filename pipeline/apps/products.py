"""Products: what you sell as one thing - a set of books (the books of one set photo, sold together) or, later, a
single book - kept in data/products.csv, the same whatever platform it is sold on (each platform's copy of it is a row
in data/listings.csv, made by pipeline/apps/shopee.py). Also the older one-folder-per-set listing (`listings`)."""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from collections import OrderedDict

from PIL import Image, ImageOps

from ..photos import decode  # noqa: F401  (registers HEIC support)
from ..core import store
from ..core.config import Config
from ..photos.grid import numbered, parse_grid, stamp_number  # noqa: F401  (parse_grid re-exported for the CLI)

from ..core.validate import CONDITION_ZH, OLD_CONDITIONS

COND_LABEL = {**CONDITION_ZH, **{old: CONDITION_ZH[new] for old, new in OLD_CONDITIONS.items()}}  # buyers read 近全新 ...


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
                       with_backs=False, force=False, numbers: bool = True, with_covers: bool = True) -> dict:
    """set_price given: one price for the whole bundle. Otherwise each book's own price is shown (if it has one)."""
    out = {"made": [], "skipped": []}
    for (batch, set_id), books in set_groups(rows).items():
        key = f"{batch}-{set_id}"
        bad = [str(b["position"]) for b in books if b["status"] not in ("validated", "listed", "sold")]
        if bad:
            out["skipped"].append((key, f"book(s) {', '.join(bad)} not complete yet"))
            continue
        front = books[0].get("set_photo") or books[0].get("front_photo")
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
            from ..photos.segment import load_segments
            segs = None if grid else load_segments(Path(front).with_name("segments.json"), img.size)
            boxes = [sg.box for sg in segs] if segs else None
            (numbered(img, *(grid or cfg.grid), region=cfg.region, boxes=boxes) if numbers else img).save(
                d / "01_front.jpg", quality=92)
        except Exception as exc:
            shutil.rmtree(d, ignore_errors=True)
            out["skipped"].append((key, f"front photo unreadable: {exc}"))
            continue
        files = ["01_front.jpg"]
        if with_covers:
            for b in books:
                own = b.get("front_photo")
                if own and own != front and Path(own).exists():
                    dst = d / f"{len(files) + 1:02d}_book{int(b['position']):02d}_cover.jpg"
                    stamp_number(own, dst, int(b["position"]))
                    files.append(dst.name)
        if with_backs:
            for b in books:
                dst = d / f"{len(files) + 1:02d}_book{int(b['position']):02d}_barcode.jpg"
                stamp_number(b["back_photo"], dst, int(b["position"]))
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


# =================================================================================================
# Products: data/products.csv (one row per product, the same on every platform).
# kind "set": the books with the same batch + set_id; its code is "<batch>-<set_id>" (e.g. 20261008-153012-S01), which
# is also how every book's sku starts (20261008-153012-S01-03 = book 3 of that set). kind "book" (later): one book,
# its code = its sku. The code goes into the Shopee title, the parent SKU (主商品貨號) and the first photo, so a Shopee
# order can always be traced back to its books.
# =================================================================================================
KINDS = ("set", "book")
PRODUCT_STATUSES = ("draft", "ready", "listed", "reserved", "sold", "withdrawn")
PRODUCT_STATUS_HELP = {
    "draft": "not ready: a book is not checked yet, or something else is missing (see errors)",
    "ready": "every book checked - the next `python -m pipeline shopee` puts it in the upload file",
    "listed": "in a Shopee upload file / on Shopee (or another platform)",
    "reserved": "someone ordered it; the order is not finished yet",
    "sold": "sold (order completed)",
    "withdrawn": "you took it off sale",
}
MANUAL_STATUSES = ("ready", "listed", "sold", "withdrawn")      # what you may set by hand (product-status / Sell)
ACTIVE_LISTING = ("exported", "live")                          # a listing that is (about to be) on sale
AUTO_FIELDS = ("title", "discount", "price", "description")
QUANTITY_FIELDS = ("stock", "min_purchase")       # how many you have (庫存) / the fewest a buyer may order (最低購買數量)
MAX_QUANTITY = 999_999                            # Shopee's limit for both
# no weight or parcel size: we do not have them, and Shopee does not need them (選填 / optional in its template)
# discount and set_price go together: type one, the other follows (the one you typed last is kept in `custom`)
FOOTER_DEFAULT = ("・整套出售，恕不拆賣。\n"
                  "・皆為二手書，書況如各書所註，以照片為準。\n"
                  "・照片皆為實物拍攝。")
BOOK_FOOTER_DEFAULT = ("・二手書，書況如上所註，以照片為準。\n"
                       "・照片皆為實物拍攝。")
SELL_MODES = {"": "set", "book": "book"}          # books.csv sell_as -> the kind of product the set's books become
ON_SALE = ("listed", "reserved", "sold")          # a product in one of these can't be turned into another kind


def sell_mode(books: list) -> str:
    """How a set is sold: "set" (one product, the default) or "book" (each book its own product)."""
    return "book" if any((b.get("sell_as") or "") == "book" for b in books) else "set"


def code_for(batch: str, set_id: str) -> str:
    """The product code of a set."""
    return f"{batch}-{set_id}"


def code_of(book: dict) -> str:
    return code_for(book.get("batch", ""), book.get("set_id", "")) if book.get("set_id") else ""


def now_text() -> str:
    import time
    return time.strftime("%Y-%m-%d %H:%M:%S")


def set_title_zh(code: str, books: list, limit: int = 60) -> str:
    """The automatic listing title: 【code】二手書8本一套 ("set of 8 second-hand books"). Change it per product in
    Sell products. Shopee wants 10 to 60 characters."""
    return f"【{code}】二手書{len(books)}本一套"[:limit]


def ntd(v) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v or "")
    return f"NT${f:.0f}" if f == int(f) else f"NT${f:.2f}"


def pct(value) -> float:
    """'20', '20%', 20 -> 20.0 (a discount in percent)."""
    return float(str(value).replace("%", "").strip() or 0)


def zhe(discount: float) -> str:
    """20% off -> '8 折', 15% off -> '85 折' (how a discount is said in Taiwan); '' for an uneven discount like 17.3%
    or none."""
    rest = round(100 - discount, 6)
    if discount <= 0 or rest <= 0 or rest != int(rest):
        return ""
    return f"{rest / 10:g} 折" if rest % 10 == 0 else f"{rest:g} 折"


def discount_for(price: float, total: float) -> float:
    """The discount (in %) that makes `total` cost `price`, to one decimal: 1000 -> 800 is 20."""
    return round((1 - price / total) * 100, 1) if total else 0.0


def set_price_of(books: list, discount: float = 0.0):
    """(price of the whole set in TWD, sum of the books' prices), or (None, None) if a book has no TWD price.
    discount = percent off the sum (20 = 20% off)."""
    total = 0.0
    for b in books:
        if (b.get("currency") or "").upper() != "TWD":
            return None, None
        try:
            total += float(b.get("price") or "x")
        except ValueError:
            return None, None
    price = max(1, int(round(total * (1 - discount / 100))))
    return price, total


def set_problems(books: list, cfg: Config) -> list:
    """Why this set can't go on sale yet (empty list = it can)."""
    out = []
    unchecked = [str(b["position"]) for b in books if b.get("status") not in ("validated", "listed", "sold")]
    if unchecked:
        out.append(f"book(s) {', '.join(unchecked)} not checked yet (review window)")
    other = [f"{b['position']} ({b.get('currency') or 'no currency'})" for b in books
             if b.get("price") and (b.get("currency") or "").upper() != "TWD"]
    if other:
        out.append(f"book(s) {', '.join(other)} not priced in TWD - Shopee sells in NT$: change the price in the "
                   "review window")
    if not (books[0].get("set_photo") or books[0].get("front_photo")):
        out.append("no front photo of the set")
    return out


def footer_text(cfg: Config, kind: str = "set") -> str:
    """The lines at the end of every description: data/listings/description_footer.txt for sets,
    description_footer_book.txt for single books (made on first use, edit them freely)."""
    name, default = (("description_footer_book.txt", BOOK_FOOTER_DEFAULT) if kind == "book"
                     else ("description_footer.txt", FOOTER_DEFAULT))
    p = cfg.listings_dir / name
    try:
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(default + "\n", encoding="utf-8")
        return p.read_text(encoding="utf-8-sig").strip()
    except OSError:
        return default


# ---- a single book as its own product (its set is sold per book) --------------------------------------------
def book_title_zh(b: dict, limit: int = 60) -> str:
    """The book's title. Shopee wants at least 10 characters: a short title gets its author, then （二手書）."""
    t = (b.get("title") or "").strip() or "二手書"
    if len(t) < 10 and (b.get("author") or "").strip():
        t += f"｜{b['author'].strip()}"
    if len(t) < 10:
        t += "（二手書）"
    if len(t) < 10:
        t += "二手書籍出售"
    return t[:limit]


def book_description_zh(b: dict, cfg: Config, price=None, footer: str = "", limit: int = 3000) -> str:
    """What buyers read for one book: its details (no numbering - it is the only book), its price, the photos."""
    lines = [b.get("title") or ""]
    for label, key in (("作者", "author"), ("出版社", "publisher"), ("出版年", "year"), ("頁數", "pages"),
                       ("分類", "genre"), ("ISBN", "isbn13")):
        if (b.get(key) or "").strip():
            lines.append(f"{label}：{b[key].strip()}")
    cond = COND_LABEL.get(b.get("condition"), b.get("condition") or "")
    if cond:
        lines.append(f"書況：{cond}")
    if price is not None:
        p = f"售價：{ntd(price)}"
        if cfg.listing_show_market and b.get("market_price") and (b.get("market_currency") or "") == "TWD":
            p += f"（新書定價 {ntd(b['market_price'])}）"
        lines.append(p)
    lines += ["", "照片 1：封面　照片 2：封底"]
    if footer:
        lines += ["──────────", footer]
    text = "\n".join(lines)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def book_problems(b: dict, cfg: Config) -> list:
    """Why this book can't go on sale on its own yet (empty list = it can)."""
    out = []
    if b.get("status") not in ("validated", "listed", "sold"):
        out.append("not checked yet (review window)")
    if b.get("price") and (b.get("currency") or "").upper() != "TWD":
        out.append(f"not priced in TWD ({b.get('currency') or 'no currency'}) - Shopee sells in NT$: change the "
                   "price in the review window")
    if not (b.get("front_photo") or b.get("set_photo")):
        out.append("no front photo")
    if not b.get("back_photo"):
        out.append("no back photo")
    return out


def set_description_zh(code: str, books: list, cfg: Config, price=None, total=None, footer: str = "",
                       limit: int = 3000, images: int = 9, discount: float = 0.0) -> str:
    """The text buyers read: every book by its number on the photo, with title, author, publisher, condition and price
    (and the price of a new copy, LISTING_SHOW_MARKET). Never longer than `limit` characters (Shopee: 3,000)."""
    per = -(-len(books) // max(1, images - 1)) if books else 1
    pics = (f"照片 2-{1 + -(-len(books) // per)}：每本書的正面與背面（左：正面，右：背面）" if books else "")
    head = [f"【{code}】二手書 {len(books)} 本一套出售", "照片 1：整套書的正面，紅色號碼對應下方書目", pics, ""]

    def block(b, cut):
        lines = [f"#{b['position']} {b.get('title', '')[:cut * 2]}"]
        if b.get("author"):
            lines.append(f"作者：{b['author'][:cut]}")
        if b.get("publisher"):
            lines.append(f"出版社：{b['publisher'][:cut]}")
        cond = COND_LABEL.get(b.get("condition"), b.get("condition") or "")
        if cond:
            lines.append(f"書況：{cond}")
        p = f"價格：{ntd(b['price'])}" if b.get("price") else ""
        if p and cfg.listing_show_market and b.get("market_price") and (b.get("market_currency") or "") == "TWD":
            p += f"（新書定價 {ntd(b['market_price'])}）"
        if p:
            lines.append(p)
        return "\n".join(lines)
    if price is not None:
        z = f"，整套 {zhe(discount)}" if zhe(discount) else ""
        money_lines = ["──────────", f"整套售價：{ntd(price)}"
                       + ("" if total is None else f"（= {len(books)} 本單價合計）" if round(total) == price
                          else f"（{len(books)} 本單價合計 {ntd(total)}{z}）")]
    else:
        money_lines = []
    for cut in (60, 30, 15, 8):
        text = "\n".join(head + ["\n\n".join(block(b, cut) for b in books), ""] + money_lines
                         + ([footer] if footer else []))
        if len(text) <= limit:
            return text
    return text[: limit - 1] + "…"


def _set_from_books(s: dict, books: list, cfg: Config) -> None:
    """Fill a product's automatic fields from its books - a set, or (kind "book") one book on its own. Fields you
    edited - listed in `custom` - are kept."""
    custom = set(filter(None, (s.get("custom") or "").split(","))) & set(AUTO_FIELDS)
    s["custom"] = ",".join(sorted(custom))         # (older files may still name weight_g: no longer used)
    code = s["product_code"]
    single = s.get("kind") == "book"
    usual = 0.0 if single else cfg.set_discount * 100     # the whole-set discount is for sets; a book: its own price
    if "discount" not in custom:
        s["discount"] = f"{usual:g}"
    try:
        discount = pct(s["discount"])
    except ValueError:
        discount = usual
    price, total = set_price_of(books, discount)
    if "price" in custom:                             # you typed the set's price: the discount follows from it
        try:
            price = max(1, int(round(float(s["price"]))))
            if total is not None:
                discount = max(0.0, discount_for(price, total))   # dearer than the books: no discount
                s["discount"] = f"{discount:g}"
            else:                                         # a book has no TWD price: no sum to take a discount off
                discount = 0.0
                s["discount"] = ""
        except ValueError:
            pass
    s["original_price"] = f"{total:g}" if total is not None else ""
    s["price"] = str(price) if price is not None else ""
    if single:
        auto = {"title": book_title_zh(books[0]),
                "description": book_description_zh(books[0], cfg, price, footer_text(cfg, "book"))}
    else:
        auto = {"title": set_title_zh(code, books),
                "description": set_description_zh(code, books, cfg, price, total, footer_text(cfg),
                                                  images=cfg.shopee_max_images, discount=discount)}
    for k, v in auto.items():
        if k not in custom:
            s[k] = v
    s["currency"] = "TWD"
    s["kind"] = "book" if single else "set"
    s["book_count"] = str(len(books))


def apply_status(s: dict, status: str, books: list) -> bool:
    """Set a product's status and make its books follow: listed/reserved -> books listed, sold -> books sold, back on the
    shelf (draft/ready/withdrawn) -> books validated again. Returns True if a book changed."""
    s["status"] = status
    want = {"listed": "listed", "reserved": "listed", "sold": "sold"}.get(status)
    changed = False
    for b in books:
        if want and b.get("status") != want:
            b["status"], changed = want, True
        elif not want and b.get("status") in ("listed", "sold"):
            b["status"], changed = "validated", True
    return changed


def _new_product(code: str, books: list, kind: str = "set") -> dict:
    """A product seen for the first time. Books you had already marked listed / sold by hand keep that."""
    sts = {b.get("status") for b in books}
    status = "sold" if sts == {"sold"} else "listed" if sts & {"listed", "sold"} else "draft"
    return {"product_code": code, "kind": kind, "status": status, "created_at": now_text(), "custom": ""}


def _products_of_set(code: str, group: list, mode: str) -> list:
    """(product code, kind, its books) for every product a set becomes: the set itself, or each of its books."""
    if mode == "book":
        return [(b["sku"], "book", [b]) for b in group]
    return [(code, "set", group)]


def refresh_products(cfg: Config) -> list:
    """Bring data/products.csv up to date with data/books.csv: a product for every set, its title / price / description
    worked out from its books (while it is draft or ready), and draft <-> ready from whether every book is
    checked. Returns the products."""
    store.migrate_old_files(cfg.data_dir)              # data/sets.csv of older versions -> data/products.csv
    with store.locked(cfg.csv_path), store.locked(cfg.products_path):
        books = store.read_rows(cfg.csv_path)
        sets = store.read_table(cfg.products_path)
        by_code = {s["product_code"]: s for s in sets}
        books_changed = False
        gone = set()
        for (batch, set_id), group in set_groups(books).items():
            code = code_for(batch, set_id)
            mode = sell_mode(group)
            wanted = _products_of_set(code, group, mode)
            # the products of the other way of selling this set: never on sale (switching is refused then) - dropped
            other = [code] if mode == "book" else [b["sku"] for b in group]
            for c in other:
                if c in by_code and by_code[c]["status"] not in ON_SALE:
                    gone.add(c)
            for pcode, kind, its_books in wanted:
                s = by_code.get(pcode)
                if s is None:
                    s = by_code[pcode] = _new_product(pcode, its_books, kind)
                    sets.append(s)
                before = dict(s)
                s["kind"] = kind
                if s["status"] in ("draft", "ready"):
                    _set_from_books(s, its_books, cfg)
                    problems = (book_problems(its_books[0], cfg) if kind == "book"
                                else set_problems(its_books, cfg))
                    s["errors"] = "; ".join(problems)
                    s["status"] = "draft" if problems else "ready"
                elif not s.get("title"):
                    _set_from_books(s, its_books, cfg)      # e.g. marked listed by hand before products existed
                for k in QUANTITY_FIELDS:                  # 1 unless you changed it
                    s[k] = s.get(k) or "1"
                books_changed |= apply_status(s, s["status"], its_books)
                if s != before:
                    s["updated_at"] = now_text()
        sets = [s for s in sets if s["product_code"] not in gone]
        sets.sort(key=lambda s: s["product_code"])
        store.write_table(cfg.products_path, sets, store.PRODUCT_COLUMNS)
        if books_changed:
            store.write_rows(cfg.csv_path, books)
        return sets


def books_of(cfg: Config, code: str, books: list = None) -> list:
    """The books of a product: all the books of a set, or the one book whose sku is the code."""
    books = store.read_rows(cfg.csv_path) if books is None else books
    return sorted((b for b in books if code_of(b) == code or b.get("sku") == code),
                  key=lambda b: int(b.get("position") or 0))


def set_sell_mode(cfg: Config, batch: str, set_id: str, mode: str) -> list:
    """Sell a set as ONE product ("set") or each of its books as its own product ("book"). Refused while a product of
    the current way is on Shopee / ordered / sold (take it off sale first). Returns the set's products now."""
    if mode not in ("set", "book"):
        raise ValueError("mode must be set or book")
    code = code_for(batch, set_id)
    with store.locked(cfg.csv_path), store.locked(cfg.products_path):
        books = store.read_rows(cfg.csv_path)
        group = [b for b in books if b.get("batch") == batch and b.get("set_id") == set_id]
        if not group:
            raise KeyError(f"set {code} not found")
        mine = {code} | {b["sku"] for b in group}
        if sell_mode(group) == mode:
            return [p for p in refresh_products(cfg) if p["product_code"] in mine]
        current = [code] if mode == "book" else [b["sku"] for b in group]
        busy = [p["product_code"] for p in store.read_table(cfg.products_path)
                if p["product_code"] in current and p["status"] in ON_SALE]
        if busy:
            what = "the set is" if mode == "book" else f"book(s) {', '.join(busy)} are"
            raise ValueError(f"Can't change how set {set_id} is sold: {what} listed, ordered or sold. Take it off "
                             "sale first (status Withdrawn in Sell products), then change it.")
        for b in group:
            b["sell_as"] = "" if mode == "set" else "book"
        store.write_rows(cfg.csv_path, books)
        return [p for p in refresh_products(cfg) if p["product_code"] in mine]


def change_product_status(cfg: Config, code: str, status: str, note: str = "") -> list:
    """You change a product's status by hand (product-status command / Sell window): e.g. sold at a market -> sold; you deleted
    the Shopee product -> ready (or withdrawn). Returns messages, e.g. listings on other platforms to take down."""
    if status not in MANUAL_STATUSES:
        raise ValueError(f"status must be one of: {', '.join(MANUAL_STATUSES)}")
    msgs = []
    with store.locked(cfg.csv_path), store.locked(cfg.products_path), store.locked(cfg.listings_path):
        books = store.read_rows(cfg.csv_path)
        sets = store.read_table(cfg.products_path)
        listings = store.read_table(cfg.listings_path)
        s = next((x for x in sets if x["product_code"] == code), None)
        if s is None:
            raise KeyError(f"product {code} not found - run python -m pipeline shopee (or status) first")
        group = books_of(cfg, code, books)
        problems = book_problems(group[0], cfg) if s.get("kind") == "book" and group else set_problems(group, cfg)
        if status == "ready" and problems:
            status = "draft"
        apply_status(s, status, group)
        s["updated_at"] = now_text()
        if note:
            s["notes"] = "; ".join(x for x in (s.get("notes"), note) if x)
        if status == "sold":
            s["sold_on"] = s.get("sold_on") or "manual"
            s["sold_at"] = s.get("sold_at") or now_text()
            s["sold_price"] = s.get("sold_price") or s.get("price", "")
        if status in ("ready", "draft", "withdrawn", "sold"):
            for li in listings:
                if li["product_code"] == code and li.get("status") in ACTIVE_LISTING:
                    if status == "sold":
                        msgs.append(f"{code} is sold: take it down on {li['platform']}"
                                    + (f" ({li['url']})" if li.get("url") else ""))
                    li["status"], li["delisted_at"] = "delisted", now_text()
        store.write_rows(cfg.csv_path, books)
        store.write_table(cfg.products_path, sets, store.PRODUCT_COLUMNS)
        store.write_table(cfg.listings_path, listings, store.LISTING_COLUMNS)
    return msgs


def save_product_fields(cfg: Config, code: str, fields: dict) -> dict:
    """You edited a product's title / discount or price / description / stock / minimum purchase quantity: saved, and
    the text fields remembered as yours (`custom`)
    so the automatic value no longer replaces it. An empty value goes back to automatic. Discount and set price go
    together: give the discount and the price follows (sum of the books' prices minus it), or give the price and the
    discount follows."""
    def change(sets):
        s = next((x for x in sets if x["product_code"] == code), None)
        if s is None:
            raise KeyError(f"product {code} not found")
        custom = set(filter(None, (s.get("custom") or "").split(",")))
        for k, v in fields.items():
            if k not in AUTO_FIELDS + QUANTITY_FIELDS + ("notes",):
                continue
            v = ("" if v is None else str(v)).strip()
            if k in QUANTITY_FIELDS:                          # a whole number from 1 to 999,999; empty = 1
                what = "Stock" if k == "stock" else "Minimum purchase quantity"
                try:
                    n = int(v.replace(",", "") or "1")
                except ValueError:
                    raise ValueError(f"{what} must be a whole number (1 to {MAX_QUANTITY:,})")
                if not 1 <= n <= MAX_QUANTITY:
                    raise ValueError(f"{what} must be from 1 to {MAX_QUANTITY:,}")
                s[k] = str(n)
                continue
            if k == "price" and v:
                v = v.replace(",", "").replace("NT$", "").replace("$", "")
                if float(v) <= 0:                             # ValueError -> shown to you, nothing saved
                    raise ValueError("The price must be more than 0")
            if k == "discount" and v:
                d = pct(v)                                    # ValueError -> shown to you, nothing saved
                if not 0 <= d < 100:
                    raise ValueError("The discount must be from 0 to 99 (%)")
                v = f"{d:g}"
            if (s.get(k) or "") == v and k not in ("discount", "price"):
                continue
            s[k] = v
            if k != "notes":
                (custom.add if v else custom.discard)(k)
            if k in ("discount", "price"):                # the one typed last decides; the other follows
                custom.discard("price" if k == "discount" else "discount")
        if int(s.get("min_purchase") or 1) > int(s.get("stock") or 1):
            raise ValueError("The minimum purchase quantity can't be more than the stock - buyers could not order it")
        s["custom"] = ",".join(sorted(custom))
        s["updated_at"] = now_text()
        return dict(s)
    store.update_table(cfg.products_path, store.PRODUCT_COLUMNS, change)
    return next(s for s in refresh_products(cfg) if s["product_code"] == code)


# ---- the photos of a set product (9 at most on Shopee) -------------------------------------------
def _square(img: Image.Image, size: int) -> Image.Image:
    img = img.copy()
    img.thumbnail((size, size))
    out = Image.new("RGB", (size, size), "white")
    out.paste(img, ((size - img.width) // 2, (size - img.height) // 2))
    return out


def _save_small(img: Image.Image, dst: Path, max_bytes: int = 1_900_000) -> None:
    """JPEG under Shopee's 2 MB limit per photo."""
    q = 90
    while True:
        img.save(dst, "JPEG", quality=q)
        if dst.stat().st_size <= max_bytes or q <= 50:
            return
        q -= 8


def make_set_images(code: str, books: list, cfg: Config, dst: Path, max_images: int = 9, size: int = 1500) -> list:
    """The set's photos, square (as shops show them), in dst/01.jpg, 02.jpg ...:
    01  the front photo with every book's number, and the set code;
    02+ each book: its front (cut out of the set photo) and its back side by side, with its number.
    With more books than photos allowed, several books share a photo. Returns the paths."""
    from PIL import ImageDraw
    from ..photos.decode import load_image
    from ..photos.grid import book_cover, draw_pair, font
    from ..photos.segment import load_segments
    dst.mkdir(parents=True, exist_ok=True)
    for old in dst.glob("*.jpg"):
        old.unlink()
    front_path = books[0].get("set_photo") or books[0].get("front_photo")
    front = load_image(front_path)
    segs = load_segments(Path(front_path).with_name("segments.json"), front.size)
    top = numbered(front, *cfg.grid, region=cfg.region, boxes=[sg.box for sg in segs] if segs else None)
    first = _square(top, size)
    d = ImageDraw.Draw(first)
    band = max(40, size // 22)
    d.rectangle([0, size - band, size, size], fill="white")
    d.text((size // 2, size - band // 2), f"套書編號 {code}", font=font(max(24, size // 30), cjk=True),
           fill=(60, 60, 60), anchor="mm")
    paths = [dst / "01.jpg"]
    _save_small(first, paths[0])
    per = -(-len(books) // max(1, max_images - 1))
    for i in range(0, len(books), per):
        group = books[i:i + per]
        p = dst / f"{len(paths) + 1:02d}.jpg"
        own = group[0].get("front_back_photo")
        if per == 1 and own and Path(own).exists():          # made with the cover cut-outs: the same picture
            _save_small(load_image(own), p)
            paths.append(p)
            continue
        canvas = Image.new("RGB", (size, size), "white")
        row_h = size // len(group)
        for j, b in enumerate(group):
            try:
                back = load_image(b["back_photo"])
            except Exception:
                back = None
            draw_pair(canvas, j * row_h, row_h, book_cover(b, front, cfg.grid, cfg.region), back, int(b["position"]))
        _save_small(canvas, p)
        paths.append(p)
    return paths


def make_book_images(book: dict, cfg: Config, dst: Path, size: int = 1500) -> list:
    """A single book's photos, square, in dst: 01.jpg its front (its own cover, cut out of the set photo - the cover
    image) and 02.jpg its back photo. Returns the paths."""
    from ..photos.decode import load_image
    from ..photos.grid import book_cover
    dst.mkdir(parents=True, exist_ok=True)
    for old in dst.glob("*.jpg"):
        old.unlink()
    set_photo = book.get("set_photo") or book.get("front_photo")
    own = book.get("front_photo")
    needs_set = not (own and own != set_photo and Path(own).exists())      # no cut-out: its cell of the set photo
    front = load_image(set_photo) if needs_set and set_photo else None
    paths = []
    for name, im in (("01.jpg", book_cover(book, front, cfg.grid, cfg.region)),
                     ("02.jpg", load_image(book["back_photo"]) if book.get("back_photo") else None)):
        if im is None:
            continue
        p = dst / name
        _save_small(_square(im.convert("RGB"), size), p)
        paths.append(p)
    return paths


def make_product_images(product: dict, books: list, cfg: Config, dst: Path) -> list:
    """The listing photos of a product: a set's numbered photos, or a single book's front + back."""
    if product.get("kind") == "book":
        return make_book_images(books[0], cfg, dst)
    return make_set_images(product["product_code"], books, cfg, dst, cfg.shopee_max_images)
