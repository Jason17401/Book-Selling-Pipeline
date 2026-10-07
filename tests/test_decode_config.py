"""Barcode reading on synthetic photos, and .env parsing."""
import os

import pytest

from pipeline.photos import decode
from pipeline.core.config import Config
from pipeline.core.isbn import normalize
from synth import TW_ISBNS, book_back, ean13_bits, with_check


def test_synthetic_barcode_is_valid_ean():
    assert normalize(TW_ISBNS[0]) == TW_ISBNS[0] and len(ean13_bits(TW_ISBNS[0])) == 95


@pytest.mark.parametrize("kw", [
    dict(module=3.0),                                  # plain
    dict(module=2.5, jpeg=70, noise=20),               # phone-quality JPEG
    dict(module=2.0, size=(700, 1000)),                # Telegram-compressed 'Photo' size
    dict(module=3.0, angle=12),                        # tilted
])
def test_reads_isbn_not_the_taiwan_price_barcode(kw):
    isbn = TW_ISBNS[3]
    img = book_back(isbn, price_code=with_check("471000012345"), **kw)
    assert decode.decode_isbn_image(img) == isbn


def test_blank_photo_reads_nothing():
    from PIL import Image
    assert decode.decode_isbn_image(Image.new("RGB", (800, 600), "white"), effort="fast") is None


def test_env_parsing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for k in list(os.environ):
        if k in ("ROTATE", "BACK_ROTATE", "BOOK_PROVIDERS", "BOOK_PROVIDERS_TW", "LAYOUT", "SET_SPLIT", "DEFAULT_CURRENCY"):
            monkeypatch.delenv(k)
    (tmp_path / ".env").write_text("ROTATE=90      # comment\nDEFAULT_CURRENCY=nzd       \n"
                                   "BOOK_PROVIDERS_TW=eslite, books_tw,ncl\n", encoding="utf-8")
    cfg = Config.from_env()
    assert cfg.rotate == 90 and cfg.default_currency == "NZD"
    assert Config().default_currency == "TWD"
    assert cfg.providers_tw == ("eslite", "books_tw", "ncl")


def test_env_rejects_typo_provider(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BOOK_PROVIDERS", "openlibary")
    with pytest.raises(SystemExit):
        Config.from_env()


def test_price_add_on_is_read_with_the_isbn(tmp_path):
    import pytest
    from pipeline.photos import decode as dec
    from synth import TW_ISBNS, book_back
    if dec.zxingcpp is None and getattr(getattr(dec.pyzbar, "ZBarSymbol", None), "EAN5", None) is None:
        pytest.skip("needs zxing-cpp (or zbar with EAN-5) to read add-ons")
    dec.ADDONS.clear()
    img = book_back(TW_ISBNS[0], size=(1200, 1600), module=3.0, addon="00350", price_code="")
    assert dec.decode_isbn_image(img) == TW_ISBNS[0]
    assert dec.ADDONS.get(TW_ISBNS[0]) == "00350"


def test_add_on_search_area_is_right_of_the_isbn_barcode():
    from PIL import Image
    from pipeline.photos.decode import Detection, addon_crops, read_addon
    img = Image.new("RGB", (1000, 800), "white")
    crops = addon_crops(img, Detection("9789861371955", cx=300, cy=400, width=200))
    assert len(crops) == 1 and crops[0].width >= 1800 * 0.99           # enlarged
    assert addon_crops(img, Detection("9789861371955")) == []         # no position known: whole photo only
    assert read_addon(img, "9789861371955") == ""                      # nothing there: no crash, no guess
