"""Selling sets: data/sets.csv, data/listings.csv, the Shopee upload file, and reading Shopee's reports back."""
import csv

import openpyxl
import pytest
from PIL import Image

from pipeline.apps import shopee
from pipeline.apps.sets import (change_set_status, refresh_sets, save_set_fields, set_description_zh,
                                set_title_zh)
from pipeline.core import store
from pipeline.core.config import Config
from synth import TW_ISBNS, book_back, set_front

CODE = "20261008-153012-S01"
TITLES = ["狄更斯的耶誕頌", "鬼屋裡的音樂家", "愛爾蘭任務", "月光下的魔笛", "再窮也要玩歐洲", "社交高手必修課",
          "衝業績一定有效的10種態度", "Google必修的圖表簡報術：Google總監首度公開，教你做對圖表"]


def make_template(path, category=True):
    """A stand-in for Shopee's bulk-upload template: a row of Shopee's keys, a row of names, three rows of
    explanations, data from row 6; and a category list."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Template"
    cols = [("ps_category", "分類代碼"), ("ps_product_name", "商品名稱"), ("ps_product_description", "商品描述"),
            ("ps_sku_parent_short", "主商品貨號"), ("et_title_variation_1", "規格名稱1"), ("ps_price", "價格"),
            ("ps_stock", "庫存"), ("ps_item_cover_image", "主商品圖片"), ("ps_item_image_1", "商品圖片 1"),
            ("ps_item_image_2", "商品圖片 2"), ("ps_weight", "重量"), ("ps_length", "長度"), ("ps_width", "寬度"),
            ("ps_height", "高度"), ("channel_30005", "7-ELEVEN"), ("channel_30006", "全家"),
            ("channel_39303", "黑貓宅急便"), ("ps_product_pre_order_dts", "較長備貨天數")]
    for j, (key, name) in enumerate(cols, 1):
        for i, v in enumerate((key, name, "必填", "說明", "格式"), 1):
            ws.cell(i, j, v)
    if category:
        cat = wb.create_sheet("分類")
        cat.append(["分類代碼", "分類"])
        cat.append([100644, "書籍及雜誌期刊 > 二手書"])
        cat.append([100645, "書籍及雜誌期刊 > 童書"])
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return [name for _, name in cols]


@pytest.fixture
def cfg(tmp_path):
    c = Config(data_dir=tmp_path / "data", market_providers=(), grid=(2, 4), set_size=8)
    c.ensure_dirs()
    d = c.processed / "20261008-153012" / "S01"
    d.mkdir(parents=True)
    front = d / "front.jpg"
    set_front(8, (2, 4)).save(front)
    rows = []
    for i in range(8):
        bc = d / f"{i + 1:02d}.jpg"
        book_back(TW_ISBNS[i], size=(300, 400)).save(bc)
        rows.append({"sku": f"{CODE}-{i + 1:02d}", "batch": "20261008-153012", "set_id": "S01",
                     "position": str(i + 1), "isbn13": TW_ISBNS[i], "title": TITLES[i], "author": "瑪麗．波．奧斯本",
                     "publisher": "小天下", "condition": "like_new", "price": "100", "currency": "TWD",
                     "market_price": "250", "market_currency": "TWD", "set_photo": str(front),
                     "front_photo": str(front), "barcode_photo": str(bc), "status": "validated"})
    rows[3]["status"] = "to_check"                    # one book not checked yet
    store.write_rows(c.csv_path, rows)
    return c


def books(cfg):
    return {r["position"]: r for r in store.read_rows(cfg.csv_path)}


def check_all(cfg):
    store.update_rows(cfg.csv_path, lambda rows: [r.update(status="validated") for r in rows])


def the_set(cfg):
    return next(s for s in store.read_table(cfg.sets_path) if s["set_code"] == CODE)


def the_listing(cfg):
    return next(li for li in store.read_table(cfg.listings_path) if li["set_code"] == CODE)


def test_the_set_follows_its_books(cfg):
    s = refresh_sets(cfg)[0]
    assert s["set_code"] == CODE and s["status"] == "draft" and "book(s) 4 not checked yet" in s["errors"]
    assert s["book_count"] == "8" and s["set_price"] == "800" and s["currency"] == "TWD"
    assert s["weight_g"] == "2400" and (s["length_cm"], s["width_cm"], s["height_cm"]) == ("30", "22", "15")
    assert s["set_title"].startswith(f"【{CODE}】狄更斯的耶誕頌、") and len(s["set_title"]) <= 60
    assert s["set_title"].endswith("｜二手書8本合售")
    check_all(cfg)
    assert refresh_sets(cfg)[0]["status"] == "ready"
    # a book priced in another currency: Shopee sells in NT$
    store.update_rows(cfg.csv_path, lambda rows: rows[2].update(currency="USD"))
    s = refresh_sets(cfg)[0]
    assert s["status"] == "draft" and "3 (USD)" in s["errors"] and s["set_price"] == ""


def test_description_lists_every_book_by_number(cfg):
    text = refresh_sets(cfg)[0]["description"]
    assert text.startswith(f"【{CODE}】二手書 8 本一套出售")
    assert "#1 狄更斯的耶誕頌\n作者：瑪麗．波．奧斯本\n出版社：小天下\n書況：近全新\n價格：NT$100（新書定價 NT$250）" in text
    assert "#8 Google必修的圖表簡報術：Google總監首度公開，教你做對圖表" in text
    assert "整套售價：NT$800" in text and "恕不拆賣" in text               # the footer you can edit
    assert (cfg.listings_dir / "description_footer.txt").exists()
    long = [{"position": str(i), "title": "長" * 200, "author": "作" * 100, "publisher": "出" * 100,
             "price": "100", "condition": "good"} for i in range(1, 31)]
    assert len(set_description_zh("X", long, cfg, 3000, 3000.0)) <= 3000
    assert len(set_title_zh(CODE, [{"title": "很" * 80}] * 8)) <= 60


def test_discount_and_your_own_text_are_kept(cfg):
    cfg.set_discount = 0.1
    check_all(cfg)
    assert refresh_sets(cfg)[0]["set_price"] == "720"
    s = save_set_fields(cfg, CODE, {"set_title": f"【{CODE}】神奇樹屋 8本", "weight_g": "1900"})
    assert s["set_title"] == f"【{CODE}】神奇樹屋 8本" and s["custom"] == "set_title,weight_g"
    store.update_rows(cfg.csv_path, lambda rows: rows[0].update(price="300"))
    s = refresh_sets(cfg)[0]
    assert s["set_title"] == f"【{CODE}】神奇樹屋 8本" and s["weight_g"] == "1900"    # yours: kept
    assert s["set_price"] == "900"                                                  # automatic: follows the books
    s = save_set_fields(cfg, CODE, {"set_title": ""})                             # empty = automatic again
    assert "狄更斯" in s["set_title"] and s["custom"] == "weight_g"
    with pytest.raises(ValueError):
        save_set_fields(cfg, CODE, {"set_price": "abc"})


def test_upload_file_is_shopees_template_filled_in(cfg):
    names = make_template(cfg.template_path)
    res = shopee.export(cfg)
    assert res["file"] is None and res["drafts"] and res["drafts"][0][0] == CODE      # book 4 not checked
    check_all(cfg)
    res = shopee.export(cfg)
    assert res["made"] == [CODE] and res["file"].exists()
    ws = openpyxl.load_workbook(res["file"])["Template"]
    row = {names[j]: ws.cell(6, j + 1).value for j in range(len(names))}
    assert ws.cell(5, 1).value == "格式"                        # the explanation rows are left alone
    assert row["分類代碼"] == 100644                             # found in the category list (二手書)
    assert row["商品名稱"].startswith(f"【{CODE}】") and row["主商品貨號"] == CODE
    assert row["價格"] == 800 and row["庫存"] == 1 and row["重量"] == 2.4 and row["長度"] == 30
    assert row["商品描述"].startswith(f"【{CODE}】二手書 8 本一套出售")
    assert row["7-ELEVEN"] == "開啟" and row["全家"] == "開啟" and row["黑貓宅急便"] == "關閉"
    assert row["規格名稱1"] is None and row["主商品圖片"] is None     # no variations; no photo links set up
    photos = sorted((cfg.listings_dir / "photos" / CODE).glob("*.jpg"))
    assert [p.name for p in photos] == [f"{i:02d}.jpg" for i in range(1, 10)]     # 1 set photo + 8 books
    assert all(Image.open(p).size == (1500, 1500) and p.stat().st_size < 2_000_000 for p in photos)
    s, li = the_set(cfg), the_listing(cfg)
    assert s["status"] == "listed" and len(s["images"].split(";")) == 9
    assert {b["status"] for b in books(cfg).values()} == {"listed"}
    assert li["platform"] == "shopee" and li["status"] == "exported" and li["export_file"] == str(res["file"])
    assert li["shopee_sku"] == CODE and li["shopee_category_id"] == "100644" and li["shopee_condition"] == "二手"
    assert li["price"] == "800" and li["title"] == s["set_title"]
    # run again: nothing new; --redo puts the set in a new file again
    assert shopee.export(cfg)["file"] is None
    again = shopee.export(cfg, redo=[CODE])
    assert again["made"] == [CODE] and again["file"] != res["file"]


def test_category_must_be_known(cfg):
    check_all(cfg)
    make_template(cfg.template_path, category=False)
    with pytest.raises(shopee.ShopeeError, match="SHOPEE_CATEGORY_ID"):
        shopee.export(cfg)
    assert the_set(cfg)["status"] == "ready"                   # nothing marked as listed
    cfg.shopee_category_id = "100644"
    assert shopee.export(cfg)["made"] == [CODE]


def test_without_template_a_plain_sheet_and_a_note(cfg):
    check_all(cfg)
    cfg.shopee_category_id = "100644"
    res = shopee.export(cfg)
    assert any("No Shopee template" in n for n in res["notes"])
    ws = openpyxl.load_workbook(res["file"]).active
    assert ws.cell(1, 2).value == "商品名稱" and ws.cell(2, 4).value == CODE


def test_photo_links_when_a_bucket_is_set(cfg):
    names = make_template(cfg.template_path)
    check_all(cfg)
    cfg.image_public_url, cfg.s3_bucket = "https://pub-123.r2.dev", "books"
    sent = []

    class Bucket:
        def upload_file(self, src, bucket, key, ExtraArgs=None):
            sent.append((bucket, key, ExtraArgs["ContentType"]))
    res = shopee.export(cfg, client=Bucket())
    assert sent[0] == ("books", f"{CODE}/01.jpg", "image/jpeg") and len(sent) == 9
    ws = openpyxl.load_workbook(res["file"])["Template"]
    row = {names[j]: ws.cell(6, j + 1).value for j in range(len(names))}
    assert row["主商品圖片"] == f"https://pub-123.r2.dev/{CODE}/01.jpg"
    assert row["商品圖片 1"].endswith("/02.jpg") and row["商品圖片 2"].endswith("/03.jpg")
    assert the_listing(cfg)["errors"] == ""


def order_report(path, rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["訂單編號", "訂單狀態", "不成立原因", "退貨 / 退款狀態", "買家帳號", "訂單成立日期", "商品總價",
               "成交手續費", "金流與系統處理費", "包裹查詢號碼", "主商品貨號", "商品名稱", "訂單完成時間"])
    for r in rows:
        ws.append(r)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def test_orders_reserve_then_sell_the_set(cfg):
    make_template(cfg.template_path)
    check_all(cfg)
    shopee.export(cfg)
    title = the_set(cfg)["set_title"]
    # someone orders it
    order_report(cfg.shopee_import / "Order.all.20261012.xlsx",
                 [["2610120ABC", "待出貨", "", "", "buyer1", "2026-10-12 10:00", "800", "", "", "", CODE, title, ""]])
    out = shopee.run(cfg, do_export=False)
    assert any("ordered on Shopee" in t for t in out["text"])
    s, li = the_set(cfg), the_listing(cfg)
    assert s["status"] == "reserved" and li["shopee_order_sn"] == "2610120ABC" and li["status"] == "live"
    assert {b["status"] for b in books(cfg).values()} == {"listed"}
    assert not list(cfg.shopee_import.glob("*.xlsx")) and len(list((cfg.shopee_import / "done").iterdir())) == 1
    # the order is completed - found by the code in the title even without the SKU column
    order_report(cfg.shopee_import / "Order.completed.xlsx",
                 [["2610120ABC", "已完成", "", "", "buyer1", "2026-10-12 10:00", "800", "48", "16", "TW123",
                   "", title, "2026-10-16 18:00"]])
    shopee.run(cfg, do_export=False)
    s, li = the_set(cfg), the_listing(cfg)
    assert s["status"] == "sold" and s["sold_on"] == "shopee" and s["sold_price"] == "800"
    assert s["sold_at"] == "2026-10-16 18:00"
    assert li["status"] == "sold" and li["shopee_fees"] == "64" and li["shopee_tracking_no"] == "TW123"
    assert {b["status"] for b in books(cfg).values()} == {"sold"}


def test_cancelled_order_puts_the_set_back_on_sale(cfg):
    make_template(cfg.template_path)
    check_all(cfg)
    shopee.export(cfg)
    f = cfg.data_dir / "r.xlsx"
    order_report(f, [["A1", "待付款", "", "", "b", "", "800", "", "", "", CODE, "", ""]])
    shopee.sync(cfg, [f])
    assert the_set(cfg)["status"] == "reserved"
    order_report(f, [["A1", "不成立", "買家取消", "", "b", "", "800", "", "", "", CODE, "", ""]])
    res = shopee.sync(cfg, [f])
    assert the_set(cfg)["status"] == "listed" and "cancelled" in res["lines"][0]
    assert the_listing(cfg)["shopee_order_sn"] == ""


def test_product_list_and_pasted_link(cfg):
    make_template(cfg.template_path)
    check_all(cfg)
    shopee.export(cfg)
    cfg.shopee_shop_id = "55555"
    p = cfg.data_dir / "products.csv"
    with open(p, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["商品ID", "商品名稱", "主商品貨號", "狀態"])
        w.writerow(["24680", "whatever", CODE, "上架中"])
    shopee.sync(cfg, [p])
    li = the_listing(cfg)
    assert li["status"] == "live" and li["shopee_item_id"] == "24680"
    assert li["url"] == "https://shopee.tw/product/55555/24680"
    assert shopee.parse_url("https://shopee.tw/神奇樹屋-i.123.456?sp_atk=x") == ("123", "456")
    li = shopee.set_link(cfg, CODE, "https://shopee.tw/product/123/999")
    assert li["shopee_item_id"] == "999" and li["url"].endswith("/999")
    with pytest.raises(shopee.ShopeeError):
        shopee.set_link(cfg, CODE, "https://example.com")


def test_set_status_by_hand_and_takedown_elsewhere(cfg):
    check_all(cfg)
    refresh_sets(cfg)
    store.write_table(cfg.listings_path, [{"set_code": CODE, "platform": "facebook", "status": "live",
                                           "url": "https://fb.example/1"}], store.LISTING_COLUMNS)
    msgs = change_set_status(cfg, CODE, "sold", "sold at the school fair")
    assert msgs == [f"{CODE} is sold: take it down on facebook (https://fb.example/1)"]
    s = the_set(cfg)
    assert s["status"] == "sold" and s["sold_on"] == "manual" and s["sold_price"] == "800" and "fair" in s["notes"]
    assert {b["status"] for b in books(cfg).values()} == {"sold"}
    change_set_status(cfg, CODE, "ready")
    assert the_set(cfg)["status"] == "ready" and {b["status"] for b in books(cfg).values()} == {"validated"}
    with pytest.raises(ValueError):
        change_set_status(cfg, CODE, "reserved")


def test_books_marked_listed_by_hand_before_sets_existed(cfg):
    store.update_rows(cfg.csv_path, lambda rows: [r.update(status="listed") for r in rows])
    s = refresh_sets(cfg)[0]
    assert s["status"] == "listed" and s["set_price"] == "800"
    assert shopee.export(cfg)["made"] == []                     # already on sale: not listed twice


def test_old_books_csv_loses_the_unused_platform_columns(tmp_path):
    p = tmp_path / "books.csv"
    p.write_text("sku,trademe_id,ebay_id,fb_status,notes\na,1,2,live,n\n", encoding="utf-8")
    store.write_rows(p, store.read_rows(p))
    header = p.read_text(encoding="utf-8-sig").splitlines()[0]
    assert "trademe_id" not in header and "fb_status" not in header and "notes" in header


def test_password_protected_report(cfg, monkeypatch):
    import sys
    import types
    make_template(cfg.template_path)
    check_all(cfg)
    shopee.export(cfg)
    plain = cfg.data_dir / "plain.xlsx"
    order_report(plain, [["A9", "運送中", "", "", "b", "", "800", "", "", "", CODE, "", ""]])
    locked = cfg.shopee_import / "Order.toship.xlsx"
    locked.write_bytes(b"not a zip: an encrypted Office file")
    out = shopee.run(cfg, do_export=False)
    assert any("SHOPEE_EXPORT_PASSWORD" in t for t in out["text"]) and locked.exists()   # left there to retry
    fake = types.ModuleType("msoffcrypto")

    class OfficeFile:
        def __init__(self, f):
            pass

        def load_key(self, password):
            assert password == "123456"

        def decrypt(self, buf):
            buf.write(plain.read_bytes())
    fake.OfficeFile = OfficeFile
    monkeypatch.setitem(sys.modules, "msoffcrypto", fake)
    cfg.shopee_export_password = "123456"
    shopee.run(cfg, do_export=False)
    assert the_set(cfg)["status"] == "reserved" and not locked.exists()
