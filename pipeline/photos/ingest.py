"""Shared plumbing for ingesting photos: listing/ordering the inbox, moving files, captions, the run summary.
The set logic itself (1 front photo + one barcode photo per book) is in setlayout.py."""
from __future__ import annotations

import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from ..core.config import Config
from ..sources.lookup import lookup_book

IMG_EXT = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp"}
_COND_WORDS = {
    "new": "new", "likenew": "like_new", "like_new": "like_new", "mint": "like_new",
    "good": "good", "ok": "fair", "fair": "fair", "acceptable": "fair",
    "poor": "poor", "worn": "poor",
}
# Chinese condition words in a caption (longest first, so 近全新 is not read as 全新)
_COND_ZH = [("差強人意", "poor"), ("近全新", "like_new"), ("九成新", "like_new"), ("全新", "new"), ("良好", "good"),
            ("八成新", "good"), ("普通", "fair"), ("七成新", "fair"), ("差", "poor")]


@dataclass
class Summary:
    batch: str = ""
    rows: list = field(default_factory=list)
    orphans: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    review_sheets: list = field(default_factory=list)
    notes: list = field(default_factory=list)


def _natural(p: Path):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", p.name)]


def order_photos(photos: list) -> list:
    """Photos in the order they were SENT: the Telegram bot names each file by its message number
    (0000000402.jpg, 0000000403_IMG_1234.jpg ...), so sorting by name is the send order. Files you copy into the
    inbox yourself are taken in name order too."""
    return sorted(photos, key=_natural)


def list_photos(inbox: Path, settle_seconds: int = 5) -> list:
    """Photos in the inbox, in send order. settle_seconds > 0 skips files changed in the last N seconds (a sync may
    still be writing them); 0 = take everything. (Never compare against 0: on Windows a file written a moment ago can
    carry a modification time slightly AFTER time.time(), which would silently drop the newest photo.)"""
    now = time.time()
    out = []
    for p in Path(inbox).iterdir():
        if not (p.is_file() and p.suffix.lower() in IMG_EXT and not p.name.startswith(".")):
            continue
        if settle_seconds > 0 and now - p.stat().st_mtime < settle_seconds:
            continue
        out.append(p)
    return order_photos(out)


def parse_caption(text: str, default_currency: str = "TWD"):
    """'good 12.50' -> ('good', '12.50', 'TWD'); 'like new 150 NZD' -> ('like_new', '150', 'NZD');
    '近全新 150' / '良好150' -> ('like_new', '150', 'TWD'). Any part may be missing (all optional)."""
    t = (text or "").replace("$", " ")
    cond, price, currency = "", "", ""
    for word, code in _COND_ZH:
        if word in t:
            cond = code
            t = t.replace(word, " ")
            break
    for tok in re.split(r"[\s,]+", t.lower().replace("like new", "like_new")):
        if tok in _COND_WORDS and not cond:
            cond = _COND_WORDS[tok]
        elif re.fullmatch(r"\d+(?:\.\d+)?", tok) and not price:
            price = tok
    for word in re.findall(r"\b([A-Za-z]{3})\b", t):
        if word.upper() in KNOWN_CURRENCIES:
            currency = {"NTD": "TWD", "RMB": "CNY"}.get(word.upper(), word.upper())
            break
    return cond, price, (currency or default_currency) if price else currency


KNOWN_CURRENCIES = {"NZD", "TWD", "NTD", "AUD", "USD", "EUR", "GBP", "JPY", "HKD", "CNY", "RMB", "SGD", "CAD"}


def _sidecar(photo: Path) -> Path:
    return photo.with_suffix(".txt")


def _move(src: Path, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    shutil.move(str(src), str(dest))
    side = _sidecar(src)
    if side.exists():
        shutil.move(str(side), str(dest_dir / side.name))
    return dest


def process_inbox(cfg: Config, lookup: Callable = lookup_book, expect: Optional[int] = None, decode=None) -> "Summary":
    from .setlayout import process_sets
    return process_sets(cfg, decode=decode, lookup=lookup, expect=expect)


def note_lookup_errors(s: Summary, meta: dict) -> None:
    for e in meta.get("_errors", []):
        msg = f"Lookup problem - {e}"
        if msg not in s.warnings:
            s.warnings.append(msg)


def enrich_rows(rows: list, cfg: Config, lookup: Callable = lookup_book) -> int:
    """For needs_manual rows that now have an ISBN (typed in by you), fetch metadata and promote to enriched."""
    from ..core.isbn import normalize
    n = 0
    for r in rows:
        if r.get("status") != "needs_manual":
            continue
        isbn = normalize(r.get("isbn13", ""))
        if not isbn:
            continue
        r["isbn13"] = isbn
        if not (r.get("title") and r.get("author")):
            meta = lookup(isbn, cfg)
            for k in ("title", "author", "publisher", "year", "pages", "genre"):
                if not r.get(k):
                    r[k] = meta.get(k, "")
            r["source"] = r.get("source") or meta.get("source", "")
        if cfg.market_providers:
            from ..sources.market import apply_market
            apply_market(r, cfg)
        from ..core.validate import refresh_status
        refresh_status(r, cfg)
        if r.get("title") and r.get("author"):
            n += 1
    return n


def chinese_titles(rows: list, cfg: Config, lookup: Callable = lookup_book) -> list:
    """Taiwanese books saved with an ENGLISH title (Google only knew the English one): look them up again for the
    Chinese title (and Chinese author name), keep the English title in notes, and search the market price again if
    it was not an exact match. Returns the skus changed."""
    from ..sources.lookup import has_cjk, is_taiwan
    from ..sources.market import apply_market, forget_market
    from ..core.validate import DONE, refresh_status
    changed = []
    for r in rows:
        isbn = r.get("isbn13", "")
        if r.get("status") in DONE or not isbn or not is_taiwan(isbn) or has_cjk(r.get("title")):
            continue
        meta = lookup(isbn, cfg)
        if not has_cjk(meta.get("title")):
            continue
        old = r.get("title", "")
        r["title"] = meta["title"]
        if has_cjk(meta.get("author")) and not has_cjk(r.get("author")):
            r["notes"] = "; ".join(x for x in (r.get("notes"), f"author in English: {r.get('author')}") if x)
            r["author"] = meta["author"]
        if old:
            r["notes"] = "; ".join(x for x in (r.get("notes"), f"English title: {old}") if x)
        if cfg.market_providers and r.get("market_match") not in ("exact", "manual"):
            forget_market(r)
            apply_market(r, cfg)
        refresh_status(r, cfg)
        changed.append(r["sku"])
    return changed


def format_summary(s: Summary) -> str:
    if not s.rows and not s.orphans:
        return "\n".join(s.notes + s.warnings) or "Nothing to process."
    lines = [f"Batch {s.batch}: {len(s.rows)} book(s)"]
    for r in s.rows:
        title = (r["title"] or ("??? unreadable barcode" if not r["isbn13"] else "??? not found"))[:45]
        price = f"{r['price']} {r['currency']}" if r.get("price") else "-"
        if r.get("market_price"):
            price += f", new {r['market_price']} {r['market_currency']}"
        lines.append(f"{r['set_id']}-{int(r['position']):02d}  {r['isbn13'] or '-------------'}  {title}  "
                     f"[{r['condition'] or '-'}, {price}]")
    lines += s.notes
    lines += [f"WARNING: {w}" for w in s.warnings]
    lines += [f"CHECK THIS: {p} (on each row, the book from the front photo and the barcode photo must match)"
              for p in s.review_sheets]
    return "\n".join(lines)
