"""Photos are always taken in the order they were SENT (the bot names files by Telegram message number)."""
from PIL import Image

from pipeline.photos.ingest import list_photos, order_photos
from pipeline.photos.setlayout import split_by_count


def _shot(path):
    Image.new("RGB", (40, 30), "white").save(path)
    return path


def test_send_order_is_message_number_order(tmp_path):
    # 'Send as File' names keep the phone's name after the message number; the message number decides
    files = [_shot(tmp_path / n) for n in ("0000000103_IMG_0001.jpg", "0000000101.jpg", "0000000102_IMG_9999.jpg",
                                          "0000000099.jpg")]
    assert [p.name for p in order_photos(files)] == ["0000000099.jpg", "0000000101.jpg", "0000000102_IMG_9999.jpg",
                                                     "0000000103_IMG_0001.jpg"]
    sets = split_by_count(order_photos(files), 3, decode=lambda p: "" if p.name.startswith("0000000099") else
                          str(9789570000000 + int(p.name[7:10])))
    assert len(sets) == 1 and sets[0].front.name == "0000000099.jpg" and len(sets[0].books) == 3


def test_list_photos_ignores_other_files(tmp_path):
    _shot(tmp_path / "0000000002.jpg")
    _shot(tmp_path / "0000000001.jpg")
    (tmp_path / "0000000001.txt").write_text("good", encoding="utf-8")
    assert [p.name for p in list_photos(tmp_path, 0)] == ["0000000001.jpg", "0000000002.jpg"]


def test_settle_zero_never_drops_a_file_with_a_future_timestamp(tmp_path):
    # Windows: a file written a moment ago can have a modification time slightly after time.time()
    import os
    import time
    p = _shot(tmp_path / "0000000001.jpg")
    future = time.time() + 2
    os.utime(p, (future, future))
    assert [x.name for x in list_photos(tmp_path, 0)] == ["0000000001.jpg"]
    assert list_photos(tmp_path, 5) == []                 # still 'being written': skipped when settling
