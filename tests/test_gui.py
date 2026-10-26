"""The review window. Uses real PyQt6 (off-screen) when installed, otherwise the stand-in in fakeqt.py."""

import importlib
import os

import pytest
from synth import TW_ISBNS, book_back, set_front

from pipeline.apps import editing
from pipeline.core import store
from pipeline.core.config import Config

try:
    import PyQt6.QtWidgets  # noqa: F401

    REAL_QT = True
except ImportError:
    REAL_QT = False


@pytest.fixture
def gui(monkeypatch):
    if REAL_QT:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt6.QtWidgets import QApplication

        app = QApplication.instance() or QApplication([])
    else:
        import fakeqt

        fakeqt.install(monkeypatch)
        app = None
    mod = importlib.import_module("pipeline.apps.gui")
    monkeypatch.setattr(mod.QMessageBox, "question", staticmethod(lambda *a, **k: mod.QMessageBox.StandardButton.Save))
    monkeypatch.setattr(mod.QMessageBox, "warning", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(mod.QMessageBox, "information", staticmethod(lambda *a, **k: None))
    # nothing may wait for a person or open windows on the computer running the tests: a real file dialog would sit
    # there forever (offscreen, nobody to click it), and Explorer / the browser would really open
    monkeypatch.setattr(mod.QFileDialog, "getOpenFileNames", staticmethod(lambda *a, **k: ([], "")))
    monkeypatch.setattr(mod, "open_folder", lambda path: None)
    monkeypatch.setattr(mod.QDesktopServices, "openUrl", staticmethod(lambda *a, **k: True))

    def settle():
        if app is not None:
            from PyQt6.QtCore import QThreadPool

            QThreadPool.globalInstance().waitForDone(10000)
            for _ in range(5):
                app.processEvents()

    mod.settle = settle
    return mod


@pytest.fixture
def cfg(tmp_path):
    c = Config(data_dir=tmp_path / "data", market_providers=())
    c.ensure_dirs()
    set_dir = tmp_path / "data" / "processed" / "b" / "S01"
    (set_dir / "06").mkdir(parents=True)
    front, barcode = set_dir / "front.jpg", set_dir / "06" / "barcode.jpg"
    set_front().save(front)
    book_back(TW_ISBNS[5], size=(300, 400)).save(barcode)
    photos = {"front_photo": str(front), "back_photo": str(barcode)}
    store.write_rows(
        c.csv_path,
        [
            {
                "sku": "b-S01-05",
                "batch": "b",
                "set_id": "S01",
                "position": "5",
                "isbn13": TW_ISBNS[4],
                "title": "Done",
                "author": "A",
                "condition": "as_new",
                "price": "5",
                "currency": "NZD",
                "status": "validated",
                **photos,
            },
            {
                "sku": "b-S01-06",
                "batch": "b",
                "set_id": "S01",
                "position": "6",
                "status": "needs_manual",
                "created_at": "keep-me",
                **photos,
            },
            {
                "sku": "b-S01-07",
                "batch": "b",
                "set_id": "S01",
                "position": "7",
                "isbn13": TW_ISBNS[6],
                "title": "T7",
                "author": "A7",
                "price": "abc",
                "status": "enriched",
                **photos,
            },
        ],
    )
    return c


def test_fix_a_book_and_save(gui, cfg):
    w = gui.ReviewWindow(cfg)
    gui.settle()
    assert len(w.book_items()) == 2 and w.editor.row["sku"] == "b-S01-06"  # finished book hidden, first to fix shown
    top = w.list.topLevelItem(0)  # the books are grouped under their set
    assert w.list.topLevelItemCount() == 1 and top.text(0).startswith("▾  Set S01  ·  batch b") and top.isExpanded()
    assert w.book_items()[0].text(0).startswith("#6  ") and "S01" not in w.book_items()[0].text(0)
    ed = w.editor
    ed.fields["isbn13"].setText("978-986-137-195-5")
    ed.fields["title"].setText("被討厭的勇氣")
    ed.fields["author"].setText("岸見一郎")
    ed.condition.setCurrentIndex(ed.condition.findData("good"))
    ed.fields["price"].setText("6")
    ed.fields["currency"].setText("twd")
    ed._changed()
    assert ed.dirty
    assert ed.save() is True and not ed.dirty
    rows = {r["sku"]: r for r in store.read_rows(cfg.csv_path)}
    assert rows["b-S01-06"]["isbn13"] == "9789861371955" and rows["b-S01-06"]["status"] == "validated"
    assert rows["b-S01-06"]["created_at"] == "keep-me" and rows["b-S01-05"]["title"] == "Done"
    assert rows["b-S01-06"]["price"] == "6" and rows["b-S01-06"]["currency"] == "TWD"
    assert "checked" in w.list.currentItem().text(0)  # saving = checked by you


def test_lookup_fills_only_empty_fields(gui, cfg, monkeypatch):
    monkeypatch.setattr(
        editing,
        "lookup_fields",
        lambda c, isbn: {
            "isbn13": "9789861371955",
            "source": "eslite",
            "fields": {"title": "From web", "author": "Web author"},
        },
    )
    w = gui.ReviewWindow(cfg)
    gui.settle()
    ed = w.editor
    ed.fields["isbn13"].setText("9789861371955")
    ed.fields["author"].setText("Typed by me")
    ed.lookup_btn.click()
    gui.settle()
    assert ed.fields["title"].text() == "From web" and ed.fields["author"].text() == "Typed by me" and ed.dirty


def test_fill_whole_set(gui, cfg):
    w = gui.ReviewWindow(cfg)
    gui.settle()
    ed = w.editor
    ed.set_condition.setCurrentIndex(ed.set_condition.findData("fair"))
    ed.set_price.setText("4")
    ed.fill_set()
    gui.settle()
    rows = {r["sku"]: r for r in store.read_rows(cfg.csv_path)}
    assert rows["b-S01-06"]["condition"] == "fair" and rows["b-S01-06"]["price"] == "4"
    assert rows["b-S01-06"]["currency"] == "TWD" and rows["b-S01-07"]["price"] == "abc"  # filled only blanks
    assert rows["b-S01-05"]["condition"] == "as_new" and rows["b-S01-05"]["price"] == "5"


def test_condition_and_price_are_required(gui, cfg):
    w = gui.ReviewWindow(cfg)
    gui.settle()
    ed = w.editor
    ed.fields["isbn13"].setText(TW_ISBNS[5])
    ed.fields["title"].setText("T6")
    ed.fields["author"].setText("A6")
    ed._changed()
    assert ed.save()
    row = {r["sku"]: r for r in store.read_rows(cfg.csv_path)}["b-S01-06"]
    assert row["status"] == "enriched" and "condition" in row["errors"] and "price" in row["errors"]
    assert len(w.book_items()) == 2  # still listed: price + condition to fill
    ed.condition.setCurrentIndex(ed.condition.findData("as_new"))
    ed.fields["price"].setText("120")
    ed.fields["currency"].setText("TWD")
    ed._changed()
    assert ed.save()
    assert {r["sku"]: r for r in store.read_rows(cfg.csv_path)}["b-S01-06"]["status"] == "validated"


def test_new_books_from_the_bot_appear(gui, cfg):
    w = gui.ReviewWindow(cfg)
    gui.settle()
    store.append_rows(
        cfg.csv_path, [{"sku": "c-S01-01", "batch": "c", "set_id": "S01", "position": "1", "status": "needs_manual"}]
    )
    w._csv_mtime = -1
    w._check_file()
    assert len(w.book_items()) == 3 and w.list.topLevelItemCount() == 2  # a new set from the bot
    assert w.list.topLevelItem(1).text(0).startswith("▾  Set S01  ·  batch c")


def test_unsaved_edits_are_not_lost_when_switching(gui, cfg):
    w = gui.ReviewWindow(cfg)
    gui.settle()
    w.editor.fields["notes"].setPlainText("remember the coffee stain")
    w.editor._changed()
    w.list.setCurrentItem(w.book_items()[1])  # the dialog answers 'Save'
    gui.settle()
    assert {r["sku"]: r for r in store.read_rows(cfg.csv_path)}["b-S01-06"]["notes"] == "remember the coffee stain"
    assert w.editor.row["sku"] == "b-S01-07"
    assert "5 to fix" in w.book_items()[0].text(0)  # book 6's entry updated after the save (ISBN, title, author)


def test_market_price_and_ratio_button(gui, cfg, monkeypatch):
    w = gui.ReviewWindow(cfg)
    gui.settle()
    ed = w.editor
    for name, value in (("isbn13", TW_ISBNS[5]), ("title", "T6"), ("author", "A6")):
        ed.fields[name].setText(value)
    ed.market_price.setText("300")
    ed.market_currency.setText("TWD")
    ed.ratio_btn.click()
    assert ed.price.text() == "120" and ed.currency.text() == "TWD"
    assert ed.save()
    row = {r["sku"]: r for r in store.read_rows(cfg.csv_path)}["b-S01-06"]
    assert row["price"] == "120" and row["currency"] == "TWD" and row["price_basis"] == "40% of 300 TWD"


def test_lookup_also_brings_market_price_and_suggestion(gui, cfg, monkeypatch):
    monkeypatch.setattr(
        editing,
        "lookup_fields",
        lambda c, isbn: {
            "isbn13": TW_ISBNS[5],
            "source": "eslite",
            "market_note": "New copy: 300 TWD",
            "fields": {
                "title": "T",
                "author": "A",
                "market_price": "300",
                "market_currency": "TWD",
                "market_source": "eslite (title)",
                "market_url": "https://www.eslite.com/product/1",
                "suggested_price": "120",
                "suggested_currency": "TWD",
                "price_basis": "40% of 300 TWD",
            },
        },
    )
    w = gui.ReviewWindow(cfg)
    gui.settle()
    ed = w.editor
    ed.fields["isbn13"].setText(TW_ISBNS[5])
    ed.lookup_btn.click()
    gui.settle()
    assert (
        ed.market_price.text() == "300"
        and ed.price.text() == "120"
        and ed.values()["market_source"] == "eslite (title)"
    )
    assert ed.save()
    row = {r["sku"]: r for r in store.read_rows(cfg.csv_path)}["b-S01-06"]
    assert row["market_url"] == "https://www.eslite.com/product/1" and row["price_basis"] == "40% of 300 TWD"


def test_paste_shop_link(gui, cfg, monkeypatch):
    monkeypatch.setattr(
        editing,
        "market_from_link",
        lambda c, url, isbn: {
            "note": "New copy: 420 TWD from your link",
            "fields": {
                "market_price": "420",
                "market_currency": "TWD",
                "market_source": "eslite (your link)",
                "market_url": url,
                "suggested_price": "168",
                "suggested_currency": "TWD",
                "price_basis": "40% of 420 TWD",
            },
        },
    )
    w = gui.ReviewWindow(cfg)
    gui.settle()
    ed = w.editor
    ed.fields["isbn13"].setText("9789869283533")
    ed.link_edit.setText("https://www.eslite.com/product/1001247312496322")
    ed.link_btn.click()
    gui.settle()
    assert ed.market_price.text() == "420" and ed.price.text() == "168" and ed.currency.text() == "TWD"
    assert ed.values()["market_url"] == "https://www.eslite.com/product/1001247312496322"


def test_to_check_books_show_and_save_confirms(gui, cfg):
    rows = store.read_rows(cfg.csv_path)
    rows[0]["status"] = "to_check"  # b-S01-05: complete, just processed
    store.write_rows(cfg.csv_path, rows)
    w = gui.ReviewWindow(cfg)
    gui.settle()
    texts = [it.text(0) for it in w.book_items()]
    assert any(t.startswith("#5") and "quick check" in t for t in texts)
    assert "to check" in w.count.text() and "to fix" in w.count.text()
    i = next(i for i, t in enumerate(texts) if t.startswith("#5"))
    w.list.setCurrentItem(w.book_items()[i])
    gui.settle()
    assert w.editor.row["sku"] == "b-S01-05"
    assert w.editor.save()  # nothing changed - 'Save / looks right'
    assert {r["sku"]: r for r in store.read_rows(cfg.csv_path)}["b-S01-05"]["status"] == "validated"
    assert "checked" in w.book_items()[i].text(0)


def test_sell_window(gui, cfg, monkeypatch):
    from pipeline.apps.products import refresh_products

    w = gui.SellWindow(cfg)
    gui.settle()
    assert w.list.topLevelItemCount() == 1 and w.current["product_code"] == "b-S01" and w.current["status"] == "draft"
    assert "not checked yet" in w.state.text()
    assert w.discount.text() == "20" and "% off" in w.list.topLevelItem(0).text(0)
    w.discount.setText("25")
    w.discount.textEdited.emit("25")
    assert "no TWD price" in w.original.text()  # book 5 is priced in NZD, book 6 has no price yet
    w.current = {**w.current, "original_price": "1000", "status": "ready"}
    w._recount_price()
    assert w.zhe.text() == "= 75 折" and w.price.text() == "750" and "1,000" in w.original.text()
    w.price.setText("800")  # typing the set price changes the discount ...
    w.price.textEdited.emit("800")
    assert w.discount.text() == "20" and w.zhe.text() == "= 8 折"
    w.price.setText("827")
    w.price.textEdited.emit("827")
    assert w.discount.text() == "17.3" and w.zhe.text() == ""
    w.discount.setText("25")  # ... and the discount changes the price
    w.discount.textEdited.emit("25")
    assert w.price.text() == "750" and w._fields()["discount"] == "25" and "price" not in w._fields()
    w.title.setText("【b-S01】三本書")
    assert w.save() is True
    s = refresh_products(cfg)[0]
    assert s["title"] == "【b-S01】三本書" and s["custom"] == "discount,title" and s["discount"] == "25"
    w.price.setText("1,234")
    w.price.textEdited.emit("1,234")
    assert w._fields()["price"] == "1,234" and "discount" not in w._fields()
    assert w.save() is True
    s = refresh_products(cfg)[0]
    assert s["custom"] == "price,title" and s["price"] == "1234"
    w.description.setPlainText("我的介紹\n第二行")  # typed, not saved: asked (Save) before going on
    assert w.unsaved()
    w.status_box.setCurrentIndex(w.status_box.findData("withdrawn"))
    w.change_status()
    assert refresh_products(cfg)[0]["description"] == "我的介紹\n第二行" and not w.unsaved()
    assert refresh_products(cfg)[0]["status"] == "withdrawn" and w.current["status"] == "withdrawn"
    w.link_edit.setText("https://shopee.tw/x-i.1.2")
    w.save_link()
    assert refresh_products(cfg)[0]["status"] == "listed" and "item 2" in w.message.text()
    shown = []
    monkeypatch.setattr(gui.QMessageBox, "information", staticmethod(lambda parent, title, text: shown.append(text)))
    w.title.setText("【b-S01】新標題")
    w.reject()  # closing asks first: Save
    assert refresh_products(cfg)[0]["title"] == "【b-S01】新標題"
    w.make_upload()
    gui.settle()
    assert shown and "No product is ready" in shown[0]
    assert w.again_btn.isEnabled() and w.upload_btn.text() == "Make Shopee upload file"
    w.upload_again()  # Shopee rejected it: a new upload file for it
    gui.settle()
    assert "No Shopee template" in shown[-1] and w.again_btn.text() == "Make a new upload file for it"
    import csv

    report = cfg.data_dir / "Shopee product list.csv"  # 'Read Shopee report...': a file you choose
    with open(report, "w", newline="", encoding="utf-8-sig") as f:
        csv.writer(f).writerows([["商品ID", "商品名稱", "主商品貨號", "狀態"], ["24680", "x", "b-S01", "上架中"]])
    monkeypatch.setattr(gui.QFileDialog, "getOpenFileNames", staticmethod(lambda *a, **k: ([str(report)], "")))
    w.read_report()
    gui.settle()
    from pipeline.core import store as st

    li = next(x for x in st.read_table(cfg.listings_path) if x["product_code"] == "b-S01")
    assert li["shopee_item_id"] == "24680" and "Read 1 Shopee file" in shown[-1] and report.exists()
    opened = []
    monkeypatch.setattr(gui, "open_folder", opened.append)
    (cfg.photos_dir / "b-S01").mkdir(parents=True)
    w.open_photos()
    assert opened == [cfg.photos_dir / "b-S01"]


def test_sets_fold_and_refresh(gui, cfg):
    w = gui.ReviewWindow(cfg)
    gui.settle()
    top = w.list.topLevelItem(0)
    w._clicked(top)  # clicking a set heading folds it ...
    assert not top.isExpanded()
    w._folded(top)
    assert top.text(0).startswith("▸  Set S01")  # its arrow shows it is folded
    w.list.itemCollapsed.emit(top)
    w.reload()  # ... and it stays folded after a refresh (Ctrl+R)
    assert not w.list.topLevelItem(0).isExpanded()
    assert "Ctrl+R" in w.refresh_btn.text() and "Sell products" in w.sell_btn.text()


def test_genres_are_chips(gui, cfg):
    w = gui.ReviewWindow(cfg)
    gui.settle()
    ed = w.editor
    g = ed.fields["genre"]
    g.setText("童書 > 冒險／驚悚小說")
    assert g.genres() == ["童書", "冒險／驚悚小說"] and len(g._chips) == 2
    g.add("繪本")
    assert g.text() == "童書 > 冒險／驚悚小說 > 繪本" and ed.dirty  # adding a genre is an edit
    g._chips[0].remove_button.click()  # x on the first chip
    assert g.genres() == ["冒險／驚悚小說", "繪本"]
    g._start_add()
    g.input.setText("Body, Mind & Spirit")  # commas stay inside one genre
    g._finish_add()
    assert g.genres()[-1] == "Body, Mind & Spirit"
    for name, value in (("isbn13", "978-986-137-195-5"), ("title", "T"), ("author", "A")):
        ed.fields[name].setText(value)
    assert ed.save()
    row = {r["sku"]: r for r in store.read_rows(cfg.csv_path)}["b-S01-06"]
    assert row["genre"] == "冒險／驚悚小說 > 繪本 > Body, Mind & Spirit"


def test_whole_set_photo_is_numbered(cfg):
    import io

    from PIL import Image

    r = editing.list_rows(cfg)[0]
    whole = next(p for p in r["photos"] if p["label"] == "Whole set")
    assert whole["numbered"] and whole["mark"] == int(r["position"])
    plain = Image.open(io.BytesIO(editing.thumbnail_bytes(cfg, whole["path"], 400)))
    marked = Image.open(io.BytesIO(editing.thumbnail_bytes(cfg, whole["path"], 400, 0, True, whole["mark"])))
    assert plain.size == marked.size and plain.tobytes() != marked.tobytes()  # numbers drawn on it


def test_stock_and_minimum_purchase_in_sell_window(gui, cfg, monkeypatch):
    from pipeline.apps.products import refresh_products

    w = gui.SellWindow(cfg)
    gui.settle()
    assert (w.stock.text(), w.min_purchase.text()) == ("1", "1") and not w.unsaved()
    warned = []
    monkeypatch.setattr(gui.QMessageBox, "warning", staticmethod(lambda parent, title, text: warned.append(text)))
    w.stock.setText("3")
    w.min_purchase.setText("4")
    w.min_purchase.textEdited.emit("4")
    assert "more than the stock" in w.quantity_note.text() and w.unsaved()
    assert w.save() is False and "more than the stock" in warned[-1]  # nothing saved
    assert refresh_products(cfg)[0]["stock"] == "1"
    w.min_purchase.setText("2")
    w.min_purchase.textEdited.emit("2")
    assert w.quantity_note.text() == "" and w.save() is True
    p = refresh_products(cfg)[0]
    assert (p["stock"], p["min_purchase"]) == ("3", "2") and (w.stock.text(), w.min_purchase.text()) == ("3", "2")


def test_sell_a_set_per_book(gui, cfg):
    w = gui.ReviewWindow(cfg)
    gui.settle()
    ed = w.editor
    assert ed.sell_mode.currentData() == "set"  # one product, unless you choose
    ed.sell_mode.setCurrentIndex(ed.sell_mode.findData("book"))
    ed._sell_mode_chosen()
    gui.settle()
    assert {r.get("sell_as") for r in store.read_rows(cfg.csv_path)} == {"book"}
    assert "sold per book" in w.list.topLevelItem(0).text(0) and w.editor.sell_mode.currentData() == "book"
    s = gui.SellWindow(cfg)
    gui.settle()
    assert s.list.topLevelItemCount() == 3 and s.current["kind"] == "book"  # 3 books, 3 products
    assert s.list.topLevelItem(0).text(0).startswith("#5 Done  ·  Set S01  ·  batch b")
    assert s.title_label.text().startswith("#5 Done") and "book's own price" not in s.original.text()  # NZD: no total
