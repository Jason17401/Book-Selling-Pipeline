"""Cutting each book out of the set's front photo."""
import json

import pytest
from PIL import Image

from pipeline.core import store
from pipeline.core.config import Config
from pipeline.photos import segment
from synth import TW_ISBNS, book_back, table_set

pytestmark = pytest.mark.skipif(not segment.available(), reason="needs OpenCV")


def iou(a, b):
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    return inter / ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter)


@pytest.mark.parametrize("kw", [{}, {"touching": True}, {"seed": 2}, {"seed": 3}, {"light_cover": 3},
                                {"seed": 4, "touching": True}, {"carpet": True, "seed": 6}])
def test_books_are_found_where_they_really_are(kw):
    img, truth = table_set(**kw)
    segs = segment.segment_books(img, 2, 5)
    assert len(segs) == 10 and all(s.found for s in segs)
    scores = [iou(s.box, t) for s, t in zip(segs, truth)]
    assert min(scores) > 0.9, scores                       # in book order: 1-5 top row, 6-10 bottom row


@pytest.mark.parametrize("kw", [{"shadow": 40}, {"shadow": 50, "seed": 7}, {"shadow": 40, "carpet": True},
                                {"shadow": 60, "carpet": True, "seed": 5},
                                {"shadow": 30, "carpet": True, "light_cover": 2, "seed": 8}])
def test_shadows_do_not_widen_the_box(kw):
    """Light from one side: every book casts a shadow to its right. The box must stay on the book itself - not
    reach into the shadow - so the cut-out cover sits in the middle with the same room left and right."""
    img, truth = table_set(**kw)
    segs = segment.segment_books(img, 2, 5)
    assert min(iou(s.box, t) for s, t in zip(segs, truth)) > 0.9
    for s, t in zip(segs, truth):
        assert abs(s.box[2] - t[2]) <= 12 and abs(s.box[0] - t[0]) <= 12, (s.box, t)     # right side not pushed out
    for s, t in zip(segs, truth):                     # the cover is cut around the box's centre: the book's centre
        assert abs((s.box[0] + s.box[2]) - (t[0] + t[2])) / 2 <= 6


def test_margin_stops_halfway_to_the_neighbour():
    a = segment.Segment((100, 100, 400, 500))
    b = segment.Segment((420, 100, 720, 500))                 # only 20 px to the right of a
    img = Image.new("RGB", (900, 700), (120, 120, 120))
    cut = segment.cut_book(img, a, 0.1, [a, b])
    assert cut.width == 300 + 2 * 10                           # 10 px each side (half the gap), not 30
    assert cut.height == 400 + 2 * 40                          # nothing above/below: the full 10%
    assert segment.cut_book(img, a, 0.1).width == 360          # without neighbours: the full margin


def test_turned_books_are_straightened():
    img, truth = table_set(tilt=5)
    segs = segment.segment_books(img, 2, 5)
    assert [round(abs(s.angle)) for s in segs] == [5] * 10
    assert all((s.angle > 0) == (i % 2 == 0) for i, s in enumerate(segs))     # each turned back the right way


def test_cover_has_the_margin_and_full_resolution():
    img, truth = table_set(seed=2)
    seg = segment.segment_books(img, 2, 5)[0]
    w, h = seg.box[2] - seg.box[0], seg.box[3] - seg.box[1]
    tight = segment.cut_book(img, seg, 0.0)
    roomy = segment.cut_book(img, seg, 0.05)
    assert tight.size == (w, h)
    assert abs(roomy.width - w * 1.1) <= 2 and abs(roomy.height - h * 1.1) <= 2
    assert w > 250                                          # cut from the full photo, not the small analysis copy


def test_grid_mode_and_no_books():
    img, _ = table_set()
    grid = segment.find_books(img, (2, 5), method="grid")
    assert not any(s.found for s in grid) and grid[0].box[:2] == (0, 0)
    empty = Image.new("RGB", (1500, 900), (150, 110, 75))
    segs = segment.segment_books(empty, 2, 5)
    assert len(segs) == 10 and not any(s.found for s in segs)            # nothing there: plain grid cells


def test_processing_saves_each_books_cover(tmp_path):
    from pipeline.photos.setlayout import process_sets
    cfg = Config(data_dir=tmp_path / "data", market_providers=(), set_size=10, grid=(2, 5), settle_seconds=0,
                 segment_margin=0.05)
    cfg.ensure_dirs()
    img, truth = table_set(seed=3)
    img.save(cfg.inbox / "0001.jpg", quality=95)
    for i in range(10):
        book_back(TW_ISBNS[i], size=(900, 1200), module=3.0, seed=i).save(cfg.inbox / f"{i + 2:04d}.jpg")
    s = process_sets(cfg, lookup=lambda i, c: {"title": "t", "author": "a"})
    rows = store.read_rows(cfg.csv_path)
    set_dir = cfg.processed / s.batch / "S01"
    data = json.loads((set_dir / "segments.json").read_text(encoding="utf-8"))
    assert len(data["books"]) == 10
    for i, r in enumerate(rows):
        cover = Image.open(r["front_photo"])
        tw, th = truth[i][2] - truth[i][0], truth[i][3] - truth[i][1]
        assert 1.0 < cover.width / tw < 1.2 and 1.0 < cover.height / th < 1.2     # the book + a little margin
        assert r["set_photo"].endswith("front.jpg")


def test_covers_redo_cuts_existing_covers_again(tmp_path, monkeypatch):
    import sys
    from pipeline.__main__ import main
    from pipeline.photos.setlayout import process_sets
    cfg = Config(data_dir=tmp_path / "data", market_providers=(), set_size=10, grid=(2, 5), settle_seconds=0)
    cfg.ensure_dirs()
    img, _ = table_set(seed=3, shadow=40, carpet=True)
    img.save(cfg.inbox / "0001.jpg", quality=95)
    for i in range(10):
        book_back(TW_ISBNS[i], size=(900, 1200), module=3.0, seed=i).save(cfg.inbox / f"{i + 2:04d}.jpg")
    process_sets(cfg, lookup=lambda i, c: {"title": "t", "author": "a"})
    cover = store.read_rows(cfg.csv_path)[0]["front_photo"]
    Image.new("RGB", (5, 5)).save(cover)                       # pretend: cut badly by an older version
    monkeypatch.setenv("DATA_DIR", str(cfg.data_dir))
    monkeypatch.setenv("GRID", "2x5")
    monkeypatch.setattr(sys, "argv", ["pipeline", "covers"])
    main()
    assert Image.open(cover).size == (5, 5)                    # without --redo: covers already cut are left alone
    monkeypatch.setattr(sys, "argv", ["pipeline", "covers", "--redo"])
    main()
    assert Image.open(cover).width > 250                       # cut again
    assert store.read_rows(cfg.csv_path)[0]["front_photo"] == cover
