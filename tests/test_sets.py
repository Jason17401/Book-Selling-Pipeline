"""Sets: 1 front photo + SET_SIZE barcode photos per set."""
from pathlib import Path

from pipeline.core import store
from pipeline.core.config import Config
from pipeline.photos.setlayout import describe_progress, process_sets, progress, split_by_count
from synth import TW_ISBNS, book_back, set_front


def _photos(tmp_path, names):
    out = []
    for n in names:
        p = tmp_path / n
        p.write_bytes(b"x")
        out.append(p)
    return out


def _stream(tmp_path, isbn_lists, unreadable=(), drop=()):
    """Photo names + a fake decoder. isbn_lists = one list of ISBNs per set. unreadable/drop = (set, book) pairs."""
    names, truth = [], {}
    k = 0
    for si, isbns in enumerate(isbn_lists, 1):
        k += 1
        names.append(f"{k:04d}.jpg")
        truth[names[-1]] = ""
        for bi, isbn in enumerate(isbns, 1):
            if (si, bi) in drop:
                continue
            k += 1
            names.append(f"{k:04d}.jpg")
            truth[names[-1]] = "" if (si, bi) in unreadable else isbn
    photos = _photos(tmp_path, names)
    return photos, (lambda p: truth[p.name] or None)


def test_unreadable_backs_no_longer_split_the_set(tmp_path):
    # the reported failure: book 6 and book 8 unreadable
    photos, dec = _stream(tmp_path, [TW_ISBNS[:10]], unreadable={(1, 6), (1, 8)})
    new = split_by_count(photos, 10, dec)
    assert len(new) == 1 and len(new[0].books) == 10
    assert new[0].front == photos[0]
    assert [b.isbn for b in new[0].books] == [*TW_ISBNS[:5], "", TW_ISBNS[6], "", *TW_ISBNS[8:10]]


def test_several_sets_back_to_back(tmp_path):
    photos, dec = _stream(tmp_path, [TW_ISBNS[:10], TW_ISBNS[10:20]], unreadable={(1, 10), (2, 1)})
    sets = split_by_count(photos, 10, dec)
    assert [len(s.books) for s in sets] == [10, 10]
    assert sets[1].front == photos[11]
    assert sets[1].books[0].isbn == "" and sets[1].books[1].isbn == TW_ISBNS[11]
    assert not any(s.notes for s in sets)


def test_lost_photo_is_detected_and_next_set_stays_aligned(tmp_path):
    photos, dec = _stream(tmp_path, [TW_ISBNS[:10], TW_ISBNS[10:20]], drop={(1, 4)})
    sets = split_by_count(photos, 10, dec)
    assert len(sets) == 2
    assert len(sets[0].books) == 9 and any("MISSING" in n for n in sets[0].notes)
    assert sets[1].front == photos[10]                        # the real front of set 2
    assert [b.isbn for b in sets[1].books] == TW_ISBNS[10:20]


def test_retakes(tmp_path):
    photos, dec = _stream(tmp_path, [TW_ISBNS[:3]], unreadable={(1, 2)})
    extra = _photos(tmp_path, ["0003b.jpg"])[0]               # retake of book 2, captioned
    (tmp_path / "0003b.txt").write_text("retake", encoding="utf-8")
    dup = _photos(tmp_path, ["0004b.jpg"])[0]                 # same barcode as book 3 shot twice
    order = photos[:3] + [extra] + photos[3:] + [dup]
    truth = {extra.name: TW_ISBNS[1], dup.name: TW_ISBNS[2]}
    sets = split_by_count(order, 3, lambda p: truth.get(p.name) or dec(p))
    assert len(sets) == 1 and [b.isbn for b in sets[0].books] == TW_ISBNS[:3]
    assert sets[0].books[1].barcode_photo == extra and len(sets[0].books[2].photos) == 2


def test_front_caption_starts_short_set(tmp_path):
    photos, dec = _stream(tmp_path, [TW_ISBNS[:4], TW_ISBNS[4:14]])
    (tmp_path / photos[5].with_suffix(".txt").name).write_text("front good 5", encoding="utf-8")
    sets = split_by_count(photos, 10, dec)
    assert [len(s.books) for s in sets] == [4, 10]
    assert sets[1].front == photos[5]


def test_progress_counter(tmp_path):
    photos = _photos(tmp_path, [f"{i:04d}.jpg" for i in range(1, 14)])
    pr = progress(photos, 10)
    assert pr[0] == (1, 0) and pr[10] == (1, 10) and pr[11] == (2, 0) and pr[12] == (2, 1)
    assert "barcode 1/10" in describe_progress(photos, 10)


def test_process_sets_end_to_end(tmp_path):
    cfg = Config(data_dir=tmp_path / "data", market_providers=(), set_size=10, grid=(2, 5), settle_seconds=0)
    cfg.ensure_dirs()
    set_front().save(cfg.inbox / "0001.jpg", quality=92)
    (cfg.inbox / "0001.txt").write_text("good 150 TWD", encoding="utf-8")
    (cfg.inbox / "0003.txt").write_text("poor 3", encoding="utf-8")         # caption on book 2's barcode photo
    for i in range(10):
        img = book_back(TW_ISBNS[i], size=(900, 1200), module=3.0, seed=i)
        if i == 5:  # one unreadable back: blank photo
            img = img.crop((0, 0, 900, 500))
        img.save(cfg.inbox / f"{i + 2:04d}.jpg", quality=92)
    seen = []

    def lookup(isbn, cfg):
        seen.append(isbn)
        return {"title": f"T{isbn[-3:]}", "author": "A", "source": "fake"}

    s = process_sets(cfg, lookup=lookup)
    rows = store.read_rows(cfg.csv_path)
    assert len(rows) == 10 and {r["set_id"] for r in rows} == {"S01"}
    assert [r["isbn13"] for r in rows] == [*TW_ISBNS[:5], "", *TW_ISBNS[6:10]]
    assert rows[5]["status"] == "needs_manual" and "isbn13" in rows[5]["errors"]
    assert rows[0]["status"] == "to_check"                   # complete: waits for your quick check
    assert rows[0]["condition"] == "good" and rows[0]["price"] == "150" and rows[0]["currency"] == "TWD"
    assert rows[1]["condition"] == "poor" and rows[1]["price"] == "3" and rows[1]["currency"] == "TWD"
    assert set(store.COLUMNS) >= {"front_photo", "barcode_photo", "price", "currency"}
    assert not {"description", "front_crop", "back_crop", "back_photo", "photo_paths"} & set(store.COLUMNS)
    set_dir = cfg.processed / s.batch / "S01"
    assert (set_dir / "front.jpg").exists() and (set_dir / "review.jpg").exists()
    assert sorted(p.name for p in set_dir.iterdir() if p.is_dir()) == [f"{i:02d}" for i in range(1, 11)]
    assert Path(rows[0]["set_photo"]) == set_dir / "front.jpg" and Path(rows[0]["barcode_photo"]).parent.name == "01"
    assert Path(rows[0]["front_photo"]) == set_dir / "01" / "cover.jpg" and Path(rows[0]["front_photo"]).exists()
    assert (set_dir / "segments.json").exists()
    assert not list(cfg.inbox.iterdir())                      # nothing left behind
    assert len(seen) == 9


def test_process_sets_rotation_keeps_original(tmp_path):
    cfg = Config(data_dir=tmp_path / "data", market_providers=(), set_size=2, grid=(1, 2), settle_seconds=0,
                 rotate=90)
    cfg.ensure_dirs()
    set_front(2, (1, 2)).rotate(90, expand=True).save(cfg.inbox / "0001.jpg")
    for i in range(2):
        book_back(TW_ISBNS[i], size=(900, 1200), module=3.0, seed=i).save(cfg.inbox / f"{i + 2:04d}.jpg")
    s = process_sets(cfg, lookup=lambda i, c: {"title": "t", "author": "a"})
    set_dir = cfg.processed / s.batch / "S01"
    from PIL import Image
    with Image.open(set_dir / "front.jpg") as im:
        assert im.width > im.height                           # turned upright (landscape)
    assert (set_dir / "original" / "0001.jpg").exists()
