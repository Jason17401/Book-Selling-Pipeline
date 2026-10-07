"""The review window. Uses real PyQt6 (off-screen) when installed, otherwise the stand-in in fakeqt.py."""
import importlib
import os

import pytest

from pipeline.apps import editing
from pipeline.core import store
from pipeline.core.config import Config
from synth import TW_ISBNS, book_back, set_front

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
    photos = {"front_photo": str(front), "barcode_photo": str(barcode)}
    store.write_rows(c.csv_path, [
        {"sku": "b-S01-05", "batch": "b", "set_id": "S01", "position": "5", "isbn13": TW_ISBNS[4], "title": "Done",
         "author": "A", "condition": "like_new", "price": "5", "currency": "NZD", "status": "validated", **photos},
        {"sku": "b-S01-06", "batch": "b", "set_id": "S01", "position": "6", "status": "needs_manual",
         "trademe_id": "keep-me", **photos},
        {"sku": "b-S01-07", "batch": "b", "set_id": "S01", "position": "7", "isbn13": TW_ISBNS[6], "title": "T7",
         "author": "A7", "price": "abc", "status": "enriched", **photos},
    ])
    return c


def test_fix_a_book_and_save(gui, cfg):
    w = gui.ReviewWindow(cfg)
    gui.settle()
    assert w.list.count() == 2 and w.editor.row["sku"] == "b-S01-06"      # finished book hidden, first to fix shown
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
    assert rows["b-S01-06"]["trademe_id"] == "keep-me" and rows["b-S01-05"]["title"] == "Done"
    assert rows["b-S01-06"]["price"] == "6" and rows["b-S01-06"]["currency"] == "TWD"
    assert "checked" in w.list.currentItem().text()          # saving = checked by you


def test_lookup_fills_only_empty_fields(gui, cfg, monkeypatch):
    monkeypatch.setattr(editing, "lookup_fields", lambda c, isbn: {
        "isbn13": "9789861371955", "source": "eslite", "fields": {"title": "From web", "author": "Web author"}})
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
    assert rows["b-S01-06"]["currency"] == "TWD" and rows["b-S01-07"]["price"] == "abc"   # filled only blanks
    assert rows["b-S01-05"]["condition"] == "like_new" and rows["b-S01-05"]["price"] == "5"


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
    assert w.list.count() == 2                                       # still listed: price + condition to fill
    ed.condition.setCurrentIndex(ed.condition.findData("like_new"))
    ed.fields["price"].setText("120")
    ed.fields["currency"].setText("TWD")
    ed._changed()
    assert ed.save()
    assert {r["sku"]: r for r in store.read_rows(cfg.csv_path)}["b-S01-06"]["status"] == "validated"


def test_new_books_from_the_bot_appear(gui, cfg):
    w = gui.ReviewWindow(cfg)
    gui.settle()
    store.append_rows(cfg.csv_path, [{"sku": "c-S01-01", "batch": "c", "set_id": "S01", "position": "1",
                                      "status": "needs_manual"}])
    w._csv_mtime = -1
    w._check_file()
    assert w.list.count() == 3


def test_unsaved_edits_are_not_lost_when_switching(gui, cfg):
    w = gui.ReviewWindow(cfg)
    gui.settle()
    w.editor.fields["notes"].setPlainText("remember the coffee stain")
    w.editor._changed()
    w.list.setCurrentRow(1)              # the dialog answers 'Save'
    gui.settle()
    assert {r["sku"]: r for r in store.read_rows(cfg.csv_path)}["b-S01-06"]["notes"] == "remember the coffee stain"
    assert w.editor.row["sku"] == "b-S01-07"
    assert "5 to fix" in w.list.item(0).text()   # book 6's entry updated after the save (ISBN, title, author)


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
    monkeypatch.setattr(editing, "lookup_fields", lambda c, isbn: {
        "isbn13": TW_ISBNS[5], "source": "eslite", "market_note": "New copy: 300 TWD",
        "fields": {"title": "T", "author": "A", "market_price": "300", "market_currency": "TWD",
                   "market_source": "eslite (title)", "market_url": "https://www.eslite.com/product/1",
                   "suggested_price": "120", "suggested_currency": "TWD", "price_basis": "40% of 300 TWD"}})
    w = gui.ReviewWindow(cfg)
    gui.settle()
    ed = w.editor
    ed.fields["isbn13"].setText(TW_ISBNS[5])
    ed.lookup_btn.click()
    gui.settle()
    assert ed.market_price.text() == "300" and ed.price.text() == "120" and ed.values()["market_source"] == "eslite (title)"
    assert ed.save()
    row = {r["sku"]: r for r in store.read_rows(cfg.csv_path)}["b-S01-06"]
    assert row["market_url"] == "https://www.eslite.com/product/1" and row["price_basis"] == "40% of 300 TWD"


def test_paste_shop_link(gui, cfg, monkeypatch):
    monkeypatch.setattr(editing, "market_from_link", lambda c, url, isbn: {
        "note": "New copy: 420 TWD from your link",
        "fields": {"market_price": "420", "market_currency": "TWD", "market_source": "eslite (your link)",
                   "market_url": url, "suggested_price": "168", "suggested_currency": "TWD", "price_basis": "40% of 420 TWD"}})
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
    rows[0]["status"] = "to_check"                         # b-S01-05: complete, just processed
    store.write_rows(cfg.csv_path, rows)
    w = gui.ReviewWindow(cfg)
    gui.settle()
    texts = [w.list.item(i).text() for i in range(w.list.count())]
    assert any("S01 #5" in t and "quick check" in t for t in texts)
    assert "to check" in w.count.text() and "to fix" in w.count.text()
    i = next(i for i, t in enumerate(texts) if "S01 #5" in t)
    w.list.setCurrentRow(i)
    gui.settle()
    assert w.editor.row["sku"] == "b-S01-05"
    assert w.editor.save()                                # nothing changed - 'Save / looks right'
    assert {r["sku"]: r for r in store.read_rows(cfg.csv_path)}["b-S01-05"]["status"] == "validated"
    assert "checked" in w.list.item(i).text()
