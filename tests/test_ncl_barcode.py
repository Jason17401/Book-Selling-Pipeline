"""Free, non-Chinese price sources: 國家圖書館 (NCL, Taiwan's ISBN agency) and the price barcode on English books."""
import pytest

from pipeline.sources import market, taiwan
from pipeline.core.config import Config
from pipeline.photos.decode import split_addon
from test_lookup import Resp, fake  # noqa: F401  (fixture)

ISBN = "9789869283533"
RESULT_LIST = """<html><body><div>顯示查詢結果 ( 找到 1 筆 )</div><table class="table-searchbooks">
<tr><th>封面圖</th><th>書名</th><th>作者</th><th>出版者</th><th>日期</th></tr>
<tr><td data-th="封面圖"></td><td data-th="書名"><a href="main_DisplayRecord_Popup.php?Pact=view&amp;Pkey=1050307*0123&amp;KeepThis=true&amp;TB_iframe=true&amp;width=780&amp;height=480">Google必修的圖表簡報術</a></td>
<td data-th="作者">柯爾.諾瑟鮑姆.娜菲克</td><td data-th="出版者">商周</td><td data-th="日期">105/03</td></tr></table></body></html>"""
DETAIL = """<html><body><table>
<tr><th>書名</th><td>Google必修的圖表簡報術 : Google總監首度公開絕活</td></tr>
<tr><th>作者</th><td>柯爾.諾瑟鮑姆.娜菲克(Cole Nussbaumer Knaflic)著 ; 徐昊譯</td></tr>
<tr><th>出版機構</th><td>商周</td></tr>
<tr><th>ISBN(裝訂方式)</th><td>9789869283533 (平裝)</td></tr>
<tr><th>定價</th><td>NT$420</td></tr>
<tr><th>出版年月</th><td>105/03</td></tr>
<tr><th>頁數</th><td>256</td></tr></table></body></html>"""


def test_ncl_parsing():
    links = taiwan.parse_ncl_links(RESULT_LIST, "https://isbn.ncl.edu.tw/NEW_ISBNNet")
    assert links == ["https://isbn.ncl.edu.tw/NEW_ISBNNet/main_DisplayRecord_Popup.php?Pact=view&Pkey=1050307*0123"]
    d = taiwan.parse_ncl_detail(DETAIL)
    assert d["isbns"] == [ISBN] and d["list_price"] == "420" and d["year"] == "2016" and d["format"] == "平裝"
    assert d["publisher"] == "商周" and d["pages"] == "256" and d["title"].startswith("Google必修的圖表簡報術")


def test_ncl_market_price_out_of_print(tmp_path, fake):  # noqa: F811
    s = fake({"H30_SearchBooks.php": Resp(200, text=RESULT_LIST),
              "main_DisplayRecord_Popup.php": Resp(200, text=DETAIL)})
    cfg = Config(data_dir=tmp_path, scrape_delay=0, market_providers=("ncl",))
    got = market.find_market_price(ISBN, "Google必修的圖表簡報術", cfg)
    assert got["market_price"] == "420" and got["market_currency"] == "TWD" and got["market_source"] == "ncl (registered price)"
    assert "Pkey=1050307*0123" in got["market_url"]
    first = s.calls[0]
    assert first[2]["params"] == {"Pact": "Search", "Pval": ISBN}


def test_ncl_record_for_another_isbn_is_ignored(tmp_path, fake):  # noqa: F811
    fake({"H30_SearchBooks.php": Resp(200, text=RESULT_LIST),
          "main_DisplayRecord_Popup.php": Resp(200, text=DETAIL.replace(ISBN, "9789865519216"))})
    cfg = Config(data_dir=tmp_path, scrape_delay=0, market_providers=("ncl",))
    assert market.find_market_price(ISBN, "T", cfg) == {}


@pytest.mark.parametrize("addon,expected", [
    ("51299", ("12.99", "USD")), ("00799", ("7.99", "GBP")), ("10999", ("109.99", "GBP")),
    ("41999", ("19.99", "NZD")), ("32495", ("24.95", "AUD")), ("61500", ("15.00", "CAD")),
    ("50000", None), ("59999", None), ("90000", None), ("", None), ("12", None),
])
def test_price_in_the_add_on_barcode(addon, expected):
    assert market.addon_price(addon) == expected


def test_split_addon():
    assert split_addon("9781862305717 50399") == ("9781862305717", "50399")
    assert split_addon("9781862305717") == ("9781862305717", "")


@pytest.mark.parametrize("addon,isbn,expected", [
    ("00350", "9789869283533", ("350", "TWD")),     # Taiwanese book: the add-on is the price in NT$
    ("00280", "9789571234567", ("280", "TWD")),
    ("00000", "9786269999999", None),
    ("51299", "9781862305717", ("12.99", "USD")),
    ("51299", "9784000000000", None),                # Japanese ISBN: currency unknown -> not used
])
def test_add_on_currency_follows_the_isbn(addon, isbn, expected):
    assert market.addon_price(addon, isbn) == expected


def test_barcode_price_is_only_the_last_resort(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(market, "find_market_price", lambda *a, **k: called.append(1) or {})
    cfg = Config(data_dir=tmp_path, market_providers=("barcode", "eslite"))
    row = {"isbn13": "9781862305717", "title": "A Wild West Ride", "barcode_addon": "50399"}
    market.apply_market(row, cfg)
    assert called                                                         # the shops were asked first
    assert row["market_price"] == "3.99" and row["market_currency"] == "USD"
    assert row["market_source"].startswith("barcode (price printed on the book")
    assert row["price"] == "1.5" and row["currency"] == "USD"            # 40% of 3.99, rounded to 0.5


def test_shop_price_beats_the_barcode_and_old_wrong_barcode_prices_are_redone(tmp_path, monkeypatch):
    shop = {"market_price": "420", "market_currency": "TWD", "market_source": "eslite (isbn)", "market_url": "u"}
    monkeypatch.setattr(market, "find_market_price", lambda *a, **k: dict(shop))
    cfg = Config(data_dir=tmp_path, market_providers=("barcode", "eslite"))
    # saved by an older version: the NT$ add-on read as British pounds, and a price worked out from that
    row = {"isbn13": ISBN, "title": "T", "barcode_addon": "00420", "market_price": "4.20", "market_currency": "GBP",
           "market_source": "barcode (price printed on the book)", "price": "1.5", "currency": "GBP",
           "price_basis": "40% of 4.20 GBP"}
    market.apply_market(row, cfg)
    assert row["market_price"] == "420" and row["market_currency"] == "TWD" and row["market_source"] == "eslite (isbn)"
    assert row["price"] == "168" and row["currency"] == "TWD"
    # a price YOU typed is never touched
    row = {"isbn13": ISBN, "market_source": "barcode (x)", "market_price": "4.20", "price": "9", "price_basis": "manual"}
    market.apply_market(row, cfg)
    assert row["price"] == "9" and row["market_price"] == "420"
