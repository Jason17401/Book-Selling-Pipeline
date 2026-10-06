"""Live progress: the in-place terminal display and the Telegram progress message."""
import asyncio
import io
import logging

from pipeline.core import progress, store
from pipeline.core.config import Config
from pipeline.photos.setlayout import process_sets
from synth import TW_ISBNS, set_front


def _run(tmp_path, listener, set_size=3, unreadable=(2,), lookup_fails=()):
    cfg = Config(data_dir=tmp_path / "data", market_providers=(), set_size=set_size, grid=(1, set_size),
                 settle_seconds=0)
    cfg.ensure_dirs()
    set_front(set_size, (1, set_size)).save(cfg.inbox / "0001.jpg")
    truth = {}
    for i in range(set_size):
        name = f"{i + 2:04d}.jpg"
        (cfg.inbox / name).write_bytes(b"x")
        truth[name] = "" if i + 1 in unreadable else TW_ISBNS[i]

    def decode(p):
        return truth.get(p.name)

    def lookup(isbn, cfg):
        logging.getLogger("pipeline.lookup").info("  eslite q=%s: 1 result(s)", isbn)   # a search, as it happens
        if isbn in lookup_fails:
            return {"_errors": []}
        return {"title": f"Book {isbn[-3:]}", "author": "A", "publisher": "P", "source": "eslite"}

    events = []
    with progress.listening(lambda k, d: events.append((k, d)), listener):
        s = process_sets(cfg, decode=decode, lookup=lookup)
    return s, events


def test_events_cover_every_book_and_both_lookups(tmp_path):
    with progress.terminal(io.StringIO()):
        s, events = _run(tmp_path, None, lookup_fails=(TW_ISBNS[2],))
    kinds = [k for k, _ in events]
    assert kinds.count("book") == 3 and kinds.count("book_done") == 3 and kinds[-1] == "finish"
    start = [d for k, d in events if k == "start"][0]
    assert start["total"] == 3
    results = [(d["phase"], d["ok"]) for k, d in events if k == "result"]
    assert results == [("barcodes", False), ("book", True), ("book", False), ("book", False)]
    steps = [d["text"] for k, d in events if k == "step" and d["phase"] == "book"]
    assert any("eslite q=" in t for t in steps)                    # the log line of each search became a step
    done = {d["position"]: d for k, d in events if k == "book_done"}
    assert done[2]["status"] == "needs_manual" and done[2]["reason"] == "barcode not read"
    assert done[3]["status"] == "needs_manual" and done[3]["reason"] == "no book source found this ISBN"
    assert done[1]["status"] == "enriched" and done[1]["errors"] == "price"     # no market price -> price to fill
    rows = store.read_rows(tmp_path / "data" / "books.csv")
    assert {r["condition"] for r in rows} == {"like_new"}                       # the default condition


def test_terminal_display_plain_keeps_one_line_per_result(tmp_path):
    out = io.StringIO()
    display = progress.TerminalDisplay(out, ansi=False, columns=120)
    _run(tmp_path, display)
    text = out.getvalue()
    assert "Processing 3 book(s) in 1 set(s)" in text
    assert "Book 1/3  S01 #1  ISBN " + TW_ISBNS[0] in text
    assert "book lookup   OK eslite: Book " + TW_ISBNS[0][-3:] in text or "✓ eslite" in text
    assert "barcode not read" in text and "NEEDS MANUAL" in text
    assert "\x1b[" not in text                                     # no cursor codes when not a terminal


def test_terminal_display_rewrites_lines_in_place():
    out = io.StringIO()
    d = progress.TerminalDisplay(out, ansi=True, columns=60)
    d("start", {"total": 2, "sets": 1})
    d("book", {"n": 1, "total": 2, "set_id": "S01", "position": 1, "isbn": "9789869283533"})
    d("step", {"phase": "market", "text": "eslite: searching"})
    d("step", {"phase": "market", "text": "books.com.tw: searching"})
    d("result", {"phase": "market", "ok": True, "text": "420 TWD new"})
    d("book_done", {"n": 1, "total": 2, "status": "validated"})
    text = out.getvalue()
    assert "\x1b[1A\r\x1b[2K" in text                               # moved up and cleared: rewritten in place
    assert "1/2 books  50%" in text
    lines = [l for l in text.replace("\x1b[1A", "").replace("\x1b[2K", "").split("\n")]
    assert all(progress.width_of(l.split("\r")[-1]) <= 60 for l in lines)   # never wider than the terminal


def test_fit_counts_chinese_as_two_columns():
    assert progress.width_of("簡報abc") == 7
    assert progress.width_of(progress.fit("簡" * 40, 20)) <= 20


def test_chat_progress_edits_one_message_and_reports_needs_manual():
    sent, edits = [], []

    class Msg:
        async def edit_text(self, text):
            edits.append(text)

    async def reply(text):
        sent.append(text)
        return Msg()

    async def main():
        chat = progress.ChatProgress(reply, asyncio.get_running_loop(), edit_every=0)
        task = asyncio.create_task(chat.run())
        for kind, d in [("start", {"total": 2}),
                        ("book", {"n": 1, "total": 2, "set_id": "S01", "position": 1}),
                        ("step", {"phase": "book", "text": "eslite: looking up"}),
                        ("book_done", {"n": 1, "total": 2, "set_id": "S01", "position": 1, "isbn": "",
                                       "status": "needs_manual", "reason": "barcode not read"}),
                        ("book", {"n": 2, "total": 2, "set_id": "S01", "position": 2}),
                        ("book_done", {"n": 2, "total": 2, "status": "validated"}),
                        ("finish", {"books": 2})]:
            chat(kind, d)
        chat.close()
        await task

    asyncio.run(main())
    assert sent[0].startswith("Processing")                        # the progress message ...
    manual = [t for t in sent if t.startswith("NEEDS MANUAL")]       # ... plus one message per needs_manual book
    assert manual == ["NEEDS MANUAL - set S01, book 1: barcode not read. ISBN not read. Fix it in the review window."]
    assert len(sent) == 2 and edits                                  # everything else edits the same message
    assert "1/2 books (50%)" in "".join(edits) and edits[-1].startswith("Processing")
    assert "2/2 books (100%)" in edits[-1] and "1 book(s) need manual" in edits[-1]


def test_no_listener_no_cost(tmp_path):
    s, events = _run(tmp_path, None, unreadable=())
    assert len(store.read_rows(s and (tmp_path / "data" / "books.csv"))) == 3
