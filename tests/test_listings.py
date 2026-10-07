"""Set listings: numbered front photo + text; condition and price are optional, prices keep their currency."""
import json

from pipeline.core import store
from pipeline.core.config import Config
from pipeline.photos.ingest import parse_caption
from pipeline.apps.sets import build_set_listings
from synth import TW_ISBNS, book_back, set_front


def _rows(tmp_path, prices):
    front = tmp_path / "front.jpg"
    set_front(3, (1, 3)).save(front)
    rows = []
    for i, (price, cur) in enumerate(prices, 1):
        bc = tmp_path / f"b{i}.jpg"
        book_back(TW_ISBNS[i], size=(300, 400)).save(bc)
        rows.append({"sku": f"x-S01-{i:02d}", "batch": "x", "set_id": "S01", "position": str(i), "isbn13": TW_ISBNS[i],
                     "title": f"Title {i}", "author": "A", "price": price, "currency": cur,
                     "condition": "good" if i == 1 else "", "front_photo": str(front), "barcode_photo": str(bc),
                     "status": "validated"})
    return rows


def test_listing_with_mixed_optional_prices(tmp_path):
    cfg = Config(data_dir=tmp_path / "data", grid=(1, 3))
    res = build_set_listings(_rows(tmp_path, [("5", "NZD"), ("", ""), ("150", "TWD")]), cfg, with_backs=True)
    assert res["made"] == ["x-S01"]
    d = cfg.listings_dir / "sets" / "x-S01"
    text = (d / "listing.txt").read_text(encoding="utf-8")
    assert "Price (sum of the books): 5 NZD + 150 TWD" in text
    assert "1. Title 1 - A  (5 NZD)" in text and "2. Title 2 - A\n" in text and "良好" in text
    assert sorted(p.name for p in d.glob("*.jpg")) == ["01_front.jpg", "02_book01_barcode.jpg",
                                                       "03_book02_barcode.jpg", "04_book03_barcode.jpg"]
    assert json.loads((d / "listing.json").read_text(encoding="utf-8"))["sum_of_book_prices"] == {"NZD": 5.0, "TWD": 150.0}


def test_listing_without_any_price_and_with_set_price(tmp_path):
    cfg = Config(data_dir=tmp_path / "data", grid=(1, 3))
    rows = _rows(tmp_path, [("", "")] * 3)
    build_set_listings(rows, cfg)
    assert "Price: not set" in (cfg.listings_dir / "sets" / "x-S01" / "listing.txt").read_text(encoding="utf-8")
    build_set_listings(rows, cfg, set_price=20, set_currency="AUD", force=True)
    assert "Price: 20 AUD" in (cfg.listings_dir / "sets" / "x-S01" / "listing.txt").read_text(encoding="utf-8")


def test_incomplete_set_is_skipped(tmp_path):
    cfg = Config(data_dir=tmp_path / "data", grid=(1, 3))
    rows = _rows(tmp_path, [("", "")] * 3)
    rows[1]["status"] = "needs_manual"
    assert build_set_listings(rows, cfg)["skipped"] == [("x-S01", "book(s) 2 not complete yet")]
    store.write_rows(cfg.csv_path, rows)


def test_caption_parsing():
    assert parse_caption("good 5") == ("good", "5", "TWD")
    assert parse_caption("good 5", "NZD") == ("good", "5", "NZD")
    assert parse_caption("like new 150 NTD", "NZD") == ("like_new", "150", "TWD")
    assert parse_caption("front") == ("", "", "")
    assert parse_caption("poor") == ("poor", "", "")
