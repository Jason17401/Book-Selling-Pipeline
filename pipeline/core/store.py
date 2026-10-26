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
    "sku",
    "batch",
    "set_id",
    "position",
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
    "price_basis",
    "market_price",
    "market_currency",
    "market_source",
    "market_url",
    "market_match",
    "market_isbn",
    "front_photo",
    "set_photo",
    "back_photo",
    "front_back_photo",
    "status",
    "errors",
    "book_source",
    "sell_as",
    "created_at",
    "notes",
]
# sell_as (the same for every book of a set): "" = the set is sold as ONE product; "book" = each book is its own product
# status: needs_manual -> enriched -> to_check -> validated -> listed -> sold   (meanings: pipeline/core/validate.py)
# A book belongs to the set batch + set_id; that set's code is "<batch>-<set_id>" - the start of the book's sku.

# data/products.csv - one row per PRODUCT: what you sell as one thing, the same whatever platform it is sold on
# (pipeline/apps/products.py). kind = "set" (the books of one set photo, sold together; code "<batch>-<set_id>") or
# "book" (one book on its own; code = its sku - for later). Each platform's copy of it is a row in listings.csv.
PRODUCT_COLUMNS = [
    "product_code",
    "kind",
    "book_count",
    "title",
    "original_price",
    "discount",
    "price",
    "currency",
    "stock",
    "min_purchase",
    "description",
    "images",
    "status",
    "errors",
    "sold_on",
    "sold_at",
    "sold_price",
    "custom",
    "created_at",
    "updated_at",
    "notes",
]
# data/listings.csv - one row per product per platform (pipeline/apps/shopee.py). Columns in groups: every platform,
# then
# one group per platform (a Facebook Marketplace group can be added later the same way: fb_...).
LISTING_COMMON = [
    "product_code",
    "platform",
    "status",
    "title",
    "price",
    "currency",
    "url",
    "export_file",
    "exported_at",
    "listed_at",
    "updated_at",
    "delisted_at",
    "last_synced_at",
    "errors",
]
LISTING_SHOPEE = [
    "shopee_item_id",
    "shopee_sku",
    "shopee_category_id",
    "shopee_condition",
    "shopee_logistics",
    "shopee_days_to_ship",
    "shopee_order_sn",
    "shopee_order_status",
    "shopee_tracking_no",
    "shopee_fees",
    "shopee_payout",
]
LISTING_COLUMNS = LISTING_COMMON + LISTING_SHOPEE


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
                    raise FileBusy(
                        f"{Path(path).name} is busy (another pipeline program is writing it). Try again."
                    ) from None
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
    """Rows written by an older version: the condition codes like_new / acceptable are as_new / fair now (TAAZE's
    近全新 / 普通)."""
    if r.get("condition") in ("like_new", "acceptable"):
        r["condition"] = {"like_new": "as_new", "acceptable": "fair"}[r["condition"]]
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
        for _ in range(20):  # about 5 seconds
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
