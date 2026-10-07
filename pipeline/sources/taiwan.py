"""Taiwanese book sources. None of them has a public, documented API, so:

eslite    誠品  athena.eslite.com/api/v2/search - the JSON endpoint eslite.com's own search page calls. Unofficial: may change.
books_tw  博客來 search.books.com.tw + www.books.com.tw/products/<id> - HTML scraping. They block obvious bots and
               sometimes non-Taiwan traffic; a 403 switches this provider off for the rest of the run.
ncl       國家圖書館 全國新書資訊網 isbn.ncl.edu.tw - HTML form search by ISBN. Few details, last resort.

Politeness built in: at most one request every SCRAPE_DELAY seconds per site (default 3), results cached for
LOOKUP_CACHE_DAYS, a site that answers 403/429 is not asked again until the next run, and only the 1-2 pages
needed per book are fetched. Check each site's terms of use; keep volumes low (a few hundred books, not bulk crawling).
"""
from __future__ import annotations

import html as htmllib
import re
from typing import Optional

from ..core import net
from ..core.config import Config
from ..core.isbn import normalize

JUNK = ("新功能介紹", "追蹤作者", "修改", "訂閱", "看更多", "加入購物車", "放入購物車")


def _ua(cfg: Config) -> str:
    return cfg.scrape_user_agent or net.BROWSER_UA


def _headers(cfg: Config, referer: str = "", accept: str = "text/html,application/xhtml+xml") -> dict:
    h = {"User-Agent": _ua(cfg), "Accept": accept, "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.6"}
    if referer:
        h["Referer"] = referer
    return h


def _year(s) -> str:
    m = re.search(r"\b(1[5-9]\d\d|20\d\d)\b", str(s or ""))
    return m.group(1) if m else ""


def _text(v) -> str:
    if v is None:
        return ""
    if isinstance(v, (list, tuple)):
        return ", ".join(_text(x) for x in v if _text(x))
    if isinstance(v, dict):
        return _text(v.get("content") or v.get("description") or v.get("text") or "")
    s = re.sub(r"<br\s*/?>", "\n", str(v), flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    s = htmllib.unescape(s)
    return re.sub(r"[ \t　\xa0]+", " ", s).strip()


def _number(text) -> str:
    """'300元' / 'NT$1,200' -> '300' / '1200'; '' if there is no number."""
    m = re.search(r"\d[\d,]*(?:\.\d+)?", str(text or ""))
    return m.group(0).replace(",", "") if m else ""


def _dejunk(s: str) -> str:
    for j in JUNK:
        s = s.replace(j, "")
    s = re.sub(r"\s*,(\s*,)*", ",", s).replace(",", ", ").replace(",  ", ", ")
    return re.sub(r"\s+", " ", s).strip(" ,，、")


# ---- 誠品 eslite --------------------------------------------------------------------------------
ESLITE_SEARCH = "https://athena.eslite.com/api/v2/search"
ESLITE_PRODUCT = "https://athena.eslite.com/api/v1/products/{id}"


def parse_eslite_search(data: dict, isbn: str) -> Optional[dict]:
    """Pick the hit whose isbn/ean matches; prefer printed books over e-books."""
    hits = ((data or {}).get("hits") or {}).get("hit") or []
    matches = []
    for h in hits:
        f = h.get("fields") or {}
        codes = {normalize(str(f.get(k) or "")) for k in ("isbn", "ean", "isbn13", "isbn10")}
        if isbn in codes:
            matches.append(h)
    if not matches:
        return None
    matches.sort(key=lambda h: bool((h.get("fields") or {}).get("is_ebook")))
    return matches[0]


def from_eslite(isbn: str, cfg: Config) -> dict:
    """誠品 eslite. Example (paste into a browser):
        GET https://athena.eslite.com/api/v2/search?q=9789861371955&size=5&start=0
        headers: browser User-Agent, Referer: https://www.eslite.com/
    Reply: {"hits": {"found": "1", "hit": [{"id": "1001136022367152", "fields": {"name": ..., "author": [...],
            "manufacturer": [...], "manufacturer_date": "10/30/2014 00:00:00", "isbn": "9789861371955", ...}}]}}
    Only a hit whose isbn/ean equals our ISBN is used. The genre comes from eslite's category levels
    (level1_name ... level3_name, e.g. 童書 / 兒童文学／橋梁書 / 冒險／驚悚小說); when the search hit doesn't carry them,
    one more request reads them (and the page count) from the product:
        GET https://athena.eslite.com/api/v1/products/1001136022367152
    """
    data = net.request("eslite", "GET", ESLITE_SEARCH, params={"q": isbn, "size": 20, "start": 0}, memo=True,
                       headers=_headers(cfg, "https://www.eslite.com/", "application/json"),
                       min_interval=cfg.scrape_delay)
    hit = parse_eslite_search(data, isbn)
    if not hit:
        return {}
    f = hit.get("fields") or {}
    title = _text(f.get("name"))
    sub = _text(f.get("subtitle") or f.get("sub_title"))
    if sub and sub not in title:
        title = f"{title}: {sub}"
    out = {
        "title": title,
        "author": _text(f.get("author")),
        "publisher": _text(f.get("manufacturer")),
        "year": _year(f.get("manufacturer_date")),
        "genre": eslite_genre(f, title),
    }
    if (cfg.eslite_details or not out["genre"]) and hit.get("id"):
        try:
            out.update({k: v for k, v in eslite_details(str(hit["id"]), cfg).items() if v})
        except net.ProviderBlocked:
            raise
        except Exception:
            pass  # the search result alone is still useful
    return out


def eslite_genre(d: dict, title: str = "") -> str:
    """eslite's category levels -> '童書 > 冒險／驚悚小說' (overarching > most specific)."""
    from .genre import from_parts
    return from_parts([_text(d.get(f"level{i}_name")) for i in (1, 2, 3)], title)


def parse_eslite_product(d: dict) -> dict:
    d = d or {}
    specs = {s.get("tag"): s.get("name", "") for s in d.get("product_specifications") or [] if isinstance(s, dict)}
    pages = re.sub(r"\D", "", str(specs.get("pages") or ""))
    return {"pages": pages, "year": _year(d.get("manufacturer_date")),
            "genre": eslite_genre(d, _text(d.get("name")))}


def eslite_product(product_id: str, cfg: Config) -> dict:
    """The raw product JSON: GET https://athena.eslite.com/api/v1/products/<id>
    (has isbn13 / isbn10 / ean, final_price = 定價 list price, retail_price = sale price, product_button_status)."""
    return net.request("eslite", "GET", ESLITE_PRODUCT.format(id=product_id), memo=True,
                       headers=_headers(cfg, "https://www.eslite.com/", "application/json"),
                       min_interval=cfg.scrape_delay) or {}


def eslite_details(product_id: str, cfg: Config) -> dict:
    return parse_eslite_product(eslite_product(product_id, cfg))


# ---- 博客來 books.com.tw ------------------------------------------------------------------------
BOOKS_SEARCH = "https://search.books.com.tw/search/query/key/{isbn}/cat/all"
BOOKS_PRODUCT = "https://www.books.com.tw/products/{id}"
# product ids appear as /products/0010710103 (also behind /exep/assp.php/... ad redirects), as prod-itemlist-0010710103,
# or as data-pid="0010710103" - accept all of them
_PRODUCT_RE = re.compile(r"(?:/products/|prod-itemlist-|data-pid=[\"']?)([0-9A-Z]{10})\b")


def parse_books_search(page: str) -> list:
    """Product ids in result order, printed books (ids not starting with E = e-book) first."""
    ids = []
    for m in _PRODUCT_RE.finditer(page or ""):
        if m.group(1) not in ids:
            ids.append(m.group(1))
    return sorted(ids, key=lambda i: i.startswith("E"))


def _soup(page: str):
    from bs4 import BeautifulSoup
    return BeautifulSoup(page or "", "html.parser")


def _labelled(lines, label: str) -> str:
    """'作者：岸見一郎' -> '岸見一郎' for the first line that starts with the label."""
    for ln in lines:
        m = re.match(rf"^\s*{label}\s*[：:]\s*(.*)$", ln)
        if m and m.group(1).strip():
            return _dejunk(m.group(1))
    return ""


def parse_books_product(page: str, isbn: str) -> dict:
    soup = _soup(page)
    lines = []
    for li in soup.find_all(["li", "p", "dd", "dt", "span", "div"]):
        if li.find(["li", "div", "ul"]):  # only leaf-ish blocks, so labels and values stay on one line
            continue
        t = li.get_text(" ", strip=True)
        if t:
            lines.append(re.sub(r"\s+", " ", t))
    page_isbn = normalize(_labelled(lines, "ISBN"))
    if isbn and page_isbn and page_isbn != isbn:
        return {}  # the search led to a different edition (isbn="" = return whatever book the page is)
    h1 = soup.find("h1")
    og = soup.find("meta", attrs={"property": "og:title"})
    title = h1.get_text(" ", strip=True) if h1 else (og.get("content", "") if og else "")
    title = re.sub(r"^博客來-", "", title).strip()
    author = _labelled(lines, "作者")
    translator = _labelled(lines, "譯者")
    spec = _labelled(lines, "規格")
    pages = ""
    m = re.search(r"(\d+)\s*頁", spec)
    if m:
        pages = m.group(1)
    fmt = spec.split("/")[0].strip() if spec else ""
    img = soup.find("meta", attrs={"property": "og:image"})
    sale = re.findall(r"(\d[\d,]*)\s*元", _labelled(lines, "優惠價"))
    genre = books_genre(soup, lines, title)
    out = {
        "isbn_on_page": page_isbn or "",
        "list_price": _number(_labelled(lines, "定價")),       # 定價 = the publisher's price for a NEW copy (TWD)
        "sale_price": _number(sale[-1]) if sale else "",       # 優惠價 = books.com.tw's current discounted price
        "title": title,
        "author": author + (f" (譯者 {translator})" if translator and author else ""),
        "publisher": _labelled(lines, "出版社"),
        "year": _year(_labelled(lines, "出版日期")),
        "pages": pages,
        "format": fmt if not re.search(r"\d", fmt) else "",
        "cover_url": img.get("content", "") if img else "",
        "genre": genre,
    }
    return out if out["title"] else {}


def books_genre(soup, lines: list, title: str = "") -> str:
    """books.com.tw: '本書分類：中文書> 童書/青少年文學> 兒童文學' on the product page, else its breadcrumb trail
    (博客來 > 中文書 > 童書/青少年文學 > 兒童文學 > <the book>). Store sections like 中文書 and the book's own title
    are not genres and are left out. ('/' is part of books.com.tw's category names, so it is not a separator.)"""
    from .genre import from_parts, from_path
    text = _labelled(lines, "本書分類")
    if text:
        return from_path(text, sep=r"\s*[>›»＞]\s*", title=title)
    crumbs = [a.get_text(" ", strip=True) for a in soup.select(
        "ul.type04_breadcrumb li, ul.container_24.type04_breadcrumb li, [class*=breadcrumb] li, "
        "[itemtype*=BreadcrumbList] [itemprop=name]")]
    return from_parts(crumbs, title) if crumbs else ""


def from_books_tw(isbn: str, cfg: Config) -> dict:
    """博客來 books.com.tw. Two pages per book:
        GET https://search.books.com.tw/search/query/key/9789861371955/cat/all   -> product ids like 0010654321
        GET https://www.books.com.tw/products/0010654321                          -> title, 作者, 出版社, 出版日期, ISBN, 規格
        headers: browser User-Agent, Accept-Language: zh-TW
    The product page's ISBN must equal ours, otherwise it is a different edition and is skipped.
    """
    page = net.request("books_tw", "GET", BOOKS_SEARCH.format(isbn=isbn), headers=_headers(cfg, "https://www.books.com.tw/"), memo=True,
                       min_interval=cfg.scrape_delay, expect="text")
    for pid in parse_books_search(page or "")[:2]:
        prod = net.request("books_tw", "GET", BOOKS_PRODUCT.format(id=pid), memo=True,
                           headers=_headers(cfg, BOOKS_SEARCH.format(isbn=isbn)),
                           min_interval=cfg.scrape_delay, expect="text")
        got = parse_books_product(prod or "", isbn)
        if got:
            return {k: v for k, v in got.items() if k not in ("isbn_on_page", "list_price", "sale_price")}
    return {}


# ---- 國家圖書館 全國新書資訊網 (NCL ISBN) -------------------------------------------------------------
NCL_COLS = {"title": ("書名", "題名", "Title"), "author": ("作者", "Author"), "publisher": ("出版者", "出版社", "出版機構", "Publisher"),
            "year": ("出版日期", "出版年月", "出版年", "Date"), "format": ("裝訂", "Binding"), "isbn": ("ISBN",),
            "list_price": ("定價", "價格", "Price")}


def parse_ncl(page: str, isbn: str) -> dict:
    soup = _soup(page)
    tables = soup.select("table.table-searchbooks") or soup.find_all("table")
    for table in tables:
        trs = table.find_all("tr")
        if len(trs) < 2:
            continue
        heads = [c.get_text(" ", strip=True) for c in trs[0].find_all(["th", "td"])]
        col = {}
        for key, words in NCL_COLS.items():
            for i, h in enumerate(heads):
                if any(w.lower() in h.lower() for w in words) and key not in col:
                    col[key] = i
        if "title" not in col:
            continue
        for tr in trs[1:]:
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
            if len(cells) <= col["title"]:
                continue
            row_isbns = {normalize(x) for x in re.findall(r"[0-9Xx-]{10,17}", " ".join(cells))}
            if isbn not in row_isbns and "isbn" in col:
                continue
            get = lambda k: cells[col[k]] if k in col and col[k] < len(cells) else ""
            return {"title": _dejunk(get("title")), "author": _dejunk(get("author")),
                    "publisher": _dejunk(get("publisher")), "year": _roc_year(get("year")),
                    "format": _dejunk(get("format")), "list_price": _number(get("list_price")),
                    "isbn_matched": isbn in row_isbns}
    # fallback: a detail-style page with "書名：..." lines
    lines = [re.sub(r"\s+", " ", t) for t in soup.get_text("\n").split("\n") if t.strip()]
    title = _labelled(lines, "書名") or _labelled(lines, "題名")
    if not title:
        return {}
    return {"title": title, "author": _labelled(lines, "作者"), "publisher": _labelled(lines, "出版者") or _labelled(lines, "出版社"),
            "year": _year(_labelled(lines, "出版日期") or _labelled(lines, "出版年月")),
            "list_price": _number(_labelled(lines, "定價"))}


def _roc_year(text: str) -> str:
    """NCL writes dates in the ROC calendar: '115/02' -> '2026', '105/03' -> '2016'. 4-digit years pass through."""
    y = _year(text)
    if y:
        return y
    m = re.match(r"\s*(\d{2,3})\s*[/.年-]", str(text or ""))
    return str(int(m.group(1)) + 1911) if m else ""


_PKEY_RE = re.compile(r"""(main_DisplayRecord[\w.]*\?[^"'\s<>]*Pkey=[^"'\s<>&]+(?:&[^"'\s<>]*)?)""")


def parse_ncl_links(page: str, base: str) -> list:
    """Links to the full records (they carry the price) on a result page, in order."""
    out = []
    for m in _PKEY_RE.finditer(htmllib.unescape(page or "")):
        url = m.group(1)
        url = url if url.startswith("http") else f"{base}/{url.lstrip('./')}"
        url = re.sub(r"&(KeepThis|TB_iframe|width|height)=[^&]*", "", url)
        if url not in out:
            out.append(url)
    return out


def parse_ncl_detail(page: str) -> dict:
    """A full NCL record: label/value rows like 書名 | ... , ISBN(裝訂方式) | 9786264380584 (精裝), 定價 | NT$360."""
    soup = _soup(page)
    fields = {}
    for tr in soup.find_all("tr"):
        cells = tr.find_all(["th", "td"])
        if len(cells) >= 2:
            label = cells[0].get_text(" ", strip=True).rstrip("：:")
            value = cells[1].get_text(" ", strip=True)
            if label and label not in fields:
                fields[label] = value
    if not fields:   # some pages use "label：value" lines instead of a table
        for ln in soup.get_text("\n").split("\n"):
            m = re.match(r"\s*([^：:]{1,12})[：:]\s*(.+)", ln)
            if m and m.group(1).strip() not in fields:
                fields[m.group(1).strip()] = m.group(2).strip()

    def get(*labels):
        for want in labels:
            for k, v in fields.items():
                if want.lower() in k.lower():
                    return v
        return ""
    from .genre import from_subjects
    isbn_text = get("ISBN")
    price_text = get("定價", "價格", "Price")
    pages = re.search(r"\d+", get("頁數", "Pages"))
    binding = re.search(r"\(([^)]+)\)", isbn_text)
    return {"title": _dejunk(get("書名", "題名", "Title")), "author": _dejunk(get("作者", "Author")),
            "publisher": _dejunk(get("出版機構", "出版者", "出版社", "Publisher")),
            "year": _roc_year(get("出版年月", "出版日期", "Publication Date", "Date")),
            "pages": pages.group(0) if pages else "", "format": binding.group(1) if binding else "",
            "genre": from_subjects(get("主題標題", "Subject Heading", "Subject")),
            "isbns": sorted({normalize(x) for x in re.findall(r"[0-9Xx-]{10,17}", isbn_text)} - {None}),
            "list_price": _number(price_text), "price_text": price_text}


NCL_SEARCH = "H30_SearchBooks.php"


def _ncl_record(page: str, isbn: str, base: str, cfg: Config, where: str, strict: bool = False) -> dict:
    """OUR book's record from an NCL result page: open the full records the titles link to (they carry 定價 and
    主題標題) and keep the one listing our ISBN; else the result row itself (title / author / publisher only)."""
    for url in parse_ncl_links(page, base)[:3]:
        detail = net.request("ncl", "GET", url, headers=_headers(cfg, f"{base}/{NCL_SEARCH}"),
                             min_interval=cfg.scrape_delay, expect="text", memo=True) or ""
        d = parse_ncl_detail(detail)
        if isbn in d["isbns"]:
            d["ncl_url"] = url
            return d
    d = parse_ncl_detail(page)                 # a single hit may open the full record straight away
    if isbn in d["isbns"]:
        d["ncl_url"] = where
        return d
    row = parse_ncl(page, isbn)                # the result row: title is plain text (no full record online)
    if strict and not row.get("isbn_matched"):
        return {}                              # a title search: the row must show OUR ISBN
    if row:
        row["ncl_url"] = where
    return row


def from_ncl(isbn: str, cfg: Config, title: str = "") -> dict:
    """國家圖書館 全國新書資訊網 (Taiwan's ISBN agency) - free, no key, knows out-of-print books. Its catalogue search
    returns a row per book (書名, 作者, 出版者...). When the title is a link, the full record behind it also has the
    price the publisher registered (定價, e.g. NT$360) and the subject heading (主題標題, used as the genre).
    Searches, until one finds a record listing OUR ISBN:
        1. quick search:  GET {base}/H30_SearchBooks.php?Pact=Search&Pval=9789869283533
        2. catalogue search by the ISBN field:
                          POST {base}/H30_SearchBooks.php?Pact=DisplayAll4Simple
                          FO_SearchField0=ISBN&FO_SearchValue0=9789869283533&FB_clicked=FB_開始查詢
        3. quick search by the title (when known) - the record must still list our ISBN
        full record:      GET {base}/main_DisplayRecord_Popup.php?Pact=view&Pkey=1050307*0123
    A search that finds nothing is saved to data/cache/debug (send it if NCL keeps missing a book you can find there)."""
    base = cfg.ncl_base
    hdr = _headers(cfg, base + "/")
    searches = [("isbn", "GET", f"{base}/{NCL_SEARCH}", {"params": {"Pact": "Search", "Pval": isbn}}),
                ("catalogue", "POST", f"{base}/{NCL_SEARCH}?Pact=DisplayAll4Simple",
                 {"data": {"FO_SearchField0": "ISBN", "FO_SearchValue0": isbn, "FO_SchRe1ation0": "AND",
                           "FB_clicked": "FB_開始查詢", "FB_pageSID": "Simple"}})]
    t = re.split(r"\s*[:：(（]", title or "")[0].strip()
    if t:
        searches.append(("title", "GET", f"{base}/{NCL_SEARCH}", {"params": {"Pact": "Search", "Pval": t}}))
    last = ""
    for how, method, url, kw in searches:
        try:
            page = net.request("ncl", method, url, headers=hdr, min_interval=cfg.scrape_delay, expect="text",
                               memo=method == "GET", **kw) or ""
        except net.ProviderBlocked:
            raise
        except Exception:
            continue
        where = f"{base}/{NCL_SEARCH}?Pact=Search&Pval={isbn}"
        got = _ncl_record(page, isbn, base, cfg, where, strict=how == "title")
        if got:
            return got
        last = page
    if last:
        net.save_debug(cfg.cache_dir, f"ncl_search_{isbn}", last)
    return {}
