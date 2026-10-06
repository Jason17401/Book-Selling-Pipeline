"""Market price of a NEW copy (定價, the publisher's list price) from Taiwanese bookshops, and your price from it.

Why not just search the ISBN?  Both shops' search boxes often answer "no results" for an ISBN even when they sell the
book (they index titles better than ISBNs). So for each shop we try, in order (MARKET_SEARCH):
    isbn    the 13-digit ISBN
    isbn10  the old 10-digit form (worked out automatically)
    title   the book's title (found earlier by the metadata providers), main part only
and we ONLY accept a product whose own ISBN equals ours - a title search can return other editions, the e-book, or a
different book with a similar name, and those prices would be wrong. E-books are skipped.

Shops (MARKET_PROVIDERS, first one that finds the book wins):
  eslite    GET https://athena.eslite.com/api/v2/search?q=<isbn or title>&size=20&start=0
            each hit carries isbn / isbn10 / ean, mprice (定價) and final_price (sale price)
  books_tw  GET https://search.books.com.tw/search/query/key/<isbn or title>/cat/all   -> product ids
            GET https://www.books.com.tw/products/<id>   -> ISBN：..., 定價：300元, 優惠價：79折237元
  ncl       國家圖書館 ISBN record (the price the publisher registered), last resort

Your price = market price x PRICE_RATIO (default 0.4), in the market price's own currency (TWD for these shops - no
currency conversion), rounded to PRICE_ROUND. Books with no market price keep whatever price you give them
(DEFAULT_CURRENCY, normally TWD, when you don't name a currency).
"""
from __future__ import annotations

import logging
import math
import re
import unicodedata
from typing import Optional
from urllib.parse import quote

from ..core import net, progress
from . import taiwan, websearch
from ..core.config import Config
from ..core.isbn import normalize

log = logging.getLogger("pipeline.market")
MARKET_FIELDS = ("market_price", "market_currency", "market_source", "market_url", "market_match", "market_isbn")
VERSION = 8   # bump to ignore old cached answers


def _main_title(title: str) -> str:
    """'被討厭的勇氣: 自我啟發之父阿德勒的教導' -> '被討厭的勇氣' (shops match short titles better)."""
    t = re.split(r"\s*[:：(（\[【]\s*", (title or "").strip())[0]
    return t.strip()[:60]


def search_title(title: str) -> str:
    """What to type into a shop's search box for this title: the main title, but for a numbered series volume the
    volume's own name - '神奇樹屋 42: 鬼屋裡的音樂家' / '神奇樹屋. 42, 鬼屋裡的音樂家' -> '鬼屋裡的音樂家'."""
    segs = [x.strip() for x in re.split(r"\s*[:：,，]\s*|\.\s+|．", title or "") if x.strip()]
    if len(segs) >= 2 and (re.search(r"\d+$", segs[0]) or re.fullmatch(r"\d+", segs[1])):
        rest = [x for x in segs[1:] if not re.fullmatch(r"\d+", x)]
        if rest:
            return _main_title(rest[0])
    return _main_title(title)


def describe(cfg: Config) -> str:
    """One line for the log: where market prices will be looked for, in order."""
    if not cfg.market_providers:
        return "Market prices: OFF (MARKET_PROVIDERS is empty)"
    shops = [s for s in cfg.market_providers if s in ("eslite", "books_tw")]
    web = websearch.enabled(cfg)
    steps = []
    if shops:
        steps.append(f"ISBN on {', '.join(shops)}")
    if "ncl" in cfg.market_providers:
        steps.append("ncl")
    if web and shops:
        steps.append("web search (ISBN)")
    if cfg.market_ebook:
        steps.append("e-book with same ISBN")
    if "title" in cfg.market_search and shops:
        steps.append(f"title on {', '.join(shops)}" + (" (+ other editions)" if cfg.market_similar else ""))
    if web and shops and cfg.market_similar and "title" in cfg.market_search:
        steps.append("web search (title)")
    if "barcode" in cfg.market_providers:
        steps.append("price barcode")
    return f"Market prices: {' -> '.join(steps)}; {websearch.describe(cfg)}"


def _price(*values) -> str:
    """The list price is the highest of the prices a shop shows (定價 >= any discounted price)."""
    nums = []
    for v in values:
        try:
            if v not in (None, ""):
                nums.append(float(str(v).replace(",", "")))
        except ValueError:
            pass
    nums = [n for n in nums if n > 0]
    if not nums:
        return ""
    best = max(nums)
    return str(int(best)) if best == int(best) else f"{best:.2f}"


# ---- shops ---------------------------------------------------------------------------------------
def _isbns_of(d: dict) -> set:
    return {normalize(str(d.get(k) or "")) for k in ("isbn", "isbn13", "isbn10", "ean")} - {None, ""}


# eslite product links: https://www.eslite.com/product/1001156501304480 and the OLD form search engines still have,
# http://www.eslite.com/product.aspx?pgid=1001156501304480 (same product id)
ESLITE_ID = re.compile(r"eslite\.com/product(?:/|\.aspx\?(?:[^#\s]*&)?pgid=)(\d+)", re.I)
BOOKS_ID = re.compile(r"books\.com\.tw/(?:[^?#\s]*/)?products/([0-9A-Z]{10})")
SHOP_SITES = {"eslite": "eslite.com", "books_tw": "books.com.tw"}


# ---- "the same book, another edition" ---------------------------------------------------------------
_EDITION_BRACKET = re.compile(r"[(\[【〔]([^)\]】〕]*(?:版|edition|ed\.|紀念|典藏|修訂|增訂|新裝|暢銷|平裝|精裝|"
                              r"首刷|限定|中英|雙語|附)[^)\]】〕]*)[)\]】〕]", re.I)
_EDITION_TAIL = re.compile(r"(?:全新|最新|新|增訂|修訂|增修|紀念|典藏|暢銷|經典|珍藏)?(?:版|新裝版)$")
_ROLE = re.compile(r"(?:編著|主編|原著|作者|著|編|譯者|譯|繪者|繪|圖|文|撰|口述|監修|審訂)$")
_SET_WORDS = re.compile(r"套書|合售|套組|全套|冊合|共\d+冊|\d+\s*冊|boxed? ?set|collection", re.I)


def _key(text: str) -> str:
    t = re.sub(r"[\W_]+", "", text)
    return _EDITION_TAIL.sub("", t) if len(t) > 3 else t


def title_parts(title: str) -> list:
    """'神奇樹屋 42: 鬼屋裡的音樂家（中英對照版）' -> ['神奇樹屋42', '鬼屋裡的音樂家'] - the title's pieces without
    edition words, punctuation or spaces (volume numbers kept)."""
    t = unicodedata.normalize("NFKC", title or "").lower()
    t = _EDITION_BRACKET.sub("", t)
    parts = [_key(p) for p in re.split(r"\s*[:：]\s*|\s+-\s+|——|\s*/\s*", t)]
    return [p for p in parts if len(p) >= 2]


def title_key(title: str, main_only: bool = True) -> str:
    """The main part: '被討厭的勇氣（二十萬冊紀念版）: 自我啟發之父…' -> '被討厭的勇氣'."""
    parts = title_parts(title)
    if not parts:
        return ""
    return parts[0] if main_only else "".join(parts)


def same_title(ours: str, theirs: str) -> bool:
    """One title's pieces all appear in the other: '鬼屋裡的音樂家' = '神奇樹屋 42: 鬼屋裡的音樂家' (series name added),
    '被討厭的勇氣' = '被討厭的勇氣: 自我啟發之父…' (subtitle left out), '神奇樹屋. 44, 狄更斯的耶誕頌' (NCL style) =
    '神奇樹屋 44: 狄更斯的耶誕頌'. Not equal: '哈利波特: 神秘的魔法石' / '哈利波特: 消失的密室' (each has a piece the
    other lacks), and '神奇樹屋' / '神奇樹屋 42: ...' (a volume number follows - another book of the series)."""
    a, b = title_parts(ours), title_parts(theirs)
    if not a or not b:
        return False
    ja, jb = "".join(a), "".join(b)

    def inside(parts, joined):
        for p in parts:
            i = joined.find(p)
            if i < 0:
                return False
            after = joined[i + len(p): i + len(p) + 1]
            if after.isdigit() and not p[-1:].isdigit():
                return False                               # 'series' found, but 'series 42' is a specific volume
        return len("".join(parts)) >= 3
    return inside(a, jb) or inside(b, ja)


def author_names(author: str) -> list:
    """'岸見一郎, 古賀史健 (譯者 葉小燕)' -> ['岸見一郎', '古賀史健'] (no translators, roles or punctuation)."""
    a = unicodedata.normalize("NFKC", author or "")
    a = re.sub(r"[(\[]\s*(?:譯者|譯|繪者|translator)[^)\]]*[)\]]", "", a, flags=re.I)
    out = []
    for part in re.split(r"[,;、/&|]|\s{2,}|\band\b", a):
        part = re.sub(r"[(\[][^)\]]*[)\]]", "", part)            # '(Cole Nussbaumer Knaflic)'
        part = _ROLE.sub("", re.sub(r"[\W_]+", "", part).lower())
        if len(part) >= 2 and part not in out:
            out.append(part)
    return out


def _script(names: list) -> set:
    from .lookup import has_cjk
    return {"zh" if has_cjk(n) else "latin" for n in names}


def same_author(ours: str, theirs: str):
    """True / False, or None when it cannot be told: one side only has the English name (Mary Pope Osborne) and the
    other only the Chinese one (瑪麗．波．奧斯本)."""
    a, b = author_names(ours), author_names(theirs)
    if not a or not b:
        return None
    if not (_script(a) & _script(b)):
        return None
    fa, fb = "".join(a), "".join(b)
    return any(n in fb for n in a) or any(n in fa for n in b)


def same_language(our_isbn: str, their_isbn: str) -> bool:
    """A Taiwanese ISBN's other edition must also be Taiwanese (never the English original), and vice versa."""
    from .lookup import is_taiwan
    if not their_isbn:
        return True
    return is_taiwan(our_isbn) == is_taiwan(their_isbn)


def same_book(our_title: str, our_author: str, their_title: str, their_author: str) -> bool:
    """`their` book is ours in another edition: the same title (edition words like 新版 / 紀念版 ignored, a series name
    in front allowed) AND the same author. If the authors can't be compared (English vs Chinese name) the title must
    be a long, clear match; without an author on either side the FULL titles must be equal."""
    if _SET_WORDS.search(their_title or "") and not _SET_WORDS.search(our_title or ""):
        return False                                   # a boxed set is not the book
    if not same_title(our_title, their_title):
        return False
    who = same_author(our_author, their_author)
    if who is not None:
        return who
    if not author_names(our_author) or not author_names(their_author):
        if not (author_names(our_author) or author_names(their_author)):
            return title_key(our_title, False) == title_key(their_title, False)
    return min(len("".join(title_parts(our_title))), len("".join(title_parts(their_title)))) >= 4


class Hunt:
    """One book's market price search. Order (cheapest and most exact first):
        1. each shop's own search box: ISBN, then the 10-digit ISBN                    exact
        2. NCL, Taiwan's ISBN agency                                                   exact
        3. web search for the ISBN (one search per query form, see websearch.py)       exact
        4. an e-book with OUR ISBN met on the way (only its price is known)            ebook
        5. each shop's own search box: the TITLE - our ISBN, else the same title + author in another edition
        6. web search for the title + author (one search for both shops)              exact or similar
    The first exact answer wins; the e-book and the other edition are only fallbacks."""

    def __init__(self, isbn: str, title: str, author: str, cfg: Config):
        self.isbn, self.title, self.author, self.cfg = isbn, title or "", author or "", cfg
        self.ebook: dict = {}
        self.candidates: list = []    # other editions found on the way (best one used, see similar)
        self.opened: set = set()      # product ids already looked at (never opened twice)
        self.errors: list = []

    # ---- results ---------------------------------------------------------------------------------
    @staticmethod
    def found(price, source, url, match="exact", other_isbn="", year="") -> dict:
        return {"market_price": price, "market_currency": "TWD", "market_source": source, "market_url": url,
                "market_match": match, "market_isbn": other_isbn, "_year": year}

    def offer_ebook(self, price, shop, url):
        if price and not self.ebook and self.cfg.market_ebook:
            log.info("  %s: only an E-BOOK with this ISBN (%s TWD) - kept in case no printed copy is found", shop, price)
            self.ebook = self.found(price, f"{shop} (e-book, same ISBN)", url, "ebook", self.isbn)

    def offer_similar(self, price, shop, url, their_isbn, their_title, year, how, their_author=""):
        if not price or not self.cfg.market_similar or not same_language(self.isbn, their_isbn):
            return
        ed = f"{year} edition" if year else "another edition"
        log.info("  %s: the same book in %s: %s (ISBN %s) %s TWD - kept in case no exact copy is found", shop, ed,
                 their_title, their_isbn or "?", price)
        found = self.found(price, f"{shop} ({how}) - similar: {ed}, ISBN {their_isbn or '?'}", url,
                           "similar", their_isbn, year)
        # how sure: authors compared and equal, the full title equal, then the newest edition
        found["_score"] = (same_author(self.author, their_author) is True,
                           title_key(self.title, False) == title_key(their_title, False), year or "0")
        self.candidates.append(found)

    @property
    def similar(self) -> dict:
        return max(self.candidates, key=lambda c: c["_score"]) if self.candidates else {}

    def check(self, shop, their_isbn, their_title, their_author, price, url, year, how, ebook=False) -> dict:
        """Judge one product: ours -> the result; our ISBN as e-book / another edition -> kept as fallback."""
        if their_isbn == self.isbn:
            if ebook:
                self.offer_ebook(price, shop, url)
                return {}
            if price:
                return self.found(price, f"{shop} ({how})", url)
            return {}
        if not ebook and same_book(self.title, self.author, their_title, their_author):
            self.offer_similar(price, shop, url, their_isbn, their_title, year, how, their_author)
        return {}

    # ---- eslite ----------------------------------------------------------------------------------
    def eslite_search(self, q: str, how: str) -> dict:
        progress.step("market", f"eslite: searching {how} {q}")
        data = net.request("eslite", "GET", taiwan.ESLITE_SEARCH, params={"q": q, "size": 20, "start": 0},
                           memo=True, headers=taiwan._headers(self.cfg, "https://www.eslite.com/", "application/json"),
                           min_interval=self.cfg.scrape_delay) or {}
        hits = (data.get("hits") or {}).get("hit") or []
        hits = sorted(hits, key=lambda h: bool((h.get("fields") or {}).get("is_ebook")))   # printed first
        for h in hits:
            f = h.get("fields") or {}
            codes = _isbns_of(f)
            their = self.isbn if self.isbn in codes else next(iter(sorted(codes)), "")
            got = self.check("eslite", their, taiwan._text(f.get("name")), taiwan._text(f.get("author")),
                             _price(f.get("mprice"), f.get("final_price")),
                             f"https://www.eslite.com/product/{h.get('id')}", taiwan._year(f.get("manufacturer_date")),
                             how, ebook=bool(f.get("is_ebook")))
            if got:
                log.info("  eslite q=%s (%s): %s TWD", q, how, got["market_price"])
                return got
        log.info("  eslite q=%s (%s): %s result(s), none is a printed copy with this ISBN", q, how,
                 (data.get("hits") or {}).get("found", len(hits)))
        return {}

    def eslite_product(self, pid: str, how: str, text: str = "") -> dict:
        if ("eslite", pid) in self.opened:
            return {}
        self.opened.add(("eslite", pid))
        progress.step("market", f"checking eslite product {pid}")
        try:
            prod = taiwan.eslite_product(pid, self.cfg)
        except net.ProviderBlocked:
            prod = {}
        url = f"https://www.eslite.com/product/{pid}"
        if not prod:   # eslite did not answer: the page text the search engine gave us
            price = websearch.price_in_text(text, self.isbn)
            return self.found(price, f"eslite ({how})", url) if price else {}
        codes = _isbns_of(prod)
        their = self.isbn if self.isbn in codes else next(iter(sorted(codes)), "")
        price = _price(prod.get("final_price"), prod.get("retail_price"), prod.get("mprice"))
        oop = " - out of print" if prod.get("product_button_status") == "out_of_print" else ""
        name = taiwan._text(prod.get("name") or prod.get("title"))
        author = taiwan._text(prod.get("author") or prod.get("authors") or prod.get("author_name"))
        got = self.check("eslite", their, name, author, price, url,
                         taiwan._year(prod.get("manufacturer_date") or prod.get("publish_date")), how,
                         ebook=bool(prod.get("is_ebook")))
        if got and oop:
            got["market_source"] += oop
        log.info("  eslite product %s: %s", pid, f"ISBN matches, {price} TWD{oop}" if got
                 else f"ISBN {their or '?'} - not this book")
        return got

    # ---- books.com.tw ----------------------------------------------------------------------------
    def books_page(self, pid: str, referer: str, text: str = "") -> dict:
        """Product page facts, read directly, else from the search engine's page text, else via Tavily."""
        url = taiwan.BOOKS_PRODUCT.format(id=pid)
        page = ""
        if not net.blocked("books_tw"):
            try:
                page = net.request("books_tw", "GET", url, headers=taiwan._headers(self.cfg, referer), memo=True,
                                   min_interval=self.cfg.scrape_delay, expect="text") or ""
                if page and net.looks_blocked(page):
                    _human_check()
            except net.ProviderBlocked:
                page = ""
        info = taiwan.parse_books_product(page, "") if page else {}
        if not info.get("isbn_on_page") and text and self.isbn in re.sub(r"[\s-]", "", text):
            price = websearch.price_in_text(text, self.isbn)
            if price:
                return {"isbn_on_page": self.isbn, "list_price": price, "title": "", "author": "", "year": ""}
        if not info.get("isbn_on_page") and net.blocked("books_tw") and websearch.enabled(self.cfg):
            extracted = websearch.page_text(url, self.cfg)
            info = taiwan.parse_books_product(extracted, "") or {}
            if not info.get("isbn_on_page") and websearch.price_in_text(extracted, self.isbn):
                info = {"isbn_on_page": self.isbn, "list_price": websearch.price_in_text(extracted, self.isbn),
                        "title": "", "author": "", "year": ""}
        if not info.get("isbn_on_page") and page:
            path = net.save_debug(self.cfg.cache_dir, f"books_tw_product_{pid}", page)
            log.info("  books.com.tw product %s: no ISBN found on the page (saved to %s)", pid, path)
        return info

    def books_product(self, pid: str, how: str, referer: str, text: str = "") -> dict:
        if ("books_tw", pid) in self.opened:
            return {}
        self.opened.add(("books_tw", pid))
        progress.step("market", f"checking books.com.tw product {pid}")
        info = self.books_page(pid, referer, text)
        their = normalize(info.get("isbn_on_page") or "") or ""
        if not their:
            return {}
        price = _price(info.get("list_price"), info.get("sale_price"))
        got = self.check("books_tw", their, info.get("title", ""), info.get("author", ""), price,
                         taiwan.BOOKS_PRODUCT.format(id=pid), info.get("year", ""), how, ebook=pid.startswith("E"))
        log.info("  books.com.tw product %s: %s", pid, f"ISBN matches, {price} TWD" if got
                 else f"ISBN {their} - not this book" if their != self.isbn else "same ISBN (e-book)")
        return got

    def books_search(self, q: str, how: str) -> dict:
        progress.step("market", f"books.com.tw: searching {how} {q}")
        referer = taiwan.BOOKS_SEARCH.format(isbn=quote(q, safe=""))
        page = net.request("books_tw", "GET", referer, memo=True, min_interval=self.cfg.scrape_delay,
                           headers=taiwan._headers(self.cfg, "https://www.books.com.tw/"), expect="text") or ""
        ids = taiwan.parse_books_search(page)              # printed books first, then e-books (E...)
        if not ids:
            if page and net.looks_blocked(page):
                _human_check()
            path = net.save_debug(self.cfg.cache_dir, f"books_tw_search_{q}", page) if page else "empty reply"
            log.info("  books.com.tw q=%s (%s): no products on the result page (saved to %s)", q, how, path)
            return {}
        printed = [i for i in ids if not i.startswith("E")]
        ebooks = [i for i in ids if i.startswith("E")]
        limit = 2 if how in ("isbn", "isbn10") else self.cfg.market_title_pages
        for pid in printed[:limit] + (ebooks[:1] if how in ("isbn", "isbn10") else []):
            got = self.books_product(pid, how, referer)
            if got:
                return got
        log.info("  books.com.tw q=%s (%s): %d product(s) checked, none a printed copy with this ISBN", q, how,
                 min(len(printed), limit))
        return {}

    # ---- web search ------------------------------------------------------------------------------
    def web_results(self, batches, how: str) -> dict:
        k = 0
        for batch in batches:
            for r in batch:
                k += 1
                m_es, m_bk = ESLITE_ID.search(r["url"]), BOOKS_ID.search(r["url"])
                if m_es:
                    got = self.eslite_product(m_es.group(1), how, r["text"])
                elif m_bk:
                    got = self.books_product(m_bk.group(1), how, "https://www.google.com/", r["text"])
                else:
                    log.info("  result %d %s: not a product page", k, r["url"])
                    continue
                if got:
                    return got
        if k:
            log.info("  %s: none of the %d result(s) checked is this ISBN", how, k)
        return {}

    def web_isbn(self, shop: str) -> dict:
        return self.web_results(websearch.searches(self.isbn, SHOP_SITES[shop], self.cfg), "web search")

    def web_title(self) -> dict:
        t = search_title(self.title)
        if not t:
            return {}
        names = author_names(self.author)
        query = f"{t} {names[0]}" if names else t
        sites = [SHOP_SITES[s] for s in self.cfg.market_providers if s in SHOP_SITES]
        return self.web_results(websearch.search_query(query, sites, self.cfg), "web search, title")

    # ---- the order -------------------------------------------------------------------------------
    def steps(self):
        from .lookup import isbn10
        cfg, kinds = self.cfg, self.cfg.market_search
        shops = [s for s in cfg.market_providers if s in SHOP_SITES]
        web = websearch.enabled(cfg)
        for shop in shops:
            for kind in ("isbn", "isbn10"):
                q = self.isbn if kind == "isbn" else isbn10(self.isbn)
                if kind in kinds and q:
                    yield shop, (self.eslite_search if shop == "eslite" else self.books_search), (q, kind)
        if "ncl" in cfg.market_providers:
            yield "ncl", self.ncl, ()
        if web:
            for shop in shops:
                yield "web", self.web_isbn, (shop,)
        yield "fallback", self.take_ebook, ()
        if "title" in kinds and search_title(self.title):
            for shop in shops:
                yield shop, (self.eslite_search if shop == "eslite" else self.books_search), (search_title(self.title),
                                                                                             "title")
        yield "fallback", self.take_similar, ()
        if web and "title" in kinds and cfg.market_similar:
            yield "web", self.web_title, ()
        yield "fallback", self.take_similar, ()

    def take_ebook(self) -> dict:
        if self.ebook:
            log.info("  no printed copy with this ISBN anywhere - using the e-book's price")
        return self.ebook

    def take_similar(self) -> dict:
        best = dict(self.similar)
        if best:
            best.pop("_score", None)
            log.info("  no copy with this ISBN anywhere - using the other edition's price (%s)", best["market_source"])
        return best

    def ncl(self) -> dict:
        progress.step("market", f"ncl: looking up registered price for {self.isbn}")
        got = taiwan.from_ncl(self.isbn, self.cfg)
        price = _price(got.get("list_price"))
        if not price:
            log.info("  ncl: %s", "record found but no price in it" if got else "no record found")
            return {}
        log.info("  ncl: %s TWD", price)
        return self.found(price, "ncl (registered price)",
                          got.get("ncl_url") or f"{self.cfg.ncl_base}/H30_SearchBooks.php?Pact=Search&Pval={self.isbn}")

    def run(self) -> dict:
        for name, fn, args in self.steps():
            if name in ("eslite", "books_tw", "ncl") and net.blocked(name):
                continue   # refused us earlier this run (its pages can still be reached through the web search)
            try:
                got = fn(*args)
            except Exception as exc:   # one source failing must not stop the others
                msg = f"{name}: {net.redact(exc)}"
                if msg not in self.errors:
                    self.errors.append(msg)
                progress.step("market", f"{name}: failed ({net.redact(exc)})")
                continue
            if got:
                got.pop("_year", None)
                return got
        return {}


def _human_check():
    why = "books.com.tw answered with a 'prove you are human' page - not asking it again this run"
    net.block("books_tw", why)
    raise net.ProviderBlocked(why)


def price_from_link(url: str, isbn: str, cfg: Config) -> dict:
    """You found the book yourself (e.g. with a Google search) and pasted the shop's link: read the price from it.
    Accepts https://www.eslite.com/product/<id> and https://www.books.com.tw/products/<id>. The page must be the
    same ISBN. Raises ValueError with a plain message otherwise. The result is cached like a found market price."""
    url = (url or "").strip()
    isbn = normalize(isbn or "") or ""
    if not isbn:
        raise ValueError("Type or fix the ISBN first.")
    m_es = ESLITE_ID.search(url)
    m_bk = re.search(r"books\.com\.tw/(?:[^?#]*/)?products/([0-9A-Z]{10})", url)
    if m_es:
        pid = m_es.group(1)
        prod = taiwan.eslite_product(pid, cfg)
        if not prod:
            raise ValueError("eslite did not return that product - check the link.")
        if isbn not in _isbns_of(prod):
            raise ValueError(f"That eslite page is ISBN {prod.get('isbn13') or prod.get('ean') or '?'}, not {isbn}.")
        price = _price(prod.get("final_price"), prod.get("retail_price"), prod.get("mprice"))
        got = {"market_price": price, "market_currency": "TWD", "market_source": "eslite (your link)", "market_match": "exact", "market_isbn": isbn,
               "market_url": f"https://www.eslite.com/product/{pid}"}
    elif m_bk:
        pid = m_bk.group(1)
        page_url = taiwan.BOOKS_PRODUCT.format(id=pid)
        page = net.request("books_tw", "GET", page_url, headers=taiwan._headers(cfg, "https://www.books.com.tw/"),
                           min_interval=cfg.scrape_delay, expect="text") or ""
        info = taiwan.parse_books_product(page, isbn)
        if not info.get("isbn_on_page"):
            raise ValueError("Could not read that books.com.tw page (it may be blocking automated requests). "
                             "Type the 定價 into 'Market price, new' instead.")
        if info["isbn_on_page"] != isbn:
            raise ValueError(f"That books.com.tw page is ISBN {info['isbn_on_page']}, not {isbn}.")
        price = _price(info.get("list_price"), info.get("sale_price"))
        got = {"market_price": price, "market_currency": "TWD", "market_source": "books_tw (your link)", "market_match": "exact", "market_isbn": isbn,
               "market_url": page_url}
    else:
        raise ValueError("Paste a link like https://www.eslite.com/product/... or https://www.books.com.tw/products/...")
    if not got["market_price"]:
        raise ValueError("Found the page, but no price on it.")
    key = f"market@v{VERSION}" + ("+web" if websearch.enabled(cfg) else "")
    net.Cache(cfg.cache_dir / "lookups.json", cfg.cache_days, cfg.cache_miss_days).put(key, isbn, got)
    return got


# ---- the price printed in the barcode itself (LAST resort) ----------------------------------------
ADDON_CURRENCY = {"0": "GBP", "1": "GBP", "3": "AUD", "4": "NZD", "5": "USD", "6": "CAD"}


def addon_price(addon: str, isbn: str = ""):
    """The small 5-digit barcode to the right of the ISBN barcode (EAN-5 add-on).

    Taiwanese books (ISBN 978-957 / 978-986 / 978-626): the five digits are the price in NEW TAIWAN DOLLARS,
        '00350' -> ('350', 'TWD').
    English-language books: first digit = currency, the rest = price in cents (the book trade's convention),
        '51299' -> ('12.99', 'USD'), '00799' -> ('7.99', 'GBP'), '41999' -> ('19.99', 'NZD').
    Anything else (no ISBN, other countries, 50000 / 59999 / 9xxxx = 'no price given') -> None, because we cannot
    know the currency. This is only used when no shop knows the book (MARKET_PROVIDERS)."""
    from .lookup import is_taiwan
    a = re.sub(r"\D", "", addon or "")
    if len(a) != 5:
        return None
    if isbn and is_taiwan(isbn):
        n = int(a)
        return (str(n), "TWD") if 20 <= n <= 20000 else None
    if a[0] not in ADDON_CURRENCY or (isbn and not isbn.startswith(("9780", "9781", "9798"))):
        return None     # only English-language ISBNs follow the currency-digit convention
    cents = int(a) if a[0] in "01" else int(a[1:])
    if cents == 0 or (a[0] == "5" and a[1:] == "9999"):
        return None
    amount = cents / 100
    return (f"{amount:.2f}", ADDON_CURRENCY[a[0]])


def find_market_price(isbn: str, title: str, cfg: Config, use_cache: bool = True, author: str = "") -> dict:
    """{"market_price": "300", "market_currency": "TWD", "market_source": "eslite (isbn)", "market_url": ...,
        "market_match": "exact" | "ebook" | "similar", "market_isbn": <ISBN of the edition priced>} or {}.
    See Hunt for the order. Every answer is cached; a 'not found' made without a title is not, so it is retried once
    the title is known."""
    isbn = normalize(isbn or "") or ""
    if not isbn or not cfg.market_providers:
        return {}
    cache = net.Cache(cfg.cache_dir / "lookups.json", cfg.cache_days, cfg.cache_miss_days)
    key = f"market@v{VERSION}" + ("+web" if websearch.enabled(cfg) else "")   # adding a key re-checks old misses
    if use_cache:
        hit = cache.get(key, isbn)
        if hit is not None:
            return hit
    hunt = Hunt(isbn, title, author, cfg)
    got = hunt.run()
    if got:
        got.setdefault("market_match", "exact")
        cache.put(key, isbn, got)
        return got
    if title and not hunt.errors:
        cache.put(key, isbn, {})
    return {"_errors": hunt.errors} if hunt.errors else {}


# ---- your price -----------------------------------------------------------------------------------
def round_step(currency: str, cfg: Config) -> float:
    """PRICE_ROUND=TWD:1,NZD:0.5,*:0.5 -> the step for this currency (0 = cents)."""
    steps = {}
    for part in (cfg.price_round or "").replace(";", ",").split(","):
        m = re.fullmatch(r"\s*(?:([A-Za-z*]{1,3})\s*[:=]\s*)?([\d.]+)\s*", part)
        if m:
            steps[(m.group(1) or "*").upper()] = float(m.group(2))
    return steps.get(currency.upper(), steps.get("*", 0.0))


def round_to(amount: float, step: float) -> float:
    if step <= 0:
        return round(amount, 2)
    return max(step, math.floor(amount / step + 0.5) * step)


def suggest_price(market_price, market_currency: str, cfg: Config, match: str = "") -> Optional[dict]:
    """Your price = market price x PRICE_RATIO, in THE SAME CURRENCY as the market price (no conversion),
    rounded to PRICE_ROUND for that currency.
    {"price": "120", "currency": "TWD", "price_basis": "40% of 300 TWD"} or None."""
    try:
        mp = float(market_price)
    except (TypeError, ValueError):
        return None
    if mp <= 0:
        return None
    cur = (market_currency or "TWD").upper()
    amount = round_to(mp * cfg.price_ratio, round_step(cur, cfg))
    text = f"{amount:.2f}".rstrip("0").rstrip(".")
    basis = f"{cfg.price_ratio:.0%} of {market_price} {cur}"
    basis += {"similar": " (other edition)", "ebook": " (e-book price)"}.get(match, "")
    return {"price": text, "currency": cur, "price_basis": basis}


def forget_market(row: dict) -> None:
    """Clear the row's market price, and the price worked out from it (a price you typed or captioned stays)."""
    if (row.get("price_basis") or "") not in ("", "manual", "caption"):
        row["price"], row["price_basis"] = "", ""
    for k in MARKET_FIELDS:
        row[k] = ""


def apply_market(row: dict, cfg: Config, use_cache: bool = True, reprice: bool = False) -> list:
    """Fill the row's market_* fields (if missing) and its price (if empty, or if reprice and the price was auto-set).
    Returns problems as text (never raises)."""
    problems = []
    if (row.get("market_source") or "").startswith("barcode"):
        # an older run read the printed price with the wrong currency: forget it (and the price worked out from it)
        # so the shops get asked first and the printed price is re-read with the right currency
        if (row.get("price_basis") or "") not in ("", "manual", "caption"):
            row["price"], row["price_basis"] = "", ""
        for k in MARKET_FIELDS:
            row[k] = ""
    if not row.get("market_price"):
        got = find_market_price(row.get("isbn13", ""), row.get("title", ""), cfg, use_cache,
                                author=row.get("author", ""))
        problems += got.pop("_errors", [])
        for k in MARKET_FIELDS:
            if got.get(k):
                row[k] = got[k]
    if not row.get("market_price") and "barcode" in cfg.market_providers:   # last resort: the printed price
        printed = addon_price(row.get("barcode_addon", ""), row.get("isbn13", ""))
        if printed:
            row["market_price"], row["market_currency"] = printed
            row["market_source"], row["market_url"] = "barcode (price printed on the book - check it)", ""
            row["market_match"], row["market_isbn"] = "exact", row.get("isbn13", "")
    auto = (row.get("price_basis") or "") not in ("", "manual")
    if cfg.auto_price and row.get("market_price") and (not row.get("price") or (reprice and auto)):
        try:
            s = suggest_price(row["market_price"], row.get("market_currency"), cfg, row.get("market_match", ""))
        except Exception as exc:
            problems.append(f"price: {exc}")
            s = None
        if s:
            row.update(s)
    return problems
