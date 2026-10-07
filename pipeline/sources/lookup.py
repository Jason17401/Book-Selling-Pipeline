"""Book metadata from several providers; later providers only fill gaps.

Providers (names for BOOK_PROVIDERS / BOOK_PROVIDERS_TW in .env):
  openlibrary         Open Library Books API          free, no key. Identified User-Agent (CONTACT_EMAIL), max ~1 call/s here
  openlibrary_search  Open Library Search API         free, no key. Finds some books the Books API misses
  google              Google Books API v1             GOOGLE_BOOKS_API_KEY; counted against GOOGLE_DAILY_LIMIT per day
  isbndb              ISBNdb (paid)                   ISBNDB_API_KEY
  eslite              誠品 eslite.com (Taiwan)          unofficial: the JSON search their own website uses
  books_tw            博客來 books.com.tw (Taiwan)      scraping: search page + product page
  ncl                 國家圖書館 全國新書資訊網 (Taiwan)   scraping: ISBN search, few details, last resort

Taiwanese ISBNs (978-957, 978-986, 978-626) use BOOK_PROVIDERS_TW, everything else BOOK_PROVIDERS.
Every answer (including "not found") is cached in data/cache/lookups.json, so re-running costs no quota.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Callable, Optional

from ..core import net, progress
from ..core.config import Config
from ..core.isbn import isbn10_to_13, normalize

log = logging.getLogger("pipeline.lookup")
FIELDS = ("title", "author", "publisher", "year", "pages", "genre")  # stored per book (sources may return more)
TW_PREFIXES = ("978957", "978986", "978626")
APP_UA = "BookSellingPipeline/1.0"


def is_taiwan(isbn: str) -> bool:
    return str(isbn).startswith(TW_PREFIXES)


def _year(s) -> str:
    m = re.search(r"\b(1[5-9]\d\d|20\d\d)\b", str(s or ""))
    return m.group(1) if m else ""


def _clean(v):
    if v is None:
        return ""
    if isinstance(v, (int, float)):
        return str(int(v)) if v else ""
    return re.sub(r"[ \t　]+", " ", str(v)).strip()


def _ol_headers(cfg: Config) -> dict:
    # Open Library asks frequent users to identify themselves: app name + contact email (gets 3 req/s instead of 1)
    return {"User-Agent": f"{APP_UA} ({cfg.contact_email})", "Accept": "application/json"}


# ---- Open Library ------------------------------------------------------------------------------
def from_openlibrary(isbn: str, cfg: Config) -> dict:
    """Open Library Books API. Example (paste into a browser):
        GET https://openlibrary.org/api/books?bibkeys=ISBN:9781451648546&format=json&jscmd=data
        header: User-Agent: BookSellingPipeline/1.0 (you@example.com)
    Reply: {"ISBN:9781451648546": {"title": ..., "authors": [{"name": ...}], "publishers": [...], ...}} or {} if unknown.
    """
    data = net.request("openlibrary", "GET", "https://openlibrary.org/api/books",
                       params={"bibkeys": f"ISBN:{isbn}", "format": "json", "jscmd": "data"},
                       headers=_ol_headers(cfg), min_interval=1.0)
    d = (data or {}).get(f"ISBN:{isbn}")
    if not d:
        return {}
    title = d.get("title", "")
    if d.get("subtitle"):
        title = f"{title}: {d['subtitle']}"
    cover = d.get("cover") or {}
    return {
        "title": title,
        "author": ", ".join(a.get("name", "") for a in d.get("authors", [])),
        "publisher": ", ".join(p.get("name", "") for p in d.get("publishers", [])),
        "year": _year(d.get("publish_date")),
        "pages": d.get("number_of_pages"),
        "cover_url": cover.get("large") or cover.get("medium") or "",
    }


def from_openlibrary_search(isbn: str, cfg: Config) -> dict:
    """Open Library Search API, indexed separately from the Books API, so it can succeed when that is empty. Example:
        GET https://openlibrary.org/search.json?q=9781451648546&limit=3&fields=title,subtitle,author_name,publisher,isbn,cover_i
    Reply: {"docs": [{"title": ..., "author_name": [...], "isbn": [... every ISBN of the work ...]}]}
    Only a doc whose isbn list contains OUR ISBN is used.
    """
    data = net.request("openlibrary", "GET", "https://openlibrary.org/search.json",
                       params={"q": isbn, "limit": 3,
                               "fields": "title,subtitle,author_name,publisher,isbn,cover_i"},
                       headers=_ol_headers(cfg), min_interval=1.0) or {}
    for d in data.get("docs", []):
        if isbn not in (d.get("isbn") or [isbn]):  # guard against a loose match
            continue
        title = d.get("title", "")
        if d.get("subtitle"):
            title = f"{title}: {d['subtitle']}"
        cover = f"https://covers.openlibrary.org/b/id/{d['cover_i']}-L.jpg" if d.get("cover_i") else ""
        # first_publish_year / page counts here describe the WORK, not this edition, so they are left blank on purpose
        return {"title": title, "author": ", ".join(d.get("author_name", [])),
                "publisher": ", ".join((d.get("publisher") or [])[:2]), "cover_url": cover}
    return {}


# ---- Google Books ------------------------------------------------------------------------------
GOOGLE_URL = "https://www.googleapis.com/books/v1/volumes"
GOOGLE_FIELDS = ("totalItems,items(id,volumeInfo(title,subtitle,authors,publisher,publishedDate,pageCount,printType,"
                 "industryIdentifiers,categories))")


def _google_isbns(v: dict) -> set:
    out = set()
    for ident in v.get("industryIdentifiers") or []:
        n = normalize(ident.get("identifier", ""))
        if n:
            out.add(n)
    return out


GOOGLE_VOLUME_FIELDS = ("id,volumeInfo(title,subtitle,authors,publisher,publishedDate,pageCount,printType,"
                        "industryIdentifiers,categories)")
GOOGLE_LINKS_URL = "https://books.google.com/books"
_JSONP_RE = re.compile(r"^[^(]*\((.*)\)\s*;?\s*$", re.S)


def pick_google_item(data: dict, isbn: str, strict: bool) -> Optional[dict]:
    """The volume whose industryIdentifiers contain our ISBN (13 or 10 - both are normalised to 13).
    strict=False (isbn: searches): a volume with NO identifiers at all is also accepted, because Google already matched
    it on the ISBN. strict=True (plain text search): the identifier MUST match - a plain search also returns books that
    merely MENTION the number in their text (e.g. '101 facts about Steve Jobs' for the Steve Jobs biography)."""
    loose = None
    for item in (data or {}).get("items") or []:
        v = item.get("volumeInfo") or {}
        ids = _google_isbns(v)
        if isbn in ids:
            return v
        if not ids and not strict and loose is None:
            loose = v
    return loose


def parse_google_links(text: str, isbn: str) -> str:
    """Dynamic Links reply 'cb({"ISBN:978...": {"info_url": "https://books.google.com/books?id=8U2oAAAAQBAJ&..."}});'
    -> the Google volume id ('8U2oAAAAQBAJ'), or '' if Google has no book with this ISBN."""
    m = _JSONP_RE.match((text or "").strip())
    if not m:
        return ""
    try:
        data = json.loads(m.group(1))
    except ValueError:
        return ""
    for key in (f"ISBN:{isbn}", f"ISBN:{isbn10(isbn)}"):
        entry = data.get(key) or {}
        for url_field in ("info_url", "preview_url", "thumbnail_url"):
            got = re.search(r"[?&]id=([A-Za-z0-9_-]+)", entry.get(url_field) or "")
            if got:
                return got.group(1)
    return ""


def google_volume_id(isbn: str, cfg: Config) -> str:
    """ISBN -> Google volume id with Google's 'Dynamic Links' service (documented, no key, NOT counted in the API quota):
        GET https://books.google.com/books?bibkeys=ISBN:9781451648546&jscmd=viewapi&callback=cb
        reply: cb({"ISBN:9781451648546": {"info_url": "https://books.google.com/books?id=8U2oAAAAQBAJ&source=gbs_ViewAPI", ...}});
    This is the same lookup that powers the book's web page, and it works when the API's isbn: search returns 0."""
    text = net.request("google_links", "GET", GOOGLE_LINKS_URL,
                       params={"bibkeys": f"ISBN:{isbn}", "jscmd": "viewapi", "callback": "cb"},
                       headers={"User-Agent": f"{APP_UA} ({cfg.contact_email})"}, min_interval=1.0, expect="text")
    return parse_google_links(text or "", isbn)


def _google_result(v: dict) -> dict:
    title = v.get("title", "")
    if v.get("subtitle"):
        title = f"{title}: {v['subtitle']}"
    from .genre import from_path
    cats = v.get("categories") or []
    return {
        "title": title,
        "author": ", ".join(v.get("authors", [])),
        "publisher": v.get("publisher", ""),
        "year": _year(v.get("publishedDate")),
        "pages": v.get("pageCount"),
        "genre": from_path(cats[0]) if cats else "",      # 'Juvenile Fiction / Action & Adventure / General'
    }


def from_google(isbn: str, cfg: Config, counter: Optional[net.DailyCounter] = None) -> dict:
    """Google Books. GOOGLE_STEPS (default "id,isbn") are tried in order until one finds THIS ISBN:

    id      1) ISBN -> volume id via Dynamic Links (free, see google_volume_id), then
            2) GET https://www.googleapis.com/books/v1/volumes/8U2oAAAAQBAJ?fields=...        (1 quota call)
            If Google has no book with this ISBN, step 1 says so and NO quota is used at all.
    isbn    GET https://www.googleapis.com/books/v1/volumes?q=isbn:9781451648546&maxResults=5&printType=books&fields=...
            (1 quota call; sometimes returns 0 for books Google does have - that's why 'id' goes first)
    isbn10  same with q=isbn:1451648545 (worked out from the 13-digit ISBN; you never need to supply it)
    plain   GET https://www.googleapis.com/books/v1/volumes?q=9781451648546&maxResults=20&...  (text search, strictly
            filtered: books that only MENTION the number are rejected)

    API calls send the key in the 'x-goog-api-key' header (Google's recommended way; never in the URL), and each
    one counts against GOOGLE_DAILY_LIMIT.
    To try by hand in a browser:  https://www.googleapis.com/books/v1/volumes/8U2oAAAAQBAJ?key=YOUR_KEY
    """
    headers = {"Accept": "application/json", "User-Agent": f"{APP_UA} (gzip)", "Accept-Encoding": "gzip"}
    if cfg.google_books_key:
        headers["x-goog-api-key"] = cfg.google_books_key
    counter = counter or net.DailyCounter(cfg.cache_dir / "quota.json")

    def api(url, params):
        counter.take("google", cfg.google_daily_limit)
        return net.request("google", "GET", url, params=params, headers=headers, min_interval=1.0) or {}

    i10 = isbn10(isbn)
    for step in cfg.google_queries:
        if step == "id":
            try:
                vid = google_volume_id(isbn, cfg)
            except net.ProviderBlocked as exc:  # the free link service refused: carry on with the API searches
                log.info("  google id lookup unavailable: %s", exc)
                continue
            if not vid:
                log.info("  google id lookup: Google has no book with ISBN %s", isbn)
                continue
            v = (api(f"{GOOGLE_URL}/{vid}", {"fields": GOOGLE_VOLUME_FIELDS}).get("volumeInfo")) or {}
            ids = _google_isbns(v)
            if v and (not ids or isbn in ids):
                log.info("  google id lookup -> volume %s", vid)
                return _google_result(v)
            log.info("  google volume %s does not list ISBN %s", vid, isbn)
            continue
        if step == "isbn":
            q, n, strict = f"isbn:{isbn}", 5, False
        elif step == "isbn10" and i10:
            q, n, strict = f"isbn:{i10}", 5, False
        elif step == "plain":
            q, n, strict = isbn, 20, True
        else:
            continue
        data = api(GOOGLE_URL, {"q": q, "fields": GOOGLE_FIELDS, "maxResults": n, "printType": "books"})
        v = pick_google_item(data, isbn, strict=strict)
        if v is None:
            log.info("  google q=%s -> %s result(s), none with this ISBN", q, data.get("totalItems", 0))
            continue
        log.info("  google q=%s -> found", q)
        return _google_result(v)
    return {}


# ---- ISBNdb ------------------------------------------------------------------------------------
def from_isbndb(isbn: str, cfg: Config) -> dict:
    """ISBNdb (paid). Example:
        GET https://api2.isbndb.com/book/9781451648546
        header: Authorization: <ISBNDB_API_KEY>
    Reply: {"book": {"title": ..., "title_long": ..., "authors": [...], "publisher": ..., "pages": ..., ...}}
    """
    if not cfg.isbndb_key:
        return {}
    data = net.request("isbndb", "GET", f"{cfg.isbndb_base}/book/{isbn}",
                       headers={"Authorization": cfg.isbndb_key, "Accept": "application/json"}, min_interval=1.1)
    b = (data or {}).get("book") or {}
    if not b:
        return {}
    return {
        "title": b.get("title_long") or b.get("title", ""),
        "author": ", ".join(b.get("authors", [])),
        "publisher": b.get("publisher", ""),
        "year": _year(b.get("date_published")),
        "pages": b.get("pages"),
        "format": b.get("binding", ""),
        "cover_url": b.get("image", ""),
    }


def _taiwan(name):
    def call(isbn, cfg):
        from . import taiwan
        return getattr(taiwan, name)(isbn, cfg)
    call.__name__ = name
    return call


PROVIDERS: dict = {"openlibrary": from_openlibrary, "openlibrary_search": from_openlibrary_search,
                   "google": from_google, "isbndb": from_isbndb,
                   "eslite": _taiwan("from_eslite"), "books_tw": _taiwan("from_books_tw"), "ncl": _taiwan("from_ncl")}


# bump a number when a provider's lookup changes, so old cached answers (especially "not found") are ignored
PROVIDER_VERSION = {"google": 4, "eslite": 2, "books_tw": 2, "ncl": 2}   # 2026-10: + genre


def _cache_name(name: str) -> str:
    v = PROVIDER_VERSION.get(name)
    return f"{name}@v{v}" if v else name


def providers_for(isbn: str, cfg: Config) -> tuple:
    return cfg.providers_tw if is_taiwan(isbn) and cfg.providers_tw else cfg.providers


def call_provider(name: str, isbn: str, cfg: Config, providers: dict = None, use_cache: bool = True,
                  cache: Optional[net.Cache] = None):
    """One provider, with cache. Returns (result_dict, 'cache'|'live'). Raises on errors."""
    fn: Callable = (providers or PROVIDERS).get(name)
    if fn is None:
        return {}, "live"
    cache = cache or net.Cache(cfg.cache_dir / "lookups.json", cfg.cache_days, cfg.cache_miss_days)
    if use_cache:
        hit = cache.get(_cache_name(name), isbn)
        if hit is not None:
            return hit, "cache"
    got = fn(isbn, cfg) or {}
    got = {k: _clean(v) for k, v in got.items() if k in FIELDS and _clean(v)}
    cache.put(_cache_name(name), isbn, got)
    return got, "live"


def has_cjk(text: str) -> bool:
    """True if the text contains Chinese characters."""
    return bool(re.search(r"[\u3400-\u9fff\uf900-\ufaff]", text or ""))


def lookup_book(isbn: str, cfg: Config, providers: dict = None, use_cache: bool = True) -> dict:
    """Ask the providers in order; later ones only fill blanks. For a TAIWANESE ISBN the Chinese title is wanted:
    Google often only knows the English title of a translated or bilingual book (9789862167557 -> 'A Ghost Tale for
    Christmas Time', really 神奇樹屋44: 狄更斯的耶誕頌), so the search goes on until a source gives a Chinese title
    (NCL usually does), and that one replaces the English title (and an English-only author name)."""
    result: dict = {}
    used, errors = [], []
    tw = is_taiwan(isbn)
    cache = net.Cache(cfg.cache_dir / "lookups.json", cfg.cache_days, cfg.cache_miss_days)
    for name in providers_for(isbn, cfg):
        if cfg.stop_when and all(result.get(k) for k in cfg.stop_when) and \
                (not tw or (has_cjk(result.get("title")) and ("genre" not in cfg.stop_when or has_cjk(result.get("genre"))))):
            break  # everything we need is filled: don't spend more calls / quota
        if net.blocked(name):
            msg = f"{name}: {net.blocked(name)}"
            if msg not in errors:
                errors.append(msg)
            continue
        progress.step("book", f"{name}: looking up {isbn}")
        try:
            got, _ = call_provider(name, isbn, cfg, providers, use_cache, cache)
        except net.QuotaReached as exc:
            net.block(name, str(exc))
            errors.append(f"{name}: {exc}")
            continue
        except Exception as exc:  # network/quota errors must not kill a batch, but must not be silent either
            log.debug("%s lookup failed for %s: %s", name, isbn, exc)
            progress.step("book", f"{name}: failed ({net.redact(exc)})")
            errors.append(f"{name}: {net.redact(exc)}")
            continue
        if got:
            used.append(name)
            progress.step("book", f"{name}: found {got.get('title') or '(no title)'}")
        else:
            progress.step("book", f"{name}: not found")
        for k in FIELDS:
            if got.get(k) and not result.get(k):
                result[k] = got[k]
            elif tw and k in ("title", "author", "genre") and has_cjk(got.get(k)) and not has_cjk(result.get(k)):
                result.setdefault(f"{k}_other", result[k])     # keep the English one for the market search
                result[k] = got[k]
    result["source"] = "+".join(used)
    result["_errors"] = errors
    return result


def isbn10(isbn13: str) -> str:
    """978-prefixed ISBN-13 -> ISBN-10 (some sites index the old form)."""
    if not isbn13.startswith("978"):
        return ""
    core = isbn13[3:12]
    total = sum((10 - i) * int(d) for i, d in enumerate(core))
    c = (11 - total % 11) % 11
    return core + ("X" if c == 10 else str(c))


__all__ = ["lookup_book", "PROVIDERS", "is_taiwan", "isbn10", "isbn10_to_13"]
