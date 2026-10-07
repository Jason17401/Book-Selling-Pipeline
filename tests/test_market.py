"""Market price of a new copy (eslite / books.com.tw), and your price from it. Network replaced by canned answers."""
import pytest

from pipeline.sources import market
from pipeline.core import net, store
from pipeline.core.config import Config, _ratio
from test_lookup import Resp, fake  # noqa: F401  (fixture)

ISBN = "9789861371955"
TITLE = "被討厭的勇氣: 自我啟發之父阿德勒的教導"

ESLITE_TITLE_HITS = {"hits": {"found": "3", "hit": [
    {"id": "1001136022547416", "fields": {"name": "被討厭的勇氣 二部曲完結篇", "isbn": "9789861372273", "mprice": "320",
                                          "final_price": "252", "is_ebook": False}},
    {"id": "10072302132682680026006", "fields": {"name": "被討厭的勇氣 (電子書)", "isbn": ISBN, "mprice": "179",
                                                 "final_price": "157", "is_ebook": True}},
    {"id": "1001136022367152", "fields": {"name": TITLE, "isbn": ISBN, "isbn10": "9861371958", "ean": ISBN,
                                          "mprice": "300", "final_price": "237", "is_ebook": False}},
]}}


def product_page(isbn, list_price="300", sale="237"):
    return f"""<html><head><meta property="og:title" content="博客來-被討厭的勇氣"></head><body>
    <h1>被討厭的勇氣</h1><ul><li>作者：<a>岸見一郎</a></li><li>出版社：<a>究竟</a></li></ul>
    <ul class="price"><li>定價：<em>{list_price}</em>元</li><li>優惠價：<strong><b>79</b></strong>折<strong><b>{sale}</b></strong>元</li></ul>
    <ul><li>ISBN：{isbn}</li><li>規格：平裝 / 336頁</li></ul></body></html>"""


SEARCH_TWO = """<a href="//www.books.com.tw/products/0010000001?loc=1">x</a>
<a href="//www.books.com.tw/products/E050000001?loc=2">ebook</a>
<a href="//www.books.com.tw/products/0010654321?loc=3">y</a>"""


def cfg_for(tmp_path, **kw):
    base = dict(data_dir=tmp_path, scrape_delay=0, price_ratio=0.4, market_providers=("eslite", "books_tw"))
    base.update(kw)
    return Config(**base)


def test_eslite_finds_by_title_when_isbn_search_is_empty(tmp_path, fake):  # noqa: F811
    def route(method, url, kw):
        return Resp(200, ESLITE_TITLE_HITS if kw["params"]["q"] == "被討厭的勇氣" else {"hits": {"hit": []}})
    s = fake({"athena.eslite.com": route})
    got = market.find_market_price(ISBN, TITLE, cfg_for(tmp_path, market_providers=("eslite",)))
    assert got["market_price"] == "300" and got["market_currency"] == "TWD"             # list price, not the e-book
    assert got["market_source"] == "eslite (title)" and got["market_url"].endswith("1001136022367152")
    assert [c[2]["params"]["q"] for c in s.calls] == [ISBN, "9861371958", "被討厭的勇氣"]


def test_books_tw_checks_isbn_on_each_product_page(tmp_path, fake):  # noqa: F811
    routes = {
        "search.books.com.tw/search/query/key/9789861371955": Resp(200, text="<html>no results</html>"),
        "search.books.com.tw/search/query/key/9861371958": Resp(200, text="<html>no results</html>"),
        "search.books.com.tw/search/query/key/%E8%A2%AB": Resp(200, text=SEARCH_TWO),
        "products/0010000001": Resp(200, text=product_page("9789861372273", "320", "252")),   # other edition
        "products/0010654321": Resp(200, text=product_page(ISBN)),
    }
    s = fake(routes)
    got = market.find_market_price(ISBN, TITLE, cfg_for(tmp_path, market_providers=("books_tw",)))
    assert got["market_price"] == "300" and got["market_source"] == "books_tw (title)"
    assert not any("E050000001" in c[1] for c in s.calls)                               # e-book never opened


def test_not_found_is_cached_only_once_the_title_is_known(tmp_path, fake):  # noqa: F811
    s = fake({"athena.eslite.com": Resp(200, {"hits": {"hit": []}})})
    cfg = cfg_for(tmp_path, market_providers=("eslite",))
    assert market.find_market_price(ISBN, "", cfg) == {}
    n = len(s.calls)
    net._MEMO.clear()                                  # a new run
    market.find_market_price(ISBN, "", cfg)
    assert len(s.calls) == 2 * n                       # no title yet: asked again
    market.find_market_price(ISBN, TITLE, cfg)
    m = len(s.calls)
    net._MEMO.clear()
    market.find_market_price(ISBN, TITLE, cfg)
    assert len(s.calls) == m                           # with a title: the 'not found' is remembered


def test_suggested_price_keeps_the_market_currency(tmp_path):
    cfg = cfg_for(tmp_path)
    assert market.suggest_price("300", "TWD", cfg) == {"price": "120", "currency": "TWD", "price_basis": "40% of 300 TWD"}
    assert market.suggest_price("299", "TWD", cfg)["price"] == "120"          # 119.6 -> whole TWD
    assert market.suggest_price("17", "NZD", cfg) == {"price": "7", "currency": "NZD", "price_basis": "40% of 17 NZD"}
    assert market.suggest_price("18", "NZD", cfg)["price"] == "7"             # 7.2 -> nearest 0.5
    assert market.suggest_price("300", "TWD", cfg_for(tmp_path, price_round="0"))["price"] == "120"
    assert market.suggest_price("301", "TWD", cfg_for(tmp_path, price_round="0"))["price"] == "120.4"


def test_apply_market_never_overwrites_your_price(tmp_path, monkeypatch):
    monkeypatch.setattr(market, "find_market_price", lambda *a, **k: {
        "market_price": "300", "market_currency": "TWD", "market_source": "eslite (isbn)", "market_url": "u"})
    cfg = cfg_for(tmp_path)
    auto = {"isbn13": ISBN, "title": TITLE}
    market.apply_market(auto, cfg)
    assert auto["price"] == "120" and auto["currency"] == "TWD" and auto["price_basis"] == "40% of 300 TWD"
    mine = {"isbn13": ISBN, "title": TITLE, "price": "9", "currency": "NZD", "price_basis": "manual"}
    market.apply_market(mine, cfg, reprice=True)
    assert mine["price"] == "9" and mine["currency"] == "NZD" and mine["market_price"] == "300"
    market.apply_market(auto, cfg_for(tmp_path, price_ratio=0.5), reprice=True)   # ratio changed: auto price follows
    assert auto["price"] == "150"


def test_ratio_formats():
    assert _ratio("0.4") == _ratio("40%") == _ratio("40") == 0.4


def test_review_window_actions(tmp_path):
    from pipeline.apps import editing
    cfg = cfg_for(tmp_path)
    store.write_rows(cfg.csv_path, [{"sku": "s1", "isbn13": ISBN, "title": "T", "author": "A", "price": "120",
                                     "currency": "TWD", "price_basis": "40% of 300 TWD"}])
    assert editing.suggestion(cfg, "500", "TWD") == {"price": "200", "currency": "TWD", "price_basis": "40% of 500 TWD"}
    r = editing.save_row(cfg, "s1", {"price": "7"})
    assert r["price_basis"] == "manual"
    r = editing.save_row(cfg, "s1", {"market_price": "NT$1,200"})
    assert r["market_price"] == "1200" and r["market_currency"] == "TWD"


@pytest.mark.parametrize("bad", ["abc"])
def test_bad_market_price_is_flagged(tmp_path, bad):
    from pipeline.core.validate import row_issues
    issues = row_issues({"isbn13": ISBN, "title": "T", "author": "A", "barcode_photo": "", "market_price": bad},
                        cfg_for(tmp_path))
    assert any(i["field"] == "market_price" for i in issues)


def test_ingest_fills_market_price_and_your_price(tmp_path, monkeypatch):
    from pipeline.photos.setlayout import process_sets
    from synth import TW_ISBNS, book_back, set_front
    monkeypatch.setattr(market, "find_market_price", lambda isbn, title, cfg, use_cache=True, author="": {
        "market_price": "450", "market_currency": "TWD", "market_source": "eslite (title)", "market_url": "u"})
    cfg = cfg_for(tmp_path / "d", set_size=2, grid=(1, 2), settle_seconds=0)
    cfg.ensure_dirs()
    set_front(2, (1, 2)).save(cfg.inbox / "0001.jpg")
    for i in range(2):
        book_back(TW_ISBNS[i], size=(900, 1200), module=3.0, seed=i).save(cfg.inbox / f"{i + 2:04d}.jpg")
    (cfg.inbox / "0003.txt").write_text("good 3", encoding="utf-8")       # book 2 has its own price in the caption
    process_sets(cfg, lookup=lambda i, c: {"title": "T", "author": "A"})
    rows = store.read_rows(cfg.csv_path)
    assert rows[0]["market_price"] == "450" and rows[0]["price"] == "180" and rows[0]["currency"] == "TWD"
    assert rows[1]["market_price"] == "450" and rows[1]["price"] == "3" and rows[1]["currency"] == "TWD"
    assert rows[1]["price_basis"] == "caption"                           # your caption price is kept as given
    assert rows[0]["status"] == rows[1]["status"] == "to_check"


# ---- out-of-print books: the shop's search leaves them out, a web search finds the page ------------------------
OOP = "9789869283533"   # 2016 edition, out of print: eslite's search only returns the 2020 edition
ESLITE_OOP_PRODUCT = {"name": "Google必修的圖表簡報術", "isbn13": OOP, "isbn10": "9869283535", "ean": OOP,
                      "final_price": 420, "retail_price": 331.0, "product_button_status": "out_of_print"}
WEB_REPLY = {"results": [
    {"url": "https://www.eslite.com/product/1001247312874726", "title": "2020 edition"},
    {"url": "https://www.eslite.com/product/1001247312496322", "title": "2016 edition"},
    {"url": "https://www.books.com.tw/products/0010710103?loc=P_036_002", "title": "博客來"}]}


def test_eslite_out_of_print_found_through_web_search(tmp_path, fake):  # noqa: F811
    s = fake({"athena.eslite.com/api/v2/search": Resp(200, {"hits": {"found": "0", "hit": []}}),
              "api.tavily.com/search": Resp(200, WEB_REPLY),
              "athena.eslite.com/api/v1/products/1001247312874726": Resp(200, {"isbn13": "9789865519216", "final_price": 420}),
              "athena.eslite.com/api/v1/products/1001247312496322": Resp(200, ESLITE_OOP_PRODUCT)})
    cfg = cfg_for(tmp_path, market_providers=("eslite",), tavily_api_key="TVKEY")
    got = market.find_market_price(OOP, "Google必修的圖表簡報術", cfg)
    assert got["market_price"] == "420" and got["market_currency"] == "TWD"          # 定價, not the 331 sale price
    assert got["market_source"] == "eslite (web search) - out of print"
    assert got["market_url"] == "https://www.eslite.com/product/1001247312496322"
    web = [c for c in s.calls if "tavily" in c[1]]
    assert len(web) == 1 and web[0][2]["json"]["query"] == f"{OOP} site:eslite.com"
    assert web[0][2]["headers"]["Authorization"] == "Bearer TVKEY"
    assert net.DailyCounter(cfg.cache_dir / "quota.json", period="month").used("tavily") == 1


def test_web_search_is_off_without_a_key(tmp_path, fake):  # noqa: F811
    s = fake({"athena.eslite.com": Resp(200, {"hits": {"hit": []}})})
    market.find_market_price(OOP, "Google必修的圖表簡報術", cfg_for(tmp_path, market_providers=("eslite",)))
    assert not any("tavily" in c[1] or "langsearch" in c[1] for c in s.calls)


def test_web_search_monthly_limit(tmp_path, fake):  # noqa: F811
    s = fake({"athena.eslite.com/api/v2/search": Resp(200, {"hits": {"hit": []}}), "api.tavily.com/search": Resp(200, {})})
    cfg = cfg_for(tmp_path, market_providers=("eslite",), tavily_api_key="k", web_queries=("site",),
                  web_search_monthly_limit=1, cache_miss_days=0)
    for isbn in (OOP, ISBN):
        market.find_market_price(isbn, "T", cfg)
    assert sum("tavily" in c[1] for c in s.calls) == 1 and net.blocked("tavily")


def test_books_tw_web_search_and_tolerant_ids(tmp_path, fake):  # noqa: F811
    from pipeline.sources import taiwan
    assert taiwan.parse_books_search('<a href="https://www.books.com.tw/exep/assp.php/x/products/0010710103?a=1">'
                                     '<div id="prod-itemlist-0010000001">') == ["0010710103", "0010000001"]
    fake({"search.books.com.tw": Resp(200, text="<html><body>沒有找到</body></html>"),
          "api.tavily.com/search": Resp(200, WEB_REPLY),
          "www.books.com.tw/products/0010710103": Resp(200, text=product_page(OOP, "420", "332"))})
    cfg = cfg_for(tmp_path, market_providers=("books_tw",), tavily_api_key="k")
    got = market.find_market_price(OOP, "Google必修的圖表簡報術", cfg)
    assert got["market_price"] == "420" and got["market_source"] == "books_tw (web search)"
    saved = list((cfg.cache_dir / "debug").glob("*books_tw_search*.html"))
    assert saved and "沒有找到" in saved[0].read_text(encoding="utf-8")         # kept for inspection


def test_books_tw_human_check_page_stops_that_shop(tmp_path, fake):  # noqa: F811
    s = fake({"search.books.com.tw": Resp(200, text="<html><title>Just a moment...</title>captcha</html>"),
              "athena.eslite.com": Resp(200, ESLITE_TITLE_HITS)})
    cfg = cfg_for(tmp_path, market_providers=("books_tw", "eslite"))
    got = market.find_market_price(ISBN, TITLE, cfg)
    assert got["market_source"].startswith("eslite")                              # the next shop still answered
    assert sum("books.com.tw" in c[1] for c in s.calls) == 1 and net.blocked("books_tw")


def test_tavily_free_web_search(tmp_path, fake):  # noqa: F811
    s = fake({"athena.eslite.com/api/v2/search": Resp(200, {"hits": {"found": "0", "hit": []}}),
              "api.tavily.com/search": Resp(200, {"results": [{"url": "https://www.eslite.com/product/1001247312496322"}]}),
              "athena.eslite.com/api/v1/products/1001247312496322": Resp(200, ESLITE_OOP_PRODUCT)})
    cfg = cfg_for(tmp_path, market_providers=("eslite",), web_search="tavily", tavily_api_key="TVKEY")
    got = market.find_market_price(OOP, "Google必修的圖表簡報術", cfg)
    assert got["market_price"] == "420" and got["market_source"].startswith("eslite (web search)")
    method, url, kw = [c for c in s.calls if "tavily" in c[1]][0]
    assert method == "POST" and kw["json"]["include_domains"] == ["eslite.com"] and kw["json"]["query"] == f"{OOP} site:eslite.com"
    assert kw["json"]["include_raw_content"] is True
    assert kw["headers"]["Authorization"] == "Bearer TVKEY" and "TVKEY" not in url


def test_price_from_a_link_you_pasted(tmp_path, fake):  # noqa: F811
    fake({"athena.eslite.com/api/v1/products/1001247312496322": Resp(200, ESLITE_OOP_PRODUCT),
          "www.books.com.tw/products/0010710103": Resp(200, text=product_page(OOP, "420", "332"))})
    cfg = cfg_for(tmp_path)
    got = market.price_from_link("https://www.eslite.com/product/1001247312496322?pt=x", OOP, cfg)
    assert got["market_price"] == "420" and got["market_source"] == "eslite (your link)"
    got = market.price_from_link("https://www.books.com.tw/products/0010710103?loc=P_036_002", OOP, cfg)
    assert got["market_price"] == "420" and got["market_source"] == "books_tw (your link)"
    with pytest.raises(ValueError, match="not 9789861371955"):
        market.price_from_link("https://www.eslite.com/product/1001247312496322", ISBN, cfg)   # a different book
    with pytest.raises(ValueError, match="Paste a link"):
        market.price_from_link("https://example.com/x", OOP, cfg)
    assert market.find_market_price(OOP, "", cfg)["market_source"] == "books_tw (your link)"   # remembered


def test_browser_search_url():
    from pipeline.sources.websearch import browser_search_url
    u = browser_search_url(OOP)
    assert u.startswith("https://www.google.com/search?q=") and OOP in u and "eslite.com" in u


def test_langsearch_daily_free_web_search(tmp_path, fake):  # noqa: F811
    reply = {"code": 200, "data": {"webPages": {"value": [
        {"name": "x", "url": "https://www.example.com/9789869283533"},
        {"name": "eslite", "url": "https://www.eslite.com/product/1001247312496322"}]}}}
    s = fake({"athena.eslite.com/api/v2/search": Resp(200, {"hits": {"found": "0", "hit": []}}),
              "api.langsearch.com": Resp(200, reply),
              "athena.eslite.com/api/v1/products/1001247312496322": Resp(200, ESLITE_OOP_PRODUCT)})
    cfg = cfg_for(tmp_path, market_providers=("eslite",), web_search="langsearch", langsearch_api_key="sk-L",
                  web_search_monthly_limit=0)
    got = market.find_market_price(OOP, "T", cfg)
    assert got["market_price"] == "420"
    call = [c for c in s.calls if "langsearch" in c[1]][0]
    assert call[2]["json"]["query"] == f"{OOP} site:eslite.com" and call[2]["headers"]["Authorization"] == "Bearer sk-L"


def test_web_step_runs_even_if_old_env_lacks_it_and_engines_fall_back(tmp_path, fake):  # noqa: F811
    """Each search runs ONCE: the next engine is only used when one fails (here: tavily refuses the key)."""
    s = fake({"athena.eslite.com/api/v2/search": Resp(200, {"hits": {"found": "0", "hit": []}}),
              "api.tavily.com/search": Resp(401, {"detail": "bad key"}),
              "api.langsearch.com": Resp(200, {"data": {"webPages": {"value": [
                  {"url": "https://www.eslite.com/product/1001247312496322"}]}}}),
              "athena.eslite.com/api/v1/products/1001247312496322": Resp(200, ESLITE_OOP_PRODUCT)})
    cfg = cfg_for(tmp_path, market_providers=("eslite",), market_search=("isbn", "isbn10", "title"),  # no 'web'
                  web_search="", tavily_api_key="T", langsearch_api_key="L")                         # both keys
    got = market.find_market_price(OOP, "T", cfg)
    assert got["market_price"] == "420"
    assert [c[1] for c in s.calls if "tavily" in c[1] or "langsearch" in c[1]] == [
        "https://api.tavily.com/search", "https://api.langsearch.com/v1/web-search"]
    assert "tavily -> langsearch" in market.describe(cfg)
    assert "OFF" in market.describe(cfg_for(tmp_path))


def test_the_same_search_is_never_repeated_on_another_engine(tmp_path, fake):  # noqa: F811
    s = fake({"athena.eslite.com/api/v2/search": Resp(200, {"hits": {"found": "0", "hit": []}}),
              "api.tavily.com/search": Resp(200, {"results": []}),
              "api.langsearch.com": Resp(200, {})})
    cfg = cfg_for(tmp_path, market_providers=("eslite",), market_search=("isbn",), tavily_api_key="T",
                  langsearch_api_key="L")
    market.find_market_price(OOP, "", cfg)
    queries = [c[2]["json"]["query"] for c in s.calls if "tavily" in c[1] or "langsearch" in c[1]]
    assert queries == [f"{OOP} site:eslite.com", f"eslite {OOP}"]                 # each once, on tavily


def test_split_addon_without_space():
    from pipeline.photos.decode import split_addon
    assert split_addon("978186230571750399") == ("9781862305717", "50399")
    assert split_addon("9781862305717") == ("9781862305717", "")


def test_web_search_checks_the_top_three_like_google(tmp_path, fake):  # noqa: F811
    """'eslite <isbn>' -> results 1..3 opened in turn; the first whose ISBN is ours gives the price."""
    reply = {"results": [
        {"url": "https://www.eslite.com/product/1001247312874726", "content": "2020 edition"},     # another edition
        {"url": "https://www.eslite.com/search?q=9789869283533", "content": "search page"},        # not a product
        {"url": "https://www.eslite.com/product/1001247312496322", "content": "2016 edition"},     # ours
        {"url": "https://www.eslite.com/product/1009999999999999", "content": "never opened"}]}
    s = fake({"athena.eslite.com/api/v2/search": Resp(200, {"hits": {"found": "0", "hit": []}}),
              "api.tavily.com/search": Resp(200, reply),
              "athena.eslite.com/api/v1/products/1001247312874726": Resp(200, {"isbn13": "9789865519216",
                                                                               "final_price": 450}),
              "athena.eslite.com/api/v1/products/1001247312496322": Resp(200, ESLITE_OOP_PRODUCT)})
    cfg = cfg_for(tmp_path, market_providers=("eslite",), market_search=("web",), tavily_api_key="k")
    got = market.find_market_price(OOP, "", cfg)
    assert got["market_price"] == "420" and got["market_url"].endswith("1001247312496322")
    assert not any("1009999999999999" in c[1] for c in s.calls)


def test_books_tw_blocked_uses_the_page_text_from_the_search(tmp_path, fake):  # noqa: F811
    page_text = f"Google必修的圖表簡報術 ISBN：{OOP} 叢書系列 定價：420元 優惠價：79折332元"
    fake({"api.tavily.com/search": Resp(200, {"results": [
              {"url": "https://www.books.com.tw/products/0010710103", "raw_content": page_text}]}),
          "www.books.com.tw/products/0010710103": Resp(403, text="forbidden")})
    cfg = cfg_for(tmp_path, market_providers=("books_tw",), market_search=("web",), tavily_api_key="k")
    got = market.find_market_price(OOP, "", cfg)
    assert got["market_price"] == "420" and got["market_source"] == "books_tw (web search)"


def test_price_in_text_needs_our_isbn():
    from pipeline.sources.websearch import price_in_text
    assert price_in_text("ISBN 978-986-92835-3-3 定價 NT$420", OOP) == "420"
    assert price_in_text("ISBN 9789865519216 定價：450元", OOP) == ""            # another book's price
    assert price_in_text(f"ISBN {OOP} no price", OOP) == ""


def test_old_eslite_links_and_second_query_form(tmp_path, fake):  # noqa: F811
    """1st search's top results are other books -> 2nd form "eslite <isbn>" -> an OLD product.aspx?pgid= link."""
    replies = iter([
        Resp(200, {"results": [{"url": "https://www.eslite.com/product/1001247312874726"}]}),
        Resp(200, {"results": [{"url": "https://www.eslite.com/product/1001247312874726"},   # already checked
                               {"url": "http://www.eslite.com/product.aspx?pgid=1001247312496322"}]})])
    s = fake({"api.tavily.com/search": lambda *a, **k: next(replies),
              "athena.eslite.com/api/v1/products/1001247312874726": Resp(200, {"isbn13": "9789865519216"}),
              "athena.eslite.com/api/v1/products/1001247312496322": Resp(200, ESLITE_OOP_PRODUCT)})
    cfg = cfg_for(tmp_path, market_providers=("eslite",), market_search=("web",), tavily_api_key="k")
    got = market.find_market_price(OOP, "", cfg)
    assert got["market_price"] == "420" and got["market_url"] == "https://www.eslite.com/product/1001247312496322"
    queries = [c[2]["json"]["query"] for c in s.calls if "tavily" in c[1]]
    assert queries == [f"{OOP} site:eslite.com", f"eslite {OOP}"]
    assert sum("1001247312874726" in c[1] for c in s.calls) == 1                        # not opened twice


def test_books_tw_refusing_us_still_gets_the_web_search_and_tavily_reads_the_page(tmp_path, fake):  # noqa: F811
    page = f"<html>ISBN：{OOP} 定價：420元 優惠價：79折332元</html>"
    s = fake({"search.books.com.tw": Resp(403, text="forbidden"),
              "api.tavily.com/search": Resp(200, {"results": [{"url": "https://www.books.com.tw/products/0010710103",
                                                               "content": "博客來-Google必修的圖表簡報術"}]}),
              "api.tavily.com/extract": Resp(200, {"results": [{"url": "x", "raw_content": page}]})})
    cfg = cfg_for(tmp_path, market_providers=("books_tw",), tavily_api_key="k")
    got = market.find_market_price(OOP, "Google必修的圖表簡報術", cfg)
    assert got["market_price"] == "420" and got["market_source"] == "books_tw (web search)"
    assert not any("www.books.com.tw/products" in c[1] for c in s.calls)       # blocked: not asked again
    # the next book: books.com.tw is switched off for the run, but its web search still runs
    got = market.find_market_price(ISBN, "", cfg)
    assert sum("api.tavily.com/search" in c[1] for c in s.calls) >= 2


# ---- fallbacks: the e-book with our ISBN, then the same book in another edition ---------------------------------
def test_same_book_rules():
    from pipeline.sources.market import same_book, title_key
    assert title_key("被討厭的勇氣（二十萬冊紀念版）: 自我啟發之父阿德勒的教導") == "被討厭的勇氣"
    assert title_key("Google必修的圖表簡報術 (新版)") == title_key("Google必修的圖表簡報術")
    assert same_book("被討厭的勇氣", "岸見一郎, 古賀史健", "被討厭的勇氣(暢銷紀念版)", "岸見一郎、古賀史健 (譯者 葉小燕)")
    assert not same_book("被討厭的勇氣", "岸見一郎", "被討厭的勇氣", "別人")                 # another author
    assert not same_book("哈利波特(1)", "J.K.羅琳", "哈利波特(2)", "J.K.羅琳")               # another volume
    assert not same_book("被討厭的勇氣", "", "被討厭的勇氣: 完結篇", "")                      # no author: full title
    assert same_book("Storytelling with Data", "Cole Nussbaumer Knaflic",
                     "Storytelling with Data (2nd edition)", "Cole Nussbaumer Knaflic")


def book_page(isbn, title, author, price, year, ebook=False):
    return (f"<html><body><h1>{title}{' (電子書)' if ebook else ''}</h1><ul><li>作者：<a>{author}</a></li>"
            f"<li>出版日期：{year}/05/01</li></ul><ul class='price'><li>定價：<em>{price}</em>元</li></ul>"
            f"<ul><li>ISBN：{isbn}</li></ul></body></html>")


def test_ebooks_and_audiobooks_are_never_used(tmp_path, fake):  # noqa: F811
    isbn = "9789866582646"
    s = fake({f"search.books.com.tw/search/query/key/{isbn}": Resp(200, text=
              '<a href="//www.books.com.tw/products/E050014054">我家有個花果菜園 (電子書)</a>'),
              "search.books.com.tw": Resp(200, text="<html>no results</html>"),
              "products/E050014054": Resp(200, text=book_page(isbn, "我家有個花果菜園", "陳", "280", "2010", True))})
    cfg = cfg_for(tmp_path, market_providers=("books_tw",))
    assert market.find_market_price(isbn, "我家有個花果菜園", cfg, author="陳") == {}
    assert not any("E050014054" in c[1] for c in s.calls)                 # the e-book page is not even opened
    assert not market.is_printed("我家有個花果菜園 (有聲書)") and not market.is_printed("X", product_type="ebook")
    assert market.is_printed("我家有個花果菜園", product_type="book")


def test_other_edition_with_same_title_and_author(tmp_path, fake):  # noqa: F811
    isbn, other = "9789862166048", "9789862168486"
    fake({f"search.books.com.tw/search/query/key/{isbn}": Resp(200, text="<html>no results</html>"),
          "search.books.com.tw/search/query/key/9862166047": Resp(200, text="<html>no results</html>"),
          "search.books.com.tw/search/query/key/%E": Resp(200, text=
              '<a href="//www.books.com.tw/products/0010827573">x</a><a href="//www.books.com.tw/products/0010000009">y</a>'),
          "products/0010827573": Resp(200, text=book_page(other, "小王子(2019新版)", "聖修伯里", "250", "2019")),
          "products/0010000009": Resp(200, text=book_page("9789860000001", "小王子的星球", "別人", "199", "2018"))})
    cfg = cfg_for(tmp_path, market_providers=("books_tw",))
    row = {"isbn13": isbn, "title": "小王子", "author": "聖修伯里 著"}
    market.apply_market(row, cfg)
    assert row["market_price"] == "250" and row["market_match"] == "similar" and row["market_isbn"] == other
    assert "similar: 2019 edition" in row["market_source"] and row["market_url"].endswith("0010827573")
    assert row["price"] == "100" and row["price_basis"] == "40% of 250 TWD (other edition)"


def test_title_steps_come_after_every_isbn_step_and_web_title_is_one_search(tmp_path, fake):  # noqa: F811
    isbn = "9789862166048"
    calls = []

    def tavily(method, url, kw):
        calls.append(kw["json"])
        if len(calls) < 3:                                        # the two ISBN searches find nothing
            return Resp(200, {"results": []})
        return Resp(200, {"results": [{"url": "https://www.eslite.com/product/1001000000000001"}]})
    fake({"athena.eslite.com/api/v2/search": Resp(200, {"hits": {"found": "0", "hit": []}}),
          "api.tavily.com/search": tavily,
          "athena.eslite.com/api/v1/products/1001000000000001": Resp(200, {
              "name": "小王子", "author": ["聖修伯里"], "isbn13": "9789862168486", "final_price": 260,
              "manufacturer_date": "05/01/2019 00:00:00"})})
    cfg = cfg_for(tmp_path, market_providers=("eslite",), tavily_api_key="T")
    got = market.find_market_price(isbn, "小王子", cfg, author="聖修伯里")
    assert got["market_match"] == "similar" and got["market_price"] == "260"
    assert [c["query"] for c in calls] == [f"{isbn} site:eslite.com", f"eslite {isbn}", "小王子 聖修伯里"]
    assert calls[2]["include_domains"] == ["eslite.com"]


def test_ncl_is_asked_before_spending_a_web_search(tmp_path, fake, monkeypatch):  # noqa: F811
    from pipeline.sources import taiwan
    monkeypatch.setattr(taiwan, "from_ncl", lambda isbn, cfg, **k: {"list_price": "350"})
    s = fake({"athena.eslite.com/api/v2/search": Resp(200, {"hits": {"found": "0", "hit": []}})})
    cfg = cfg_for(tmp_path, market_providers=("eslite", "ncl"), tavily_api_key="T")
    got = market.find_market_price(OOP, "T", cfg)
    assert got["market_source"] == "ncl (registered price)" and got["market_match"] == "exact"
    assert not any("tavily" in c[1] for c in s.calls)


# ---- translated series books: 神奇樹屋 (Magic Tree House) -------------------------------------------------------
def test_series_title_and_translated_author_still_match():
    from pipeline.sources.market import same_author, same_book, same_title
    assert same_title("鬼屋裡的音樂家", "神奇樹屋 42: 鬼屋裡的音樂家")
    assert not same_title("鬼屋裡的音樂家", "神奇樹屋 44: 狄更斯的耶誕頌")
    assert not same_title("哈利波特: 神秘的魔法石", "哈利波特: 消失的密室")
    assert same_author("Mary Pope Osborne", "瑪麗．波．奧斯本") is None          # can't compare: not a 'no'
    assert same_author("Mary Pope Osborne, 奧斯本", "瑪麗．波．奧斯本") is True
    assert same_author("岸見一郎", "別人") is False
    assert same_book("鬼屋裡的音樂家", "Mary Pope Osborne", "神奇樹屋 42: 鬼屋裡的音樂家", "瑪麗．波．奧斯本")
    assert not same_book("鬼屋裡的音樂家", "Mary Pope Osborne", "Magic Tree House 5-8 (4 冊合售)", "Mary Pope Osborne")


def test_other_edition_on_eslite_by_its_own_search_and_never_the_english_original(tmp_path, fake):  # noqa: F811
    isbn = "9789862166048"
    eslite_hits = {"hits": {"found": "3", "hit": [
        {"id": "1003264122772000", "fields": {"name": "Magic Tree House 42: A Good Night for Ghosts",
                                              "author": ["Mary Pope Osborne"], "isbn": "9780375856495", "mprice": 299}},
        {"id": "1003264122772001", "fields": {"name": "神奇樹屋套書 41-44 (4冊合售)", "author": ["瑪麗．波．奧斯本"],
                                              "isbn": "9789865030000", "mprice": 1100}},
        {"id": "1003264122772529", "fields": {"name": "神奇樹屋 42: 鬼屋裡的音樂家", "author": ["瑪麗．波．奧斯本"],
                                              "isbn": "9789865035440", "mprice": 320, "final_price": 252,
                                              "manufacturer_date": "07/08/2019 00:00:00"}}]}}
    s = fake({"athena.eslite.com/api/v2/search": lambda m, url, kw: Resp(200, eslite_hits if kw["params"]["q"] ==
                                                                      "鬼屋裡的音樂家" else {"hits": {"hit": []}}),
              "search.books.com.tw": Resp(200, text="<html>no results</html>")})
    cfg = cfg_for(tmp_path, market_providers=("eslite", "books_tw"))
    row = {"isbn13": isbn, "title": "鬼屋裡的音樂家", "author": "Mary Pope Osborne"}
    market.apply_market(row, cfg)
    assert row["market_price"] == "320" and row["market_match"] == "similar" and row["market_isbn"] == "9789865035440"
    assert "2019 edition" in row["market_source"] and row["market_url"].endswith("1003264122772529")
    assert row["price"] == "128" and row["price_basis"] == "40% of 320 TWD (other edition)"
    assert not any("tavily" in c[1] for c in s.calls)


def test_max_price_default_allows_twd():
    from pipeline.core.validate import row_issues
    cfg = Config()
    assert cfg.max_price == 500
    r = {"isbn13": OOP, "title": "T", "author": "A", "condition": "like_new", "price": "450", "currency": "TWD",
         "barcode_photo": "x"}
    assert not [i for i in row_issues(r, cfg) if i["field"] == "price"]


def test_ncl_style_title_matches_the_shops_title():
    from pipeline.sources.market import same_title
    assert same_title("神奇樹屋. 44, 狄更斯的耶誕頌", "神奇樹屋 44: 狄更斯的耶誕頌")
    assert not same_title("神奇樹屋", "神奇樹屋 42: 鬼屋裡的音樂家")
    assert not same_title("神奇樹屋. 43, 耶誕夜的神秘訪客", "神奇樹屋 44: 狄更斯的耶誕頌")


def test_search_box_gets_the_volume_name():
    from pipeline.sources.market import search_title
    assert search_title("神奇樹屋 42: 鬼屋裡的音樂家") == "鬼屋裡的音樂家"
    assert search_title("神奇樹屋. 44, 狄更斯的耶誕頌") == "狄更斯的耶誕頌"
    assert search_title("被討厭的勇氣: 自我啟發之父阿德勒的教導") == "被討厭的勇氣"
    assert search_title("衝業績一定有效的10種態度: 突破1個客戶, 等於增加250筆生意") == "衝業績一定有效的10種態度"


def test_ncl_price_comes_before_the_shops(tmp_path, fake, monkeypatch):  # noqa: F811
    from pipeline.sources import taiwan
    seen = []
    monkeypatch.setattr(taiwan, "from_ncl", lambda isbn, cfg, **k: seen.append(k.get("title")) or {"list_price": "250"})
    s = fake({"athena.eslite.com": Resp(200, {"hits": {"hit": []}})})
    cfg = cfg_for(tmp_path, market_providers=("eslite", "books_tw", "ncl"))
    got = market.find_market_price(OOP, "Google必修的圖表簡報術", cfg)
    assert got["market_price"] == "250" and got["market_source"] == "ncl (registered price)"
    assert seen == ["Google必修的圖表簡報術"] and not s.calls             # no shop asked at all
