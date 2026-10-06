"""Book-data providers, with the network replaced by canned answers (no quota used by tests)."""
import json

import pytest

from pipeline.sources import lookup, taiwan
from pipeline.core import net
from pipeline.core.config import Config

TW = "9789861371955"     # 被討厭的勇氣 (Taiwan ISBN)
EN = "9781408703748"

ESLITE_SEARCH = {"hits": {"start": 0, "found": "2", "hit": [
    {"id": "999", "fields": {"name": "被討厭的勇氣 (電子書)", "is_ebook": True, "isbn": TW, "ean": TW}},
    {"id": "1001136022367152", "fields": {
        "is_ebook": False, "name": "被討厭的勇氣: 自我啟發之父阿德勒的教導", "description": "★讓人生為之一變的全新經典\n所謂的自由",
        "product_photo_url": "/upload/product/o/2680913219003/ec989808.jpg", "isbn": TW, "isbn10": "9861371958",
        "ean": TW, "author": ["岸見一郎/ 古賀史健"], "manufacturer": ["究竟出版社股份有限公司"],
        "manufacturer_date": "10/30/2014 00:00:00", "subtitle": "", "translator": "葉小燕"}}]}}
ESLITE_PRODUCT = {"product_specifications": [
    {"name": "304", "tag_name": "頁數", "tag": "pages"}, {"name": "P:平裝", "tag_name": "裝訂", "tag": "coverType"}],
    "photos": [{"large_path": "https://s2.eslite.com/unsafe/fit-in/x900/s.eslite.com/upload/x.jpg"}],
    "manufacturer_date": "2014-10-30T00:00:00.000+08:00"}

BOOKS_SEARCH_HTML = """<html><body><div class="table-searchbox">
<div class="table-td" id="prod-itemlist-E050000001"><h4><a href="//www.books.com.tw/products/E050000001?loc=P_0001_001" title="被討厭的勇氣(電子書)">x</a></h4></div>
<div class="table-td" id="prod-itemlist-0010654321"><h4><a href="//www.books.com.tw/products/0010654321?loc=P_0001_002" title="被討厭的勇氣">x</a></h4></div>
</div></body></html>"""
BOOKS_PRODUCT_HTML = """<html><head><meta property="og:title" content="博客來-被討厭的勇氣">
<meta property="og:image" content="https://im2.book.com.tw/image/getImage?i=https://www.books.com.tw/img/001/065/43/0010654321.jpg"></head>
<body><div class="mod type02_p002 clearfix"><h1>被討厭的勇氣：自我啟發之父「阿德勒」的教導</h1></div>
<div class="type02_p003 clearfix"><ul>
<li>作者： <a href="#">岸見一郎</a>, <a href="#">古賀史健</a> <a class="type02_btn09">新功能介紹</a></li>
<li>譯者： <a href="#">葉小燕</a></li>
<li>出版社：<a href="#"><span>究竟</span></a> <a class="type02_btn09">新功能介紹</a></li>
<li>出版日期：2014/10/30</li><li>語言：繁體中文</li></ul></div>
<div class="mod_b type02_m057 clearfix"><div class="bd"><ul>
<li>ISBN：9789861371955</li><li>叢書系列：<a>心理</a></li>
<li>規格：平裝 / 336頁 / 14.8 x 21 x 1.69 cm / 普通級 / 單色印刷 / 初版</li><li>出版地：台灣</li></ul></div></div>
<div class="mod_b type02_m057 clearfix"><h3>內容簡介</h3><div class="bd"><div class="content">所謂的自由，就是被別人討厭。<br>有人討厭你。</div></div></div>
</body></html>"""
NCL_HTML = """<html><body><table class="table-searchbooks">
<tr><th>序號</th><th>書名</th><th>作者</th><th>出版者</th><th>ISBN</th><th>裝訂</th><th>出版日期</th></tr>
<tr><td>1</td><td>被討厭的勇氣 : 自我啟發之父阿德勒的教導</td><td>岸見一郎, 古賀史健著</td><td>究竟</td>
<td>978-986-137-195-5</td><td>平裝</td><td>2014/10</td></tr></table></body></html>"""
GOOGLE = {"totalItems": 2, "items": [
    {"id": "x", "volumeInfo": {"title": "Wrong book", "industryIdentifiers": [{"type": "ISBN_13", "identifier": "9780000000002"}]}},
    {"id": "y", "volumeInfo": {"title": "被討厭的勇氣", "authors": ["岸見一郎", "古賀史健"], "publisher": "究竟",
                               "publishedDate": "2014-10-30", "pageCount": 336,
                               "industryIdentifiers": [{"type": "ISBN_10", "identifier": "9861371958"}],
                               "imageLinks": {"thumbnail": "http://books.google.com/x.jpg"}}}]}


class Resp:
    def __init__(self, status=200, body=None, text=None, headers=None):
        self.status_code, self._body, self.headers = status, body, headers or {}
        self.text = text if text is not None else json.dumps(body)
        self.encoding, self.apparent_encoding = "utf-8", "utf-8"

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


class FakeSession:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def request(self, method, url, **kw):
        self.calls.append((method, url, kw))
        for part, resp in self.routes.items():
            if part in url:
                return resp(method, url, kw) if callable(resp) else resp
        return Resp(404, {})


@pytest.fixture
def fake(monkeypatch):
    def install(routes):
        s = FakeSession(routes)
        monkeypatch.setattr(net, "_SESSION", s)
        monkeypatch.setattr(net, "_LAST_CALL", {})
        monkeypatch.setattr(net, "_MEMO", {})
        net.reset_blocks()
        return s
    yield install
    net.reset_blocks()


def cfg_for(tmp_path, **kw):
    base = dict(data_dir=tmp_path, google_books_key="SECRET123", scrape_delay=0, contact_email="me@x.nz",
                google_queries=("isbn", "isbn10", "plain"))  # search steps; the 'id' step has its own tests below
    base.update(kw)
    return Config(**base)


def test_taiwan_isbn_routing():
    assert lookup.is_taiwan("9789571374632") and lookup.is_taiwan("9786263101128") and lookup.is_taiwan(TW)
    assert not lookup.is_taiwan(EN)


def test_eslite_parsing_prefers_printed_book(tmp_path, fake):
    s = fake({"athena.eslite.com/api/v2/search": Resp(200, ESLITE_SEARCH),
              "athena.eslite.com/api/v1/products/1001136022367152": Resp(200, ESLITE_PRODUCT)})
    got = taiwan.from_eslite(TW, cfg_for(tmp_path, eslite_details=True))
    assert got["title"] == "被討厭的勇氣: 自我啟發之父阿德勒的教導"
    assert got["author"] == "岸見一郎/ 古賀史健" and got["publisher"] == "究竟出版社股份有限公司"
    assert got["year"] == "2014" and got["pages"] == "304" and got["format"] == "平裝"
    assert got["cover_url"].startswith("https://")
    assert len(s.calls) == 2


def test_books_tw_parsing(tmp_path, fake):
    fake({"search.books.com.tw": Resp(200, text=BOOKS_SEARCH_HTML),
          "www.books.com.tw/products/0010654321": Resp(200, text=BOOKS_PRODUCT_HTML)})
    got = taiwan.from_books_tw(TW, cfg_for(tmp_path))
    assert got["title"].startswith("被討厭的勇氣")
    assert got["author"].startswith("岸見一郎, 古賀史健")
    assert "新功能介紹" not in got["author"] and "譯者 葉小燕" in got["author"]
    assert got["publisher"] == "究竟" and got["year"] == "2014"
    assert got["pages"] == "336" and got["format"] == "平裝"
    assert "description" not in got and got["cover_url"].startswith("https://im2.book.com.tw")


def test_books_tw_rejects_other_edition(tmp_path):
    assert taiwan.parse_books_product(BOOKS_PRODUCT_HTML, "9789571374632") == {}


def test_ncl_table_parsing():
    got = taiwan.parse_ncl(NCL_HTML, TW)
    assert got["title"].startswith("被討厭的勇氣") and got["publisher"] == "究竟" and got["year"] == "2014"
    assert taiwan.parse_ncl(NCL_HTML, "9789571374632") == {}


def test_google_key_in_header_not_url_and_quota_counted(tmp_path, fake):
    s = fake({"googleapis.com/books": Resp(200, GOOGLE)})
    cfg = cfg_for(tmp_path)
    got = lookup.from_google(TW, cfg)
    assert got["title"] == "被討厭的勇氣" and got["pages"] == 336 and got["cover_url"].startswith("https://")
    method, url, kw = s.calls[0]
    assert "SECRET123" not in url and "SECRET123" not in json.dumps(kw["params"])
    assert kw["headers"]["x-goog-api-key"] == "SECRET123"
    assert "country" not in kw["params"] and "fields" in kw["params"]
    assert net.DailyCounter(cfg.cache_dir / "quota.json").used("google") == 1


def test_google_daily_limit_stops_calls(tmp_path, fake):
    s = fake({"googleapis.com/books": Resp(200, GOOGLE)})
    cfg = cfg_for(tmp_path, google_daily_limit=2, providers=("google",), cache_days=0, cache_miss_days=0)
    for _ in range(3):
        lookup.lookup_book(EN, cfg)
    r = lookup.lookup_book(EN, cfg)
    assert len(s.calls) == 2
    assert any("daily limit" in e for e in r["_errors"])


def test_cache_means_second_run_costs_nothing(tmp_path, fake):
    s = fake({"googleapis.com/books": Resp(200, GOOGLE)})
    cfg = cfg_for(tmp_path, providers=("google",))
    a = lookup.lookup_book(TW, cfg_for(tmp_path, providers_tw=("google",)))
    b = lookup.lookup_book(TW, cfg_for(tmp_path, providers_tw=("google",)))
    assert a["title"] == b["title"] == "被討厭的勇氣" and len(s.calls) == 1
    assert cfg.cache_dir.joinpath("lookups.json").exists()


def test_stop_when_skips_later_providers(tmp_path, fake):
    s = fake({"athena.eslite.com": Resp(200, ESLITE_SEARCH), "googleapis.com": Resp(200, GOOGLE)})
    cfg = cfg_for(tmp_path, providers_tw=("eslite", "google"), stop_when=("title", "author", "publisher", "year"))
    r = lookup.lookup_book(TW, cfg)
    assert r["source"] == "eslite" and not any("googleapis" in c[1] for c in s.calls)


def test_403_blocks_provider_for_the_run(tmp_path, fake):
    s = fake({"search.books.com.tw": Resp(403, text="Forbidden"), "athena.eslite.com": Resp(200, {"hits": {"hit": []}})})
    cfg = cfg_for(tmp_path, providers_tw=("books_tw", "eslite"), cache_miss_days=0)
    r1 = lookup.lookup_book(TW, cfg)
    r2 = lookup.lookup_book("9789571374632", cfg)
    assert sum("books.com.tw" in c[1] for c in s.calls) == 1   # not hammered after the 403
    assert any("books_tw" in e for e in r1["_errors"]) and any("books_tw" in e for e in r2["_errors"])


def test_429_retries_then_gives_up(tmp_path, fake, monkeypatch):
    monkeypatch.setattr(net.time, "sleep", lambda s: None)
    s = fake({"openlibrary.org": Resp(429, {}, headers={"Retry-After": "1"})})
    with pytest.raises(net.ProviderBlocked):
        lookup.from_openlibrary(EN, cfg_for(tmp_path))
    assert len(s.calls) == 3


def test_openlibrary_identifies_itself(tmp_path, fake):
    s = fake({"openlibrary.org/api/books": Resp(200, {f"ISBN:{EN}": {"title": "T", "authors": [{"name": "A"}]}})})
    assert lookup.from_openlibrary(EN, cfg_for(tmp_path))["title"] == "T"
    assert "me@x.nz" in s.calls[0][2]["headers"]["User-Agent"]


def test_redact():
    assert "abc" not in net.redact("403 for url: https://x/y?q=1&key=abc")


# What Google really returned for q=9781451648546 (Steve Jobs): books that only MENTION the number in their text
JOBS = "9781451648546"
GOOGLE_PLAIN_JUNK = {"totalItems": 10, "items": [
    {"id": "nvgdAwAAQBAJ", "volumeInfo": {"title": "Steve Jobs - 101 Amazing Facts You Didn't Know", "authors": ["G Whiz"],
     "industryIdentifiers": [{"type": "ISBN_13", "identifier": "9781497732032"}, {"type": "ISBN_10", "identifier": "1497732034"}]}},
    {"id": "zR4rEAAAQBAJ", "volumeInfo": {"title": "Die Filmindustrie der Vereinigten Staaten", "authors": ["Vasil Teigens"]}},
]}
GOOGLE_PLAIN_GOOD = {"totalItems": 3, "items": GOOGLE_PLAIN_JUNK["items"] + [
    {"id": "8U2oAAAAQBAJ", "volumeInfo": {"title": "Steve Jobs", "authors": ["Walter Isaacson"], "publisher": "Simon and Schuster",
     "publishedDate": "2011-10-24", "industryIdentifiers": [{"type": "ISBN_13", "identifier": JOBS}]}}]}


def _google_by_query(answers):
    """answers: {'isbn:...' or plain isbn: reply}"""
    def route(method, url, kw):
        return Resp(200, answers.get(kw["params"]["q"], {"kind": "books#volumes", "totalItems": 0}))
    return route


def test_google_isbn_search_empty_falls_back_and_filters_by_identifier(tmp_path, fake):
    s = fake({"googleapis.com/books": _google_by_query({JOBS: GOOGLE_PLAIN_GOOD})})
    cfg = cfg_for(tmp_path)
    got = lookup.from_google(JOBS, cfg)
    assert got["title"] == "Steve Jobs" and got["author"] == "Walter Isaacson"
    assert [c[2]["params"]["q"] for c in s.calls] == [f"isbn:{JOBS}", "isbn:1451648545", JOBS]
    assert net.DailyCounter(cfg.cache_dir / "quota.json").used("google") == 3   # every attempt is counted


def test_google_plain_search_never_takes_a_book_that_only_mentions_the_isbn(tmp_path, fake):
    fake({"googleapis.com/books": _google_by_query({JOBS: GOOGLE_PLAIN_JUNK})})
    assert lookup.from_google(JOBS, cfg_for(tmp_path)) == {}


def test_google_stops_at_first_hit_and_queries_are_configurable(tmp_path, fake):
    s = fake({"googleapis.com/books": _google_by_query({f"isbn:{JOBS}": GOOGLE_PLAIN_GOOD, JOBS: GOOGLE_PLAIN_GOOD})})
    assert lookup.from_google(JOBS, cfg_for(tmp_path))["title"] == "Steve Jobs" and len(s.calls) == 1
    s = fake({"googleapis.com/books": _google_by_query({JOBS: GOOGLE_PLAIN_GOOD})})
    assert lookup.from_google(JOBS, cfg_for(tmp_path, google_queries=("plain",)))["title"] == "Steve Jobs"
    assert len(s.calls) == 1


def test_old_cached_google_misses_are_ignored(tmp_path, fake):
    cfg = cfg_for(tmp_path, providers=("google",))
    net.Cache(cfg.cache_dir / "lookups.json", 180, 7).put("google", JOBS, {})   # 'not found' saved by the old version
    fake({"googleapis.com/books": _google_by_query({JOBS: GOOGLE_PLAIN_GOOD})})
    assert lookup.lookup_book(JOBS, cfg)["title"] == "Steve Jobs"


# ---- the 'id' step: ISBN -> volume id via Dynamic Links (free), then volumes/{id} -------------------------
LINKS_REPLY = ('cb({"ISBN:9781451648546":{"bib_key":"ISBN:9781451648546","info_url":'
               '"https://books.google.com/books?id=8U2oAAAAQBAJ\\u0026source=gbs_ViewAPI","preview":"partial",'
               '"thumbnail_url":"https://books.google.com/books/content?id=8U2oAAAAQBAJ\\u0026printsec=frontcover"}});')
VOLUME = {"id": "8U2oAAAAQBAJ", "volumeInfo": {
    "title": "Steve Jobs", "authors": ["Walter Isaacson"], "publisher": "Simon and Schuster", "publishedDate": "2011",
    "pageCount": 630, "industryIdentifiers": [{"type": "ISBN_10", "identifier": "1451648545"},
                                              {"type": "ISBN_13", "identifier": JOBS}],
    "imageLinks": {"thumbnail": "http://books.google.com/t.jpg", "large": "http://books.google.com/l.jpg"}}}


def test_parse_dynamic_links():
    assert lookup.parse_google_links(LINKS_REPLY, JOBS) == "8U2oAAAAQBAJ"
    assert lookup.parse_google_links("cb({});", JOBS) == ""
    assert lookup.parse_google_links("<html>captcha</html>", JOBS) == ""


def test_google_id_step_finds_book_the_isbn_search_misses(tmp_path, fake):
    s = fake({"books.google.com/books": Resp(200, text=LINKS_REPLY),
              "googleapis.com/books/v1/volumes/8U2oAAAAQBAJ": Resp(200, VOLUME),
              "googleapis.com/books/v1/volumes": Resp(200, {"kind": "books#volumes", "totalItems": 0})})
    cfg = cfg_for(tmp_path, google_queries=("id", "isbn"))
    got = lookup.from_google(JOBS, cfg)
    assert got["title"] == "Steve Jobs" and got["pages"] == 630 and got["cover_url"] == "https://books.google.com/l.jpg"
    links_call, api_call = s.calls
    assert links_call[2]["params"]["bibkeys"] == f"ISBN:{JOBS}" and "x-goog-api-key" not in links_call[2]["headers"]
    assert api_call[1].endswith("/volumes/8U2oAAAAQBAJ") and api_call[2]["headers"]["x-goog-api-key"] == "SECRET123"
    assert net.DailyCounter(cfg.cache_dir / "quota.json").used("google") == 1


def test_google_id_step_costs_no_quota_when_google_has_no_such_book(tmp_path, fake):
    s = fake({"books.google.com/books": Resp(200, text="cb({});")})
    cfg = cfg_for(tmp_path, google_queries=("id",))
    assert lookup.from_google(JOBS, cfg) == {}
    assert len(s.calls) == 1 and net.DailyCounter(cfg.cache_dir / "quota.json").used("google") == 0


def test_google_id_step_blocked_falls_back_to_search(tmp_path, fake):
    fake({"books.google.com/books": Resp(403, text="no"),
              "googleapis.com/books/v1/volumes": Resp(200, GOOGLE_PLAIN_GOOD)})
    got = lookup.from_google(JOBS, cfg_for(tmp_path, google_queries=("id", "isbn")))
    assert got["title"] == "Steve Jobs" and not net.blocked("google")


def test_default_steps_are_id_then_isbn(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for k in ("GOOGLE_STEPS", "GOOGLE_QUERIES", "BOOK_PROVIDERS", "BOOK_PROVIDERS_TW"):
        monkeypatch.delenv(k, raising=False)
    assert Config.from_env().google_queries == ("id", "isbn")


def test_parse_real_dynamic_links_reply_saved_from_chrome():
    from pathlib import Path
    text = (Path(__file__).parent / "google_links_9781451648546.txt").read_text(encoding="utf-8")
    assert lookup.parse_google_links(text, JOBS) == "8U2oAAAAQBAJ"


def test_taiwanese_isbn_gets_the_chinese_title_not_googles_english_one(tmp_path):
    from pipeline.sources.lookup import lookup_book
    calls = []
    providers = {
        "google": lambda isbn, cfg: calls.append("google") or {"title": "A Ghost Tale for Christmas Time",
                                                                "author": "Mary P. Osborne", "publisher": "Commonwealth",
                                                                "year": "2011", "pages": 240},
        "ncl": lambda isbn, cfg: calls.append("ncl") or {"title": "神奇樹屋. 44, 狄更斯的耶誕頌",
                                                          "author": "瑪麗.波.奧斯本", "publisher": "天下遠見"},
    }
    cfg = cfg_for(tmp_path, providers_tw=("google", "ncl"), stop_when=("title", "author", "publisher", "year"))
    got = lookup_book("9789862167557", cfg, providers=providers)
    assert calls == ["google", "ncl"]                         # google had everything, but only in English
    assert got["title"] == "神奇樹屋. 44, 狄更斯的耶誕頌" and got["author"] == "瑪麗.波.奧斯本"
    assert got["title_other"] == "A Ghost Tale for Christmas Time" and got["publisher"] == "Commonwealth"
    calls.clear()
    lookup_book("9781862305717", cfg_for(tmp_path / "en", providers=("google", "ncl"),
                                         stop_when=("title", "author", "publisher", "year")), providers=providers)
    assert calls == ["google"]                                # English book: English title is right


def test_titles_command_fixes_old_rows(tmp_path):
    from pipeline.photos.ingest import chinese_titles
    cfg = cfg_for(tmp_path, market_providers=())
    rows = [{"sku": "a", "isbn13": "9789862167557", "title": "A Ghost Tale for Christmas Time",
             "author": "Mary P. Osborne", "status": "enriched"},
            {"sku": "b", "isbn13": "9789862164914", "title": "月光下的魔笛", "author": "x", "status": "validated"}]
    changed = chinese_titles(rows, cfg, lookup=lambda isbn, c: {"title": "狄更斯的耶誕頌", "author": "瑪麗.波.奧斯本"})
    assert changed == ["a"] and rows[0]["title"] == "狄更斯的耶誕頌" and rows[0]["author"] == "瑪麗.波.奧斯本"
    assert "English title: A Ghost Tale for Christmas Time" in rows[0]["notes"]
