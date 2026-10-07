from __future__ import annotations

import argparse
from collections import Counter

from .core import store
from .core.config import Config
from .photos.ingest import format_summary, process_inbox
from .core.validate import find_warnings, refresh_status, validate_rows


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m pipeline")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("ingest", help="decode barcodes in data/inbox, look up metadata, append to books.csv")
    p.add_argument("--expect", type=int, help="total number of books you photographed (sanity check)")
    p.add_argument("--grid", help="rows x cols of the books on the front photo, e.g. 2x5 (default GRID in .env)")
    p.add_argument("--rotate", type=int, choices=[0, 90, 180, 270], help="degrees clockwise to turn the front photo upright")
    p.add_argument("--set-size", type=int, help="books per set (default SET_SIZE in .env)")
    sub.add_parser("validate", help="re-check every book and set its status (see README: Statuses)")
    sub.add_parser("init", help="create the data folders and show where they are")
    p = sub.add_parser("lookup", help="test the metadata sources for one ISBN and show exactly what each returned")
    p.add_argument("isbn")
    p.add_argument("--fresh", action="store_true", help="ignore the cache and ask the providers again (costs quota)")
    p.add_argument("--only", help="comma-separated providers to try, e.g. eslite,books_tw (default: your .env list)")
    sub.add_parser("quota", help="how many Google Books calls were used today")
    p = sub.add_parser("check-barcode", help="try to read the ISBN barcode in one or more single-book photos")
    p.add_argument("photos", nargs="+")
    p.add_argument("--effort", choices=["fast", "normal", "max"])
    p = sub.add_parser("orient", help="find out which way up your photos need to be turned (sets ROTATE)")
    p.add_argument("photo")
    p.add_argument("--save", default="orient.jpg")
    sub.add_parser("enrich", help="look up metadata for needs_manual rows where you have typed in the ISBN")
    sub.add_parser("genres", help="look up the genre of books saved before genres existed (or still without one)")
    sub.add_parser("titles", help="Taiwanese books saved with an English title: get the Chinese title (and redo the "
                                  "market price if it was not an exact match)")
    p = sub.add_parser("market", help="find the price of a NEW copy (eslite / books.com.tw / NCL) and fill empty prices")
    p.add_argument("isbn", nargs="?", help="just show it for one ISBN (nothing saved)")
    p.add_argument("--title", default="", help="with an ISBN: title to search by when the ISBN search finds nothing")
    p.add_argument("--fresh", action="store_true", help="ignore cached market prices and search again")
    p.add_argument("--redo-similar", action="store_true",
                   help="search again for books whose market price came from another edition or the e-book")
    p.add_argument("--reprice", action="store_true",
                   help="also recompute prices that were set automatically (after changing PRICE_RATIO / PRICE_ROUND)")
    p = sub.add_parser("fill", help="set condition/price on books that have none")
    p.add_argument("--set", dest="set_id", help="only this set, e.g. S01")
    p.add_argument("--position", help="only this number within the set")
    p.add_argument("--condition")
    p.add_argument("--price")
    p.add_argument("--currency", help="3-letter code, default DEFAULT_CURRENCY in .env")
    p.add_argument("--batch", help="default: all books with blanks")
    p = sub.add_parser("export-sets", help="one listing per complete set: numbered front photo + text of every book")
    p.add_argument("--grid", help="rows x cols for the numbers on the front photo (default GRID in .env)")
    p.add_argument("--no-numbers", action="store_true", help="don't draw numbers on the front photo (physical labels)")
    p.add_argument("--set-price", type=float, help="one price for the whole set (default: each book's own price)")
    p.add_argument("--set-currency", help="currency of --set-price (default DEFAULT_CURRENCY)")
    p.add_argument("--with-barcodes", action="store_true", help="also include each book's barcode photo, number stamped on")
    p.add_argument("--force", action="store_true")
    p = sub.add_parser("labels", help="write a printable sheet of numbers to cut out and place beside the books")
    p.add_argument("--count", type=int, default=10)
    sub.add_parser("status", help="row counts by status")
    sub.add_parser("review", help="open the review window: see and fix books that need attention")
    sub.add_parser("todo", help="list, in plain words, what each book still needs")
    sub.add_parser("bot", help="run the Telegram bot")
    a = ap.parse_args()
    cfg = Config.from_env()
    if a.cmd == "ingest":
        from .photos.grid import parse_grid
        cfg.set_size = a.set_size or cfg.set_size
        if a.grid:
            cfg.grid = parse_grid(a.grid)
        if a.rotate is not None:
            cfg.rotate = a.rotate

    if a.cmd == "ingest":
        from .core.progress import terminal
        with terminal():
            summary = process_inbox(cfg, expect=a.expect)
        print(format_summary(summary))
    elif a.cmd == "init":
        cfg.ensure_dirs()
        print(f"Data folder: {cfg.data_dir.resolve()}")
        print(f"Put (or sync) photos into: {cfg.inbox.resolve()}")
    elif a.cmd == "lookup":
        import logging
        logging.basicConfig(level=logging.WARNING, format="%(message)s")
        logging.getLogger("pipeline.lookup").setLevel(logging.INFO)  # show each Google search tried
        from .core import net
        from .core.isbn import normalize
        from .sources.lookup import FIELDS, call_provider, is_taiwan, providers_for
        isbn = normalize(a.isbn)
        if not isbn:
            raise SystemExit(f"{a.isbn} is not a valid ISBN (checksum failed)")
        names = tuple(x.strip() for x in a.only.split(",")) if a.only else providers_for(isbn, cfg)
        print(f"ISBN {isbn} ({'Taiwan: BOOK_PROVIDERS_TW' if is_taiwan(isbn) else 'BOOK_PROVIDERS'}) -> {', '.join(names)}")
        merged, used = {}, []
        for name in names:
            try:
                got, where = call_provider(name, isbn, cfg, use_cache=not a.fresh)
                used += [name] if got else []
                for k in FIELDS:
                    if got.get(k) and not merged.get(k):
                        merged[k] = got[k]
                shown = {k: (v[:80] + "..." if len(v) > 80 else v) for k, v in got.items() if v} or "nothing found (or no key set)"
                print(f"[{name}{' (cached)' if where == 'cache' else ''}] {shown}")
            except Exception as exc:
                print(f"[{name}] ERROR: {net.redact(exc)}")
        print(f"\nMerged title/author: {merged.get('title')!r} / {merged.get('author')!r}  (sources: {'+'.join(used)})")
        print(f"Google Books calls today: {net.DailyCounter(cfg.cache_dir / 'quota.json').used('google')}/{cfg.google_daily_limit}")
    elif a.cmd == "quota":
        from .core import net
        print(f"Google Books calls today (US Pacific day {net.pacific_today()}): "
              f"{net.DailyCounter(cfg.cache_dir / 'quota.json').used('google')}/{cfg.google_daily_limit}")
    elif a.cmd == "check-barcode":
        import time as _t
        from .photos.decode import ADDONS, available_engines, decode_isbn_image, load_image
        from .sources.market import addon_price
        print(f"Readers installed and enabled: {', '.join(available_engines()) or 'NONE - pip install zxing-cpp'}")
        for ph in a.photos:
            t0 = _t.time()
            img = load_image(ph)
            got = decode_isbn_image(img, effort=a.effort)
            addon = ADDONS.get(got, "") if got else ""
            price = addon_price(addon, got or "") if addon else None
            shown = f"   price barcode {addon}" + (f" = {price[0]} {price[1]}" if price else " (currency unknown)") \
                if addon else ("   price barcode: not read" if got else "")
            print(f"{ph}: {got or 'NOT READ'}{shown}   ({img.width}x{img.height}, {_t.time() - t0:.1f}s)")
    elif a.cmd == "orient":
        from .photos.decode import exif_orientation, load_image
        from .photos.grid import make_orient_sheet
        img = load_image(a.photo)
        tag = exif_orientation(a.photo)
        print(f"Photo size after applying any EXIF rotation: {img.width}x{img.height} px "
              f"({'landscape' if img.width >= img.height else 'portrait'})")
        print(f"EXIF orientation tag in the file: {tag if tag else 'none (the phone/Telegram did not keep one)'}")
        make_orient_sheet(img, a.save)
        print(f"Open {a.save}. Find the panel where the books are the right way up, then put that ROTATE=<number> in .env")
        print(f"Currently ROTATE={cfg.rotate}. Remember: it must also make the photo landscape if your grid is wider than tall.")
    elif a.cmd == "enrich":
        from .photos.ingest import enrich_rows
        # look books up WITHOUT holding the file (can take a while), then write only the rows that changed
        work = store.read_rows(cfg.csv_path)
        before = {r["sku"]: dict(r) for r in work}
        n = enrich_rows(work, cfg)
        changed = {r["sku"]: r for r in work if r != before[r["sku"]]}

        def apply(rows):
            for r in rows:
                if r["sku"] in changed and r == before.get(r["sku"]):  # skip rows someone edited meanwhile
                    r.update(changed[r["sku"]])
        store.update_rows(cfg.csv_path, apply)
        print(f"enriched {n} row(s)")
    elif a.cmd == "genres":
        from .sources.lookup import lookup_book
        work = store.read_rows(cfg.csv_path)
        found = {}
        for r in work:
            if r.get("status") in ("listed", "sold") or r.get("genre") or not r.get("isbn13"):
                continue
            g = lookup_book(r["isbn13"], cfg).get("genre", "")
            print(f"{r['sku']}  {r['isbn13']}  {(r.get('title') or '')[:30]:<30}  {g or '-'}")
            if g:
                found[r["sku"]] = g

        def apply(rows):
            for r in rows:
                if r["sku"] in found and not r.get("genre"):
                    r["genre"] = found[r["sku"]]
        store.update_rows(cfg.csv_path, apply)
        print(f"{len(found)} genre(s) added")
    elif a.cmd == "titles":
        import logging
        logging.basicConfig(level=logging.WARNING, format="%(message)s")
        logging.getLogger("pipeline.market").setLevel(logging.INFO)
        from .photos.ingest import chinese_titles
        work = store.read_rows(cfg.csv_path)
        before = {r["sku"]: dict(r) for r in work}
        skus = chinese_titles(work, cfg)
        fixed = {r["sku"]: r for r in work if r["sku"] in skus}

        def apply(rows):
            for r in rows:
                if r["sku"] in fixed and r == before.get(r["sku"]):  # skip rows someone edited meanwhile
                    r.update(fixed[r["sku"]])
        store.update_rows(cfg.csv_path, apply)
        for r in fixed.values():
            print(f"{r['sku']}  {r['isbn13']}  -> {r['title']}   market: {r.get('market_price') or '-'} "
                  f"{r.get('market_currency', '')} ({r.get('market_match') or 'not found'})")
        print(f"{len(fixed)} title(s) changed to Chinese")
    elif a.cmd == "market":
        import logging
        logging.basicConfig(level=logging.WARNING, format="%(message)s")
        logging.getLogger("pipeline.market").setLevel(logging.INFO)   # show every search tried
        from .sources.market import MARKET_FIELDS, apply_market, describe, find_market_price, forget_market, suggest_price
        print(describe(cfg))
        if a.isbn:
            got = find_market_price(a.isbn, a.title, cfg, use_cache=not a.fresh)
            if not got.get("market_price"):
                print("No market price found.", "; ".join(got.get("_errors", [])))
            else:
                print(f"New copy: {got['market_price']} {got['market_currency']}  ({got['market_source']})  {got['market_url']}")
                sug = suggest_price(got["market_price"], got["market_currency"], cfg)
                print(f"Your price: {sug['price']} {sug['currency']}  ({sug['price_basis']})")
        else:
            work = store.read_rows(cfg.csv_path)       # search WITHOUT holding the file, then write only changes
            changed = {}
            for r in work:
                if r.get("status") in ("listed", "sold") or not r.get("isbn13"):
                    continue
                old = dict(r)
                redo = a.redo_similar and r.get("market_match") in ("similar", "ebook")
                if a.fresh or redo:
                    forget_market(r)
                print(f"{r['sku']}  {r['isbn13']}  {r.get('title', '')[:30]}")
                for problem in apply_market(r, cfg, use_cache=not (a.fresh or redo), reprice=a.reprice):
                    print("   problem:", problem)
                if r != old:
                    changed[r["sku"]] = {k: r.get(k, "") for k in MARKET_FIELDS + ("price", "currency", "price_basis")}
                    print(f"   new copy {r.get('market_price')} {r.get('market_currency')} -> price "
                          f"{r.get('price')} {r.get('currency')}  ({r.get('price_basis')})")

            def apply(rows):
                for r in rows:
                    if r["sku"] in changed:
                        r.update(changed[r["sku"]])
                        refresh_status(r, cfg)
            store.update_rows(cfg.csv_path, apply)
            print(f"updated {len(changed)} book(s)")
    elif a.cmd == "validate":
        with store.locked(cfg.csv_path):
            rows = store.read_rows(cfg.csv_path)
            c = validate_rows(rows, cfg)
            store.write_rows(cfg.csv_path, rows)
        print("   ".join(f"{k}: {v}" for k, v in c.items() if v) or "no books yet")
        for r in rows:
            if r["status"] in ("needs_manual", "enriched"):
                print(f"  {r['sku']}  {r['status']:<12} {r['isbn13'] or '-':<14} to fix: {r['errors']}")
        for w in find_warnings(rows):
            print("  WARNING:", w)
    elif a.cmd == "fill":
        def fill(rows):
            n = 0
            for r in rows:
                if a.batch and r["batch"] != a.batch:
                    continue
                if a.set_id and r.get("set_id") != a.set_id:
                    continue
                if a.position and str(r.get("position")) != str(a.position):
                    continue
                if r["status"] in ("listed", "sold"):
                    continue
                if a.condition and not r["condition"]:
                    r["condition"], n = a.condition, n + 1
                if a.price and not r["price"]:
                    r["price"], r["currency"], n = a.price, (a.currency or cfg.default_currency).upper(), n + 1
                refresh_status(r, cfg)
            return n
        print(f"filled {store.update_rows(cfg.csv_path, fill)} field(s)")
    elif a.cmd == "export-sets":
        from .photos.grid import parse_grid
        from .apps.sets import build_set_listings
        res = build_set_listings(store.read_rows(cfg.csv_path), cfg, grid=parse_grid(a.grid) if a.grid else None,
                                 set_price=a.set_price, set_currency=(a.set_currency or "").upper(),
                                 with_backs=a.with_barcodes, force=a.force, numbers=not a.no_numbers)
        print(f"exported {len(res['made'])} set listing(s) to {cfg.listings_dir / 'sets'}")
        for key, why in res["skipped"]:
            print(f"  skipped {key}: {why}")
    elif a.cmd == "labels":
        from .apps.sets import make_label_sheet
        cfg.ensure_dirs()
        dst = cfg.data_dir / "labels.png"
        make_label_sheet(dst, a.count)
        print(f"wrote {dst} - print it, cut out the numbers")
    elif a.cmd == "status":
        counts = Counter(r["status"] for r in store.read_rows(cfg.csv_path))
        meaning = {"needs_manual": "book not identified yet (ISBN / title / author missing)",
                   "enriched": "identified, but price or condition missing/invalid, or a photo missing",
                   "to_check": "complete - give it a quick check in the review window and Save",
                   "validated": "checked by you, ready for a listing", "listed": "posted by you", "sold": "sold"}
        for st in ("needs_manual", "enriched", "to_check", "validated", "listed", "sold"):
            print(f"  {st:<13}{counts.pop(st, 0):>5}   {meaning[st]}")
        for st, n in counts.items():
            print(f"  {st:<13}{n:>5}")
    elif a.cmd == "review":
        try:
            from .apps.gui import run as run_gui
        except ImportError as exc:
            raise SystemExit(f"The review window needs PyQt6: pip install -r requirements.txt  ({exc})")
        run_gui(cfg)
    elif a.cmd == "todo":
        from .apps.editing import todo_text
        print(todo_text(cfg))
    elif a.cmd == "bot":
        from .apps.bot import run
        run(cfg)


if __name__ == "__main__":
    main()
