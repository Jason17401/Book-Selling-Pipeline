"""Per set: ONE photo of all the front covers, then ONE barcode photo (the back cover) per book.

    photo 1      : front covers of the whole set (GRID, e.g. 2x5)
    photos 2..11 : barcode photo of book 1, 2, ... 10 - top row left to right, then the next row left to right

Each book in books.csv keeps two photos: front_photo (the whole set, shared) and barcode_photo (its own back cover).

Sets are found by counting, in send order: 1 front + SET_SIZE barcode photos. A barcode that cannot be read is still
that book (marked needs_manual, you type its ISBN in), so one bad photo never shifts the books after it.

Captions (Telegram caption, or a .txt next to the photo with the same name) override the counting:
  front / set    this photo is the front photo of a new set (lets you shoot a short set, e.g. 7 books)
  back           this photo is a book's back, never a front
  retake / again this photo replaces the previous back photo (use it right after a bad shot)
  良好 150       condition and price (both optional; 'good 150 NZD' works too, default currency DEFAULT_CURRENCY);
                 on the front photo it applies to the whole set, on a barcode photo to that book
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional


from ..core import progress as live, store
from ..core.config import Config
from .decode import ADDONS as decode_addons, decode_isbn_image, load_image, rotate_cw
from .ingest import Summary, _move, _sidecar, list_photos, note_lookup_errors, parse_caption
from ..sources.lookup import lookup_book

FRONT_WORDS = {"front", "set", "cover", "covers"}
BACK_WORDS = {"back"}
RETAKE_WORDS = {"retake", "again", "redo"}


def caption_words(photo: Path) -> set:
    side = _sidecar(photo)
    if not side.exists():
        return set()
    return set(re.split(r"[\s,]+", side.read_text(encoding="utf-8", errors="ignore").lower()))


def photo_hint(photo: Path) -> str:
    w = caption_words(photo)
    if w & RETAKE_WORDS:
        return "retake"
    if w & FRONT_WORDS:
        return "front"
    if w & BACK_WORDS:
        return "back"
    return ""


@dataclass
class Book:
    isbn: str                     # "" when the barcode could not be read
    photos: List[Path] = field(default_factory=list)

    @property
    def barcode_photo(self) -> Path:
        return self.photos[-1]    # after a retake, the newest photo is the one that counts


@dataclass
class ShotSet:
    front: Optional[Path] = None
    books: List[Book] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)


# ---- splitting the photo stream into sets ----------------------------------------------------
def split_by_count(photos: list, set_size: int, decode: Callable[[Path], Optional[str]],
                   hint: Callable[[Path], str] = photo_hint,
                   quick_decode: Optional[Callable[[Path], Optional[str]]] = None) -> List[ShotSet]:
    """Walk the photos in order: 1 front, then `set_size` backs, repeat. Self-corrects for one lost back photo.

    quick_decode (a fast, low-effort read) is used where a FRONT photo is expected, just to check it has no barcode."""
    sets: List[ShotSet] = []
    full: dict = {}
    quick: dict = {}

    def isbn_of(p):
        if p not in full:
            full[p] = decode(p) or ""
        return full[p]

    def quick_isbn(p):
        if p in full:
            return full[p]
        if quick_decode is None:
            return isbn_of(p)
        if p not in quick:
            quick[p] = quick_decode(p) or ""
        return quick[p]

    i = 0
    while i < len(photos):
        p = photos[i]
        h = hint(p)
        cur = ShotSet()
        if h == "front":
            cur.front, i = p, i + 1
        elif h in ("back", "retake") or quick_isbn(p):
            prev = sets[-1] if sets else None
            if prev and prev.books and not prev.books[-1].isbn and hint(prev.books[-1].photos[-1]) not in ("back", "retake") \
                    and len(prev.books[-1].photos) == 1:
                # The previous set's last 'book' had no barcode, and the photo where this set's front should be HAS one:
                # almost certainly a back photo of the previous set went missing, and that last 'book' is really our front.
                moved = prev.books.pop()
                cur.front = moved.photos[0]
                prev.notes.append(f"only {len(prev.books)} back photos - one seems to be MISSING (lost upload?). "
                                  "Every book after the missing one is numbered one too low: check review.jpg")
            else:
                cur.notes.append(f"no front photo: photo {p.name} has a barcode where the set's front photo was expected")
        else:
            cur.front, i = p, i + 1
        while i < len(photos) and len(cur.books) < set_size:
            p = photos[i]
            h = hint(p)
            if h == "front":
                break
            if h == "retake" and cur.books:
                cur.books[-1].photos.append(p)
                cur.books[-1].isbn = isbn_of(p) or cur.books[-1].isbn
                i += 1
                continue
            isbn = isbn_of(p)
            if isbn and cur.books and cur.books[-1].isbn == isbn:   # same barcode twice in a row = a retake
                cur.books[-1].photos.append(p)
                i += 1
                continue
            cur.books.append(Book(isbn, [p]))
            i += 1
        # a retake of the LAST book (captioned, or the same barcode again) still belongs to this set
        while i < len(photos) and cur.books and (hint(photos[i]) == "retake" or
                                                (hint(photos[i]) != "front" and cur.books[-1].isbn and
                                                 quick_isbn(photos[i]) == cur.books[-1].isbn)):
            cur.books[-1].photos.append(photos[i])
            cur.books[-1].isbn = isbn_of(photos[i]) or cur.books[-1].isbn
            i += 1
        sets.append(cur)
    for k, s in enumerate(sets):
        if len(s.books) < set_size and not any("MISSING" in n for n in s.notes):
            last = k == len(sets) - 1
            s.notes.append(f"{len(s.books)} back photos, expected {set_size}" +
                           (" (last set - fine if it is a short set)" if last else ""))
    return sets


# ---- storing ------------------------------------------------------------------------------------
def _place(src: Path, dest_dir: Path, rotate: int, name: Optional[str] = None) -> Path:
    """Move a photo into dest_dir. If it needs turning, save an upright copy and keep the untouched original in original/."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    if rotate % 360 == 0:
        moved = _move(src, dest_dir)
        if name:
            target = dest_dir / f"{name}{moved.suffix.lower()}"
            if not target.exists():
                moved.rename(target)
                return target
        return moved
    img = rotate_cw(load_image(src), rotate)
    dst = dest_dir / f"{name or src.stem}.jpg"
    img.save(dst, quality=95)
    _move(src, dest_dir / "original")
    return dst


def process_sets(cfg: Config, decode: Callable = None, lookup: Callable = lookup_book,
                 expect: Optional[int] = None) -> Summary:
    from .grid import make_review_sheet, orientation_warning
    from ..sources.market import apply_market
    from ..core.validate import refresh_status
    cfg.ensure_dirs()
    s = Summary(batch=time.strftime("%Y%m%d-%H%M%S"))
    photos = list_photos(cfg.inbox, cfg.settle_seconds)
    if not photos:
        s.warnings.append("Inbox is empty (or files are still syncing).")
        return s
    from ..sources.market import describe as describe_market
    from ..sources.lookup import providers_for
    live.emit("note", text=describe_market(cfg))

    quick = None
    if decode is None:
        def _reader(effort):
            def read(p):
                try:
                    img = load_image(p)
                except Exception:
                    return None
                return decode_isbn_image(img, effort=effort)
            return read
        decode, quick = _reader(None), _reader("fast")

    names = {p: i for i, p in enumerate(photos, 1)}

    def watched(read, how):
        def run(p):
            live.step("barcodes", f"reading{how} barcode photo {names.get(p, '?')}/{len(photos)}  ({p.name})")
            return read(p)
        return run
    decode = watched(decode, "")
    if quick is not None:
        quick = watched(quick, " (quick)")
    sets = split_by_count(photos, cfg.set_size, decode, quick_decode=quick)

    rows_n, cols_n = cfg.grid
    if rows_n * cols_n != cfg.set_size:
        s.warnings.append(f"GRID={rows_n}x{cols_n} is {rows_n * cols_n} books but SET_SIZE={cfg.set_size}: "
                          "the numbers drawn on the front photo won't match. Make them match in .env.")
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%S")
    total = sum(len(ss.books) for ss in sets)
    read_ok = sum(1 for ss in sets for b in ss.books if b.isbn)
    live.result("barcodes", read_ok == total, f"{read_ok} of {total} barcode(s) read from {len(photos)} photo(s)")
    live.emit("start", total=total, sets=len(sets))
    n = 0
    for si, ss in enumerate(sets, 1):
        set_id = f"S{si:02d}"
        s.warnings += [f"{set_id}: {n}" for n in ss.notes]
        set_dir = cfg.processed / s.batch / set_id
        cond, price, currency = "", "", ""
        front_path, front_img = None, None
        if ss.front is not None:
            side = _sidecar(ss.front)
            if side.exists():
                cond, price, currency = parse_caption(side.read_text(encoding="utf-8", errors="ignore"),
                                                      cfg.default_currency)
            front_path = _place(ss.front, set_dir, cfg.rotate, name="front")
            try:
                front_img = load_image(front_path)
            except Exception:
                front_img = None
            if front_img is not None:
                w = orientation_warning(front_img, rows_n, cols_n, "Front", cfg.region)
                if w:
                    s.warnings.append(f"{set_id}: {w}")
        unread = 0
        set_rows = []
        for pos, book in enumerate(ss.books, 1):
            book_dir = set_dir / f"{pos:02d}"
            sidecars = [_sidecar(p) for p in book.photos]
            placed = [_place(p, book_dir, 0) for p in book.photos]     # barcode photos are kept as sent
            b_cond, b_price, b_cur = cond, price, currency
            for side in sidecars:
                moved_side = book_dir / side.name
                if moved_side.exists():
                    c2, p2, cur2 = parse_caption(moved_side.read_text(encoding="utf-8", errors="ignore"),
                                                 cfg.default_currency)
                    b_cond, b_price, b_cur = c2 or b_cond, p2 or b_price, cur2 or b_cur
            n += 1
            live.emit("book", n=n, total=total, set_id=set_id, position=pos, isbn=book.isbn)
            if book.isbn:
                live.step("book", f"looking up {book.isbn} ({' -> '.join(providers_for(book.isbn, cfg))})")
                meta = lookup(book.isbn, cfg)
                if meta.get("title"):
                    who = " - ".join(x for x in (meta.get("author"), meta.get("publisher"), meta.get("year")) if x)
                    live.result("book", True, f"{meta.get('source') or 'found'}: {meta['title']}"
                                    + (f"  ({who})" if who else ""))
                else:
                    tried = ", ".join(meta.get("_errors") or []) or "no source knows this ISBN"
                    live.result("book", False, f"not found ({tried})")
            else:
                meta = {}
                live.result("book", False, "barcode not read - type the ISBN in the review window")
            note_lookup_errors(s, meta)
            unread += not book.isbn
            row = {
                "sku": f"{s.batch}-{set_id}-{pos:02d}", "batch": s.batch, "set_id": set_id, "position": pos,
                "isbn13": book.isbn,
                "barcode_addon": decode_addons.get(book.isbn, "") if book.isbn else "",
                **{k: meta.get(k, "") for k in ("title", "author", "publisher", "year", "pages", "genre")},
                "condition": b_cond or cfg.default_condition, "price": b_price, "currency": b_cur, "price_basis": "caption" if b_price else "",
                "front_photo": str(front_path) if front_path else "",
                "barcode_photo": str(placed[-1]),          # after a retake, the newest photo is the one that counts
                "source": meta.get("source", ""), "created_at": now_iso,
            }
            if book.isbn and cfg.market_providers:
                live.step("market", "searching the shops")
                for problem in apply_market(row, cfg):
                    note_lookup_errors(s, {"_errors": [f"market price - {problem}"]})
                if row.get("market_price"):
                    mine = f" -> your price {row['price']} {row.get('currency', '')}" if row.get("price") else ""
                    live.result("market", True, f"{row['market_price']} {row.get('market_currency', '')} new "
                                    f"({row.get('market_source', '')}){mine}")
                else:
                    live.result("market", False, "no market price found (fill it in the review window)")
            refresh_status(row, cfg)
            set_rows.append(row)
            live.emit("book_done", n=n, total=total, set_id=set_id, position=pos, isbn=book.isbn,
                          title=row.get("title", ""), status=row.get("status", ""), errors=row.get("errors", ""),
                          reason=("barcode not read" if not book.isbn else
                                  "no book source found this ISBN" if not meta.get("title") else ""))
        if unread:
            s.warnings.append(f"{set_id}: {unread} barcode(s) unreadable -> needs_manual. Fix them in the review window "
                              "(review.bat): type the ISBN from the photo and press Look up.")
        dup = {}
        for r in set_rows:
            if r["isbn13"]:
                dup.setdefault(r["isbn13"], []).append(r["position"])
        for isbn, ps in dup.items():
            if len(ps) > 1:
                s.warnings.append(f"{set_id}: ISBN {isbn} at positions {ps} - second copy, or a retake not captioned 'retake'?")
        s.rows += set_rows
        if set_rows:
            sheet = set_dir / "review.jpg"
            make_review_sheet(front_img, set_rows, cfg.grid, cfg.region, sheet)
            s.review_sheets.append(str(sheet))
    if expect is not None and len(s.rows) != expect:
        s.warnings.append(f"Expected {expect} books but made {len(s.rows)} rows.")
    store.append_rows(cfg.csv_path, s.rows)
    live.emit("finish", books=len(s.rows))
    return s


# ---- live progress for the Telegram bot (counts only, no barcode reading) -----------------------
def progress(photos: list, set_size: int, hint: Callable[[Path], str] = photo_hint) -> List[tuple]:
    """For each photo in order: (set_number, slot) where slot 0 = front photo, 1..set_size = back of book N,
    and slot -1 = a retake of the previous back. Mirrors split_by_count when every photo arrives."""
    out = []
    set_no, slot = 0, set_size  # 'previous set full' so the first photo starts set 1
    for p in photos:
        h = hint(p)
        if h == "retake" and slot >= 1:
            out.append((set_no, -1))
            continue
        if h == "front" or (slot >= set_size and h != "back"):
            set_no, slot = set_no + 1, 0
        elif slot >= set_size:  # captioned 'back' but the set is full: a set without a front photo
            set_no, slot = set_no + 1, 1
        else:
            slot += 1
        out.append((set_no, slot))
    return out


def describe_progress(photos: list, set_size: int) -> str:
    pr = progress(photos, set_size)
    if not pr:
        return "Inbox is empty. Send the FRONT photo of the first set."
    set_no, slot = pr[-1]
    if slot == -1:
        msg = f"Set {set_no}: retake saved (replaces the previous barcode photo)."
    elif slot == 0:
        msg = f"Set {set_no}: FRONT photo saved. Next: barcode photo of book 1."
    else:
        msg = f"Set {set_no}: barcode {slot}/{set_size} saved."
        msg += " Set complete - next photo starts a new set (front)." if slot == set_size else f" Next: barcode photo of book {slot + 1}."
    return msg + f"  [{len(photos)} photo(s) waiting]"
