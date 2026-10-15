"""One marketplace post per set: the numbered front photo (+ optionally each back photo) and a text listing every book
by its number. Made only for sets where every book is 'validated'."""
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
# Sets as things you sell: data/sets.csv (one row per set, the same on every platform).
# A set is the books with the same batch + set_id; its code is "<batch>-<set_id>" (e.g. 20261008-153012-S01), which is
# also how every book's sku starts (20261008-153012-S01-03 = book 3 of that set). The code goes into the Shopee title,
# the parent SKU (主商品貨號) and the first photo, so a Shopee order can always be traced back to its books.
# =================================================================================================
SET_STATUSES = ("draft", "ready", "listed", "reserved", "sold", "withdrawn")
SET_STATUS_HELP = {
    "draft": "not ready: a book is not checked yet, or something else is missing (see errors)",
    "ready": "every book checked - the next `python -m pipeline shopee` puts it in the upload file",
    "listed": "in a Shopee upload file / on Shopee (or another platform)",
    "reserved": "someone ordered it; the order is not finished yet",
    "sold": "sold (order completed)",
    "withdrawn": "you took it off sale",
}
MANUAL_STATUSES = ("ready", "listed", "sold", "withdrawn")      # what you may set by hand (set-status / set view)
ACTIVE_LISTING = ("exported", "live")                          # a listing that is (about to be) on sale
AUTO_FIELDS = ("set_title", "discount", "set_price", "description", "weight_g")
# discount and set_price go together: type one, the other follows (the one you typed last is kept in `custom`)
FOOTER_DEFAULT = ("・整套出售，恕不拆賣。\n"
                  "・皆為二手書，書況如各書所註，以照片為準。\n"
                  "・照片皆為實物拍攝。")


def set_code(batch: str, set_id: str) -> str:
    return f"{batch}-{set_id}"


def code_of(book: dict) -> str:
    return set_code(book.get("batch", ""), book.get("set_id", "")) if book.get("set_id") else ""


def now_text() -> str:
    import time
    return time.strftime("%Y-%m-%d %H:%M:%S")


def short_title(title: str) -> str:
    """The main title without its subtitle: 'Google必修的圖表簡報術：Google總監首度公開...' -> 'Google必修的圖表簡報術'."""
    import re
    t = re.split(r"[：:（(／/]| - ", title or "", maxsplit=1)[0].strip()
    return t or (title or "").strip()


def set_title_zh(code: str, books: list, limit: int = 60) -> str:
    """【code】title1、title2 等｜二手書8本合售 - as many titles as fit in `limit` characters."""
    head, tail = f"【{code}】", f"｜二手書{len(books)}本合售"
    names = [short_title(b.get("title", "")) for b in books if b.get("title")]
    room = limit - len(head) - len(tail)
    shown = ""
    for i, n in enumerate(names):
        more = "、".join(names[: i + 1])
        rest = "等" if i + 1 < len(names) else ""
        if len(more + rest) > room:
            break
        shown = more + rest
    if not shown and names:                      # not even the first title fits: cut it
        shown = names[0][: max(1, room - 1)] + ("等" if len(names) > 1 else "")
    return (head + shown + tail)[:limit]


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


def footer_text(cfg: Config) -> str:
    """The lines at the end of every set's description: data/listings/description_footer.txt (made on first use, edit
    it freely)."""
    p = cfg.listings_dir / "description_footer.txt"
    try:
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(FOOTER_DEFAULT + "\n", encoding="utf-8")
        return p.read_text(encoding="utf-8-sig").strip()
    except OSError:
        return FOOTER_DEFAULT


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
    """Fill a set's automatic fields from its books (fields you edited - listed in `custom` - are kept)."""
    custom = set(filter(None, (s.get("custom") or "").split(",")))
    code = s["set_code"]
    if "discount" not in custom:
        s["discount"] = f"{cfg.set_discount * 100:g}"
    try:
        discount = pct(s["discount"])
    except ValueError:
        discount = cfg.set_discount * 100
    price, total = set_price_of(books, discount)
    if "set_price" in custom:                             # you typed the set's price: the discount follows from it
        try:
            price = max(1, int(round(float(s["set_price"]))))
            if total is not None:
                discount = max(0.0, discount_for(price, total))   # dearer than the books: no discount
                s["discount"] = f"{discount:g}"
            else:                                         # a book has no TWD price: no sum to take a discount off
                discount = 0.0
                s["discount"] = ""
        except ValueError:
            pass
    s["original_price"] = f"{total:g}" if total is not None else ""
    s["set_price"] = str(price) if price is not None else ""
    auto = {"set_title": set_title_zh(code, books),
            "description": set_description_zh(code, books, cfg, price, total, footer_text(cfg),
                                              images=cfg.shopee_max_images, discount=discount),
            "weight_g": str(cfg.set_weight_per_book_g * len(books))}
    for k, v in auto.items():
        if k not in custom:
            s[k] = v
    s["currency"] = "TWD"
    s["book_count"] = str(len(books))


def apply_set_status(s: dict, status: str, books: list) -> bool:
    """Set a set's status and make its books follow: listed/reserved -> books listed, sold -> books sold, back on the
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


def _new_set(code: str, books: list) -> dict:
    """A set seen for the first time. Books you had already marked listed / sold by hand keep that."""
    sts = {b.get("status") for b in books}
    status = "sold" if sts == {"sold"} else "listed" if sts & {"listed", "sold"} else "draft"
    return {"set_code": code, "status": status, "created_at": now_text(), "custom": ""}


def refresh_sets(cfg: Config) -> list:
    """Bring data/sets.csv up to date with data/books.csv: a row for every set, its title / price / description /
    weight worked out from its books (while it is draft or ready), and draft <-> ready from whether every book is
    checked. Returns the sets."""
    with store.locked(cfg.csv_path), store.locked(cfg.sets_path):
        books = store.read_rows(cfg.csv_path)
        sets = store.read_table(cfg.sets_path)
        by_code = {s["set_code"]: s for s in sets}
        books_changed = False
        for (batch, set_id), group in set_groups(books).items():
            code = set_code(batch, set_id)
            s = by_code.get(code)
            if s is None:
                s = by_code[code] = _new_set(code, group)
                sets.append(s)
            before = dict(s)
            if s["status"] in ("draft", "ready"):
                _set_from_books(s, group, cfg)
                problems = set_problems(group, cfg)
                s["errors"] = "; ".join(problems)
                s["status"] = "draft" if problems else "ready"
            elif not s.get("set_title"):
                _set_from_books(s, group, cfg)          # e.g. marked listed by hand before sets existed
            books_changed |= apply_set_status(s, s["status"], group)
            if s != before:
                s["updated_at"] = now_text()
        sets.sort(key=lambda s: s["set_code"])
        store.write_table(cfg.sets_path, sets, store.SET_COLUMNS)
        if books_changed:
            store.write_rows(cfg.csv_path, books)
        return sets


def books_of(cfg: Config, code: str, books: list = None) -> list:
    books = store.read_rows(cfg.csv_path) if books is None else books
    return sorted((b for b in books if code_of(b) == code), key=lambda b: int(b.get("position") or 0))


def change_set_status(cfg: Config, code: str, status: str, note: str = "") -> list:
    """You change a set's status by hand (set-status command / set view): e.g. sold at a market -> sold; you deleted
    the Shopee product -> ready (or withdrawn). Returns messages, e.g. listings on other platforms to take down."""
    if status not in MANUAL_STATUSES:
        raise ValueError(f"status must be one of: {', '.join(MANUAL_STATUSES)}")
    msgs = []
    with store.locked(cfg.csv_path), store.locked(cfg.sets_path), store.locked(cfg.listings_path):
        books = store.read_rows(cfg.csv_path)
        sets = store.read_table(cfg.sets_path)
        listings = store.read_table(cfg.listings_path)
        s = next((x for x in sets if x["set_code"] == code), None)
        if s is None:
            raise KeyError(f"set {code} not found - run python -m pipeline shopee (or status) first")
        group = books_of(cfg, code, books)
        if status == "ready" and set_problems(group, cfg):
            status = "draft"
        apply_set_status(s, status, group)
        s["updated_at"] = now_text()
        if note:
            s["notes"] = "; ".join(x for x in (s.get("notes"), note) if x)
        if status == "sold":
            s["sold_on"] = s.get("sold_on") or "manual"
            s["sold_at"] = s.get("sold_at") or now_text()
            s["sold_price"] = s.get("sold_price") or s.get("set_price", "")
        if status in ("ready", "draft", "withdrawn", "sold"):
            for li in listings:
                if li["set_code"] == code and li.get("status") in ACTIVE_LISTING:
                    if status == "sold":
                        msgs.append(f"{code} is sold: take it down on {li['platform']}"
                                    + (f" ({li['url']})" if li.get("url") else ""))
                    li["status"], li["delisted_at"] = "delisted", now_text()
        store.write_rows(cfg.csv_path, books)
        store.write_table(cfg.sets_path, sets, store.SET_COLUMNS)
        store.write_table(cfg.listings_path, listings, store.LISTING_COLUMNS)
    return msgs


def save_set_fields(cfg: Config, code: str, fields: dict) -> dict:
    """You edited a set's title / discount or price / description / weight: saved, and remembered as yours (`custom`)
    so the automatic value no longer replaces it. An empty value goes back to automatic. Discount and set price go
    together: give the discount and the price follows (sum of the books' prices minus it), or give the price and the
    discount follows."""
    def change(sets):
        s = next((x for x in sets if x["set_code"] == code), None)
        if s is None:
            raise KeyError(f"set {code} not found")
        custom = set(filter(None, (s.get("custom") or "").split(",")))
        for k, v in fields.items():
            if k not in AUTO_FIELDS + ("notes",):
                continue
            v = ("" if v is None else str(v)).strip()
            if k in ("weight_g", "set_price") and v:
                v = v.replace(",", "").replace("NT$", "").replace("$", "")
                if float(v) <= 0:                             # ValueError -> shown to you, nothing saved
                    raise ValueError("The price must be more than 0")
            if k == "discount" and v:
                d = pct(v)                                    # ValueError -> shown to you, nothing saved
                if not 0 <= d < 100:
                    raise ValueError("The discount must be from 0 to 99 (%)")
                v = f"{d:g}"
            if (s.get(k) or "") == v and k not in ("discount", "set_price"):
                continue
            s[k] = v
            if k != "notes":
                (custom.add if v else custom.discard)(k)
            if k in ("discount", "set_price"):                # the one typed last decides; the other follows
                custom.discard("set_price" if k == "discount" else "discount")
        s["custom"] = ",".join(sorted(custom))
        s["updated_at"] = now_text()
        return dict(s)
    store.update_table(cfg.sets_path, store.SET_COLUMNS, change)
    return next(s for s in refresh_sets(cfg) if s["set_code"] == code)


# ---- the photos of a set listing (9 at most on Shopee) -------------------------------------------
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
