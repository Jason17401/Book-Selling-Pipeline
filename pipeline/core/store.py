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
    "market_price", "market_currency", "market_source", "market_url", "market_match", "market_isbn", "barcode_addon", "front_photo", "barcode_photo",
    "status", "errors", "source", "created_at", "trademe_id", "ebay_id", "fb_status", "notes",
]
# status: needs_manual -> enriched -> validated -> listed -> sold   (meanings: pipeline/core/validate.py and README)


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


def read_rows(path) -> list:
    path = Path(path)
    with locked(path):
        if not path.exists():
            return []
        with open(path, newline="", encoding="utf-8-sig") as f:
            return [_upgrade(dict(r)) for r in csv.DictReader(f)]


def _upgrade(r: dict) -> dict:
    """Rows written by older versions: price_nzd -> price + currency NZD. Dropped columns vanish on the next save."""
    if r.get("price_nzd") and not r.get("price"):
        r["price"], r["currency"] = r["price_nzd"], r.get("currency") or "NZD"
    if not r.get("barcode_photo") and r.get("back_crop"):
        r["barcode_photo"] = r["back_crop"]
    if r.get("condition") == "acceptable":       # renamed to the Taiwanese grade 普通 = fair
        r["condition"] = "fair"
    return r


def write_rows(path, rows: list) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with locked(path):
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        with os.fdopen(fd, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore", restval="")
            w.writeheader()
            for r in rows:
                w.writerow({c: ("" if r.get(c) is None else r.get(c)) for c in COLUMNS})
        for attempt in range(20):  # atomic: a crash never leaves a half-written CSV
            try:
                os.replace(tmp, path)
                return
            except PermissionError:  # Windows: someone (Excel, a virus scanner) has the file open right now
                time.sleep(0.25)
        os.remove(tmp)
        raise FileBusy(f"Could not save {path.name}: it is open in another program (Excel?). Close it and try again.")


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
