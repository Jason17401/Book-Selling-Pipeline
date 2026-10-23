from __future__ import annotations

import csv
import os
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable

COLUMNS = [
    "sku", "batch", "set_id", "position", "isbn13", "title", "author", "publisher", "year", "pages", "genre",
    "condition", "price", "currency", "price_basis",
    "market_price", "market_currency", "market_source", "market_url", "market_match", "market_isbn", "front_photo", "set_photo",
    "back_photo", "front_back_photo", "status", "errors", "book_source", "sell_as", "created_at", "notes",
]
# sell_as (the same for every book of a set): "" = the set is sold as ONE product; "book" = each book is its own product
# status: needs_manual -> enriched -> to_check -> validated -> listed -> sold   (meanings: pipeline/core/validate.py)
# A book belongs to the set batch + set_id; that set's code is "<batch>-<set_id>" - the start of the book's sku.

# data/products.csv - one row per PRODUCT: what you sell as one thing, the same whatever platform it is sold on
# (pipeline/apps/products.py). kind = "set" (the books of one set photo, sold together; code "<batch>-<set_id>") or
# "book" (one book on its own; code = its sku - for later). Each platform's copy of it is a row in listings.csv.
PRODUCT_COLUMNS = [
    "product_code", "kind", "book_count", "title", "original_price", "discount", "price", "currency", "stock",
    "min_purchase",
    "description", "images", "status", "errors", "sold_on", "sold_at", "sold_price", "custom", "created_at",
    "updated_at", "notes",
]
# data/listings.csv - one row per product per platform (pipeline/apps/shopee.py). Columns in groups: every platform, then
# one group per platform (a Facebook Marketplace group can be added later the same way: fb_...).
LISTING_COMMON = [
    "product_code", "platform", "status", "title", "price", "currency", "url", "export_file", "exported_at", "listed_at",
    "updated_at", "delisted_at", "last_synced_at", "errors",
]
LISTING_SHOPEE = [
    "shopee_item_id", "shopee_sku", "shopee_category_id", "shopee_condition", "shopee_logistics",
    "shopee_days_to_ship", "shopee_order_sn", "shopee_order_status", "shopee_tracking_no", "shopee_fees",
    "shopee_payout",
]
LISTING_COLUMNS = LISTING_COMMON + LISTING_SHOPEE


def migrate_old_files(data_dir) -> list:
    """Files written by older versions, brought up to date (once). Returns what was done, to show.
    - data/sets.csv -> data/products.csv (set_code -> product_code, set_title -> title, set_price -> price, kind = set)
    - data/listings.csv: set_code -> product_code
    - data/listings/shopee/template.xlsx -> data/listings/shopee/template/ (the template folder)"""
    data_dir = Path(data_dir)
    done = []
    old, new = data_dir / "sets.csv", data_dir / "products.csv"
    if old.exists() and not new.exists():
        rename = {"set_code": "product_code", "set_title": "title", "set_price": "price"}
        with locked(old):
            rows = []
            for r in read_table(old):
                r = {rename.get(k, k): v for k, v in r.items()}
                r["kind"] = r.get("kind") or "set"
                r["custom"] = ",".join(sorted(rename.get(c, c) for c in (r.get("custom") or "").split(",")
                                              if c and c != "weight_g"))
                rows.append(r)
            write_table(new, rows, PRODUCT_COLUMNS)
            os.replace(old, data_dir / "sets_old.csv")
        done.append(f"{old.name} is now {new.name} (the old file is kept as sets_old.csv)")
    listings = data_dir / "listings.csv"
    if listings.exists():
        with locked(listings):
            with open(listings, newline="", encoding="utf-8-sig") as f:
                header = next(csv.reader(f), [])
            if "set_code" in header and "product_code" not in header:
                rows = [{("product_code" if k == "set_code" else k): v for k, v in r.items()}
                        for r in read_table(listings)]
                write_table(listings, rows, LISTING_COLUMNS)
                done.append("listings.csv: set_code is now product_code")
    shopee = data_dir / "listings" / "shopee"
    tpl = shopee / "template.xlsx"
    if tpl.is_file():
        (shopee / "template").mkdir(parents=True, exist_ok=True)
        dst = shopee / "template" / "template.xlsx"
        if not dst.exists():
            os.replace(tpl, dst)
            done.append(f"the Shopee template moved into {dst.parent}")
    return done


class FileBusy(RuntimeError):
    """books.csv is locked by another pipeline program for too long, or open in a program like Excel."""


_LOCAL = threading.local()


@contextmanager
def locked(path, timeout: float = 30.0):
    """Only one pipeline program at a time may read-modify-write books.csv (the bot, the review window, a command).
    Uses a small 'books.csv.lock' file next to it. Re-entrant within one thread."""
    key = str(Path(path).resolve())
    held = getattr(_LOCAL, "held", None)
    if held is None:
        held = _LOCAL.held = {}
    if held.get(key):
        held[key] += 1
        try:
            yield
        finally:
            held[key] -= 1
        return
    lock_path = Path(path).with_name(Path(path).name + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    f = open(lock_path, "a+b")
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                if os.name == "nt":
                    import msvcrt
                    f.seek(0)
                    msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise FileBusy(f"{Path(path).name} is busy (another pipeline program is writing it). Try again.")
                time.sleep(0.05)
        held[key] = 1
        try:
            yield
        finally:
            held[key] = 0
            if os.name == "nt":
                import msvcrt
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    finally:
        f.close()


def read_table(path, upgrade=None) -> list:
    """All rows of a CSV file as dicts ([] if it does not exist yet)."""
    path = Path(path)
    with locked(path):
        if not path.exists():
            return []
        with open(path, newline="", encoding="utf-8-sig") as f:
            return [upgrade(dict(r)) if upgrade else dict(r) for r in csv.DictReader(f)]


def read_rows(path) -> list:
    """The books (data/books.csv)."""
    return read_table(path, _upgrade)


def _upgrade(r: dict) -> dict:
    """Rows written by older versions: price_nzd -> price + currency NZD. Dropped columns vanish on the next save."""
    if r.get("price_nzd") and not r.get("price"):
        r["price"], r["currency"] = r["price_nzd"], r.get("currency") or "NZD"
    if not r.get("back_photo"):                        # renamed: barcode_photo (and back_crop before it)
        r["back_photo"] = r.pop("barcode_photo", "") or r.get("back_crop", "") or ""
    if not r.get("set_photo") and r.get("front_photo"):   # older rows: front_photo WAS the whole-set photo
        r["set_photo"] = r["front_photo"]
    old = {"acceptable": "fair", "like_new": "as_new"}      # renamed: TAAZE's grades 普通 = fair, 近全新 = as_new
    if r.get("condition") in old:
        r["condition"] = old[r["condition"]]
    if "source" in r and not r.get("book_source"):   # renamed: where the book's details came from
        r["book_source"] = r.pop("source") or ""
    if (r.get("market_source") or "").startswith("barcode"):   # priced from the book itself: no ISBN to note
        r["market_isbn"] = ""
    return r


def write_table(path, rows: list, columns: list) -> None:
    """Write all rows (only `columns`, in that order). Atomic: a crash never leaves a half-written file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with locked(path):
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        with os.fdopen(fd, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore", restval="")
            w.writeheader()
            for r in rows:
                w.writerow({c: ("" if r.get(c) is None else r.get(c)) for c in columns})
        for attempt in range(20):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:  # Windows: someone (Excel, a virus scanner) has the file open right now
                time.sleep(0.25)
        os.remove(tmp)
        raise FileBusy(f"Could not save {path.name}: it is open in another program (Excel?). Close it and try again.")


def write_rows(path, rows: list) -> None:
    write_table(path, rows, COLUMNS)


def update_table(path, columns: list, change: Callable[[list], object], upgrade=None):
    """Like update_rows, for any of the CSV files (products.csv, listings.csv)."""
    with locked(path):
        rows = read_table(path, upgrade)
        result = change(rows)
        write_table(path, rows, columns)
        return result


def update_rows(path, change: Callable[[list], object]):
    """Read all rows, let `change(rows)` modify them in place, write them back - all while holding the lock,
    so two programs can never overwrite each other's work. Returns whatever change() returns."""
    with locked(path):
        rows = read_rows(path)
        result = change(rows)
        write_rows(path, rows)
        return result


def append_rows(path, rows: list) -> None:
    update_rows(path, lambda existing: existing.extend(rows))
