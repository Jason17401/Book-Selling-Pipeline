"""The rules for "is this book complete?", in ONE place - used by `validate`, the review window and `/todo`.

Status of a book (column `status` in books.csv):
  needs_manual  The book is not identified yet: barcode unreadable / ISBN missing or mistyped, or no title/author found.
  enriched      The book is identified (ISBN + title + author) but something else is missing or wrong: no price yet
                (no market price was found to work it out from), no condition, an invalid price / condition /
                currency, or a photo file is missing.
  validated     Complete: ready to go into a listing (export-sets / "Make listings").
  listed        You posted it (set this yourself). The pipeline never changes listed / sold books.
  sold          Sold (set this yourself).
Condition and price are REQUIRED (new books get DEFAULT_CONDITION, normally like_new, and a price worked out from the
market price when one is found). The market price is OPTIONAL: an empty one is fine, a filled-in one must be valid.
"""
from __future__ import annotations

import re
from pathlib import Path

from .config import Config
from .isbn import isbn13_valid

CONDITIONS = {"new", "like_new", "good", "acceptable", "poor"}
DONE = ("listed", "sold")


def photos_of(row: dict) -> list:
    return [p for p in (row.get("front_photo"), row.get("barcode_photo")) if p]


def row_issues(r: dict, cfg: Config) -> list:
    """What still needs doing, as [{"field": column or "", "msg": plain words}]. Empty list = complete."""
    out = []
    isbn = (r.get("isbn13") or "").strip()
    if not isbn:
        out.append({"field": "isbn13", "msg": "ISBN missing: type the 13 digits printed under the barcode"})
    elif not isbn13_valid(isbn):
        out.append({"field": "isbn13", "msg": "ISBN has a typo (the last digit doesn't match): check every digit"})
    for f, name in (("title", "Title"), ("author", "Author")):
        if not (r.get(f) or "").strip():
            hint = " - press 'Look up' to fetch it" if isbn and isbn13_valid(isbn) else ""
            out.append({"field": f, "msg": f"{name} missing{hint}"})
    cond = (r.get("condition") or "").strip()
    if not cond:
        out.append({"field": "condition", "msg": "Condition missing: pick one (like_new if nothing is wrong with it)"})
    elif cond not in CONDITIONS:
        out.append({"field": "condition", "msg": f"Condition '{cond}' is not one of: {', '.join(sorted(CONDITIONS))}"})
    price = (r.get("price") or "").strip()
    if not price:
        why = ("no market price was found to work it out from" if not (r.get("market_price") or "").strip()
               else "press '= 40% of market' or type it")
        out.append({"field": "price", "msg": f"Price missing: {why} - type your price and its currency"})
    else:
        try:
            p = float(price)
            if not (cfg.min_price <= p <= cfg.max_price):
                out.append({"field": "price", "msg": f"Price {p:g} is outside {cfg.min_price:g}-{cfg.max_price:g} "
                                                     "(MIN_PRICE / MAX_PRICE in .env)"})
        except ValueError:
            out.append({"field": "price", "msg": "Price must be a number, e.g. 5 or 12.50"})
        if not re.fullmatch(r"[A-Z]{3}", (r.get("currency") or "").strip()):
            out.append({"field": "currency", "msg": "Currency must be a 3-letter code like NZD or TWD"})
    mp = (r.get("market_price") or "").strip()
    if mp:
        try:
            float(mp)
        except ValueError:
            out.append({"field": "market_price", "msg": "Market price must be a number, e.g. 300"})
        if not re.fullmatch(r"[A-Z]{3}", (r.get("market_currency") or "").strip()):
            out.append({"field": "market_currency", "msg": "Market price currency must be a 3-letter code like TWD"})
    if not r.get("barcode_photo"):
        out.append({"field": "", "msg": "No barcode photo recorded for this book"})
    missing = [Path(p).name for p in photos_of(r) if not Path(p).exists()]
    if missing:
        out.append({"field": "", "msg": f"Photo file missing: {', '.join(missing)}"})
    return out


def refresh_status(r: dict, cfg: Config) -> list:
    """Set status/errors from the row's content (never touches listed/sold). Returns the issues."""
    issues = row_issues(r, cfg)
    if r.get("status") in DONE:
        return issues
    identified = isbn13_valid((r.get("isbn13") or "").strip()) and r.get("title") and r.get("author")
    if not issues:
        r["status"], r["errors"] = "validated", ""
    else:
        r["status"] = "enriched" if identified else "needs_manual"
        r["errors"] = "; ".join(sorted({i["field"] or "photo" for i in issues}))
    return issues


def validate_rows(rows: list, cfg: Config) -> dict:
    """Re-check every book that is not listed/sold and set its status."""
    counts = {"validated": 0, "failed": 0}
    for r in rows:
        if r.get("status") in DONE:
            continue
        counts["failed" if refresh_status(r, cfg) else "validated"] += 1
    return counts


def find_warnings(rows: list) -> list:
    """Non-blocking checks: duplicate SKUs (bug) and repeated ISBNs (maybe a second copy, maybe a re-scan)."""
    warns, skus, isbns = [], set(), {}
    for r in rows:
        if r["sku"] in skus:
            warns.append(f"duplicate sku {r['sku']}")
        skus.add(r["sku"])
        if r.get("status") in ("enriched", "validated", "listed") and r.get("isbn13"):
            isbns.setdefault(r["isbn13"], []).append(r["sku"])
    for isbn, s in isbns.items():
        if len(s) > 1:
            warns.append(f"ISBN {isbn} appears {len(s)} times ({', '.join(s)}) - second copy or re-scan?")
    return warns
