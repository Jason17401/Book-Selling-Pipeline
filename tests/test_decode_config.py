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
