"""What the review window does to the data: fixing fields writes only those fields back into books.csv."""
import pytest

from pipeline.apps import editing
from pipeline.core import store
from pipeline.core.config import Config
from synth import TW_ISBNS, book_back, set_front


@pytest.fixture
def cfg(tmp_path):
    c = Config(data_dir=tmp_path / "data")
    c.ensure_dirs()
    set_dir = tmp_path / "data" / "processed" / "b" / "S01"
    (set_dir / "06").mkdir(parents=True)
    front, barcode = set_dir / "front.jpg", set_dir / "06" / "barcode.jpg"
    set_front().save(front)
    book_back(TW_ISBNS[5], size=(300, 400)).save(barcode)
    rows = [
        {"sku": "b-S01-05", "batch": "b", "set_id": "S01", "position": "5", "isbn13": TW_ISBNS[4], "title": "Done",
         "author": "A", "condition": "like_new", "price": "5", "currency": "NZD", "front_photo": str(front),
         "barcode_photo": str(barcode), "status": "validated"},
        {"sku": "b-S01-06", "batch": "b", "set_id": "S01", "position": "6", "front_photo": str(front),
         "barcode_photo": str(barcode), "status": "needs_manual", "trademe_id": "keep-me"},
    ]
    store.write_rows(c.csv_path, rows)
    return c


def test_only_books_needing_attention_are_listed(cfg):
    rows = editing.list_rows(cfg)
    assert [r["sku"] for r in rows] == ["b-S01-06"]
    msgs = " ".join(i["msg"] for i in rows[0]["issues"])
    assert "ISBN missing" in msgs and "Title missing" in msgs
    assert "Price missing" in msgs and "Condition missing" in msgs        # both required
    assert [p["label"] for p in rows[0]["photos"]] == ["This book (front photo)", "Barcode photo", "Whole set"]
    assert len(editing.list_rows(cfg, only_attention=False)) == 2


def test_condition_and_price_are_required(cfg):
    r = editing.save_row(cfg, "b-S01-06", {"isbn13": "978-986-137-195-5", "title": "被討厭的勇氣", "author": "岸見一郎"})
    assert r["isbn13"] == "9789861371955" and r["status"] == "enriched"
    assert {i["field"] for i in r["issues"]} == {"condition", "price"}
    assert "no market price was found" in " ".join(i["msg"] for i in r["issues"])
    r = editing.save_row(cfg, "b-S01-06", {"condition": "like_new", "price": "150", "currency": "TWD"})
    assert r["status"] == "validated" and not r["issues"]
    rows = {x["sku"]: x for x in store.read_rows(cfg.csv_path)}
    assert rows["b-S01-06"]["trademe_id"] == "keep-me" and "manual" in rows["b-S01-06"]["source"]
    assert rows["b-S01-05"]["title"] == "Done"                              # other book untouched


def test_price_gets_default_currency_and_bad_values_are_flagged(cfg):
    base = {"isbn13": TW_ISBNS[5], "title": "T", "author": "A"}
    r = editing.save_row(cfg, "b-S01-06", {**base, "price": "$6.50", "condition": "good"})
    assert r["price"] == "6.50" and r["currency"] == "TWD" and r["status"] == "validated"
    r = editing.save_row(cfg, "b-S01-06", {"currency": "nzd"})
    assert r["currency"] == "NZD" and r["status"] == "validated"
    r = editing.save_row(cfg, "b-S01-06", {"price": "cheap", "condition": "meh"})
    assert r["status"] == "enriched" and {i["field"] for i in r["issues"]} == {"price", "condition"}


def test_isbn_typo_is_kept_and_flagged(cfg):
    r = editing.save_row(cfg, "b-S01-06", {"isbn13": "9789861371956"})
    assert r["isbn13"] == "9789861371956" and any("typo" in i["msg"] for i in r["issues"])


def test_save_does_not_lose_rows_added_meanwhile(cfg):
    store.append_rows(cfg.csv_path, [{"sku": "new-1", "status": "needs_manual"}])   # e.g. the bot processed more
    editing.save_row(cfg, "b-S01-06", {"title": "X"})
    assert "new-1" in [r["sku"] for r in store.read_rows(cfg.csv_path)]


def test_fill_set_only_fills_blanks(cfg):
    editing.save_row(cfg, "b-S01-06", {"condition": "good"})
    assert editing.fill_set(cfg, "b", "S01", "acceptable", "150", "twd") == 1   # only book 6's empty price
    rows = {x["sku"]: x for x in store.read_rows(cfg.csv_path)}
    assert rows["b-S01-06"]["condition"] == "good" and rows["b-S01-05"]["condition"] == "like_new"
    assert rows["b-S01-06"]["price"] == "150" and rows["b-S01-06"]["currency"] == "TWD"
    assert rows["b-S01-05"]["price"] == "5" and rows["b-S01-05"]["currency"] == "NZD"


def test_old_csv_is_upgraded(cfg):
    import csv
    with open(cfg.csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["sku", "price_nzd", "description", "back_crop", "status"])
        w.writeheader()
        w.writerow({"sku": "old", "price_nzd": "5", "description": "blurb", "back_crop": "x.jpg", "status": "enriched"})
    r = store.read_rows(cfg.csv_path)[0]
    assert r["price"] == "5" and r["currency"] == "NZD" and r["barcode_photo"] == "x.jpg"
    store.write_rows(cfg.csv_path, [r])
    assert "description" not in open(cfg.csv_path, encoding="utf-8-sig").readline()


def test_todo_text(cfg):
    t = editing.todo_text(cfg)
    assert "1 book(s) need attention" in t and "S01 #6" in t and "review.bat" in t


def test_thumbnails_including_the_book_cut_from_the_front_photo(cfg):
    photos = editing.list_rows(cfg)[0]["photos"]
    crop = editing.thumbnail_bytes(cfg, photos[0]["path"], 200, photos[0]["pos"])
    whole = editing.thumbnail_bytes(cfg, photos[2]["path"], 200, photos[2]["pos"])
    assert crop[:2] == whole[:2] == b"\xff\xd8" and crop != whole
    assert editing.thumbnail_bytes(cfg, photos[0]["path"], 200, photos[0]["pos"]) == crop      # cached
    assert list((cfg.cache_dir / "thumbs").glob("*.jpg"))
