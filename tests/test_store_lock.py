"""books.csv is shared by the bot, the review window and the commands: no program may overwrite another's work."""
import multiprocessing as mp
import time

from pipeline.core import store
def _append_many(path, tag, n):
    for i in range(n):
        store.append_rows(path, [{"sku": f"{tag}-{i}", "status": "needs_manual"}])


def test_two_programs_appending_at_once_lose_nothing(tmp_path):
    path = tmp_path / "books.csv"
    ctx = mp.get_context("spawn")
    procs = [ctx.Process(target=_append_many, args=(path, tag, 25)) for tag in ("bot", "window")]
    for p in procs:
        p.start()
    for p in procs:
        p.join(60)
    skus = {r["sku"] for r in store.read_rows(path)}
    assert len(skus) == 50


def _hold_lock(path, seconds):
    with store.locked(path):
        time.sleep(seconds)


def test_lock_waits_then_gives_a_clear_error(tmp_path):
    path = tmp_path / "books.csv"
    ctx = mp.get_context("spawn")
    p = ctx.Process(target=_hold_lock, args=(path, 3))
    p.start()
    time.sleep(1.0)
    try:
        with store.locked(path, timeout=0.5):
            raise AssertionError("should not get the lock while another program holds it")
    except store.FileBusy as exc:
        assert "busy" in str(exc)
    p.join()
    with store.locked(path, timeout=1):   # free again
        pass


def test_lock_is_reentrant_in_one_program(tmp_path):
    path = tmp_path / "books.csv"
    store.update_rows(path, lambda rows: store.append_rows(path, [{"sku": "x"}]) or rows.clear())
    assert store.read_rows(path) == []   # outer write wins; the point is that it did not deadlock
