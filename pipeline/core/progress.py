"""Live progress while photos are processed: a tidy in-place terminal display, and the Telegram bot's progress message.

The pipeline calls emit(kind, **data) at each step. Whoever is listening (see `listening`) gets the event; when nobody
listens (tests, `python -m pipeline ingest` without a terminal) nothing happens.

Events, in order, for a run:
    start      total=<books>, sets=<n>                 once, after the barcodes are read
    note       text=...                                a line to keep (e.g. where market prices are looked for)
    book       n, total, set_id, position, isbn        a new book starts
    step       phase, text                             what is being tried right now (replaced in place)
    result     phase, ok, text                         the outcome of a phase (kept)
    book_done  n, total, set_id, position, isbn, title, status, errors
    finish     books=<n>
phase is "barcodes", "book" (metadata lookup) or "market" (market price).

Log lines from pipeline.lookup / pipeline.market (e.g. "  eslite q=... : 0 result(s)") become `step` events while a
display is active, so every search shows up as it happens.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import sys
import threading
import unicodedata
from contextlib import contextmanager

_LOCAL = threading.local()
_phase = threading.local()


def _listeners() -> list:
    return getattr(_LOCAL, "listeners", [])


def emit(kind: str, **data) -> None:
    if kind == "step" and "phase" not in data:
        data["phase"] = getattr(_phase, "name", "")
    if kind in ("step", "result") and data.get("phase"):
        _phase.name = data["phase"]
    for listener in list(_listeners()):
        try:
            listener(kind, data)
        except Exception:  # a broken display must never stop the pipeline
            logging.getLogger("pipeline").debug("progress listener failed", exc_info=True)


def step(phase: str, text: str) -> None:
    emit("step", phase=phase, text=text)


def result(phase: str, ok: bool, text: str) -> None:
    emit("result", phase=phase, ok=ok, text=text)


@contextmanager
def listening(*listeners):
    """Send events from THIS thread (the one running the pipeline) to the listeners while the block runs."""
    old = _listeners()
    _LOCAL.listeners = old + [l for l in listeners if l]
    try:
        yield
    finally:
        _LOCAL.listeners = old


class LogToProgress(logging.Handler):
    """Turns the pipeline's INFO log lines (each search tried) into in-place `step` events."""

    def emit(self, record):
        if record.levelno >= logging.WARNING:
            emit("note", text=record.getMessage().strip(), warning=True)
        else:
            emit("step", text=record.getMessage().strip())


# ---- text helpers ----------------------------------------------------------------------------------
def bar(done: int, total: int, width: int = 20, full: str = "█", empty: str = "░") -> str:
    total = max(total, 1)
    filled = int(round(width * min(done, total) / total))
    return full * filled + empty * (width - filled)


def width_of(text: str) -> int:
    """Columns the text takes in a terminal (Chinese characters take two)."""
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)


def fit(text: str, columns: int) -> str:
    """Cut the text to `columns` terminal columns (so an in-place line never wraps)."""
    text = str(text).replace("\r", " ").replace("\n", " ").replace("\t", " ")
    if width_of(text) <= columns:
        return text
    out, used = "", 0
    for c in text:
        w = 2 if unicodedata.east_asian_width(c) in "WF" else 1
        if used + w > columns - 1:
            break
        out, used = out + c, used + w
    return out + "…"


def _enable_ansi(stream) -> bool:
    """True if the stream is a terminal that understands 'move the cursor up / clear the line'."""
    if os.environ.get("PLAIN_PROGRESS", "").strip() not in ("", "0") or not hasattr(stream, "isatty") or not stream.isatty():
        return False
    if os.name != "nt":
        return os.environ.get("TERM") != "dumb"
    try:  # Windows 10+: switch the console to 'virtual terminal' mode
        import ctypes
        k32 = ctypes.windll.kernel32
        handle = k32.GetStdHandle(-11 if stream is sys.stdout else -12)
        mode = ctypes.c_uint32()
        if not k32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(k32.SetConsoleMode(handle, mode.value | 0x0004)) or bool(mode.value & 0x0004)
    except Exception:
        return False


# ---- terminal display ------------------------------------------------------------------------------
LABEL = {"barcodes": "barcodes", "book": "book lookup", "market": "market price"}


class TerminalDisplay:
    """Per book:
        Book 3/10  S01 #3  ISBN 9789866920738
          book lookup   ✓ eslite: Google必修的圖表簡報術 - 許郁文 (旗標 2016)
          market price  … books.com.tw q=9789866920738 (isbn): no products      <- rewritten in place, then kept
        [██████░░░░░░░░░░░░░░]  2/10 books  20%                                  <- always the bottom line
    Without a terminal (output redirected to a file) it prints only the kept lines."""

    def __init__(self, stream=None, ansi=None, columns=None):
        self.out = stream or sys.stdout
        self.ansi = _enable_ansi(self.out) if ansi is None else ansi
        self.columns = columns
        enc = (getattr(self.out, "encoding", "") or "").lower()
        self.fancy = "utf" in enc
        self.ok, self.bad, self.busy = ("✓", "✗", "…") if self.fancy else ("OK", "--", "..")
        self.done = self.total = 0
        self.current = ""        # the line being rewritten in place
        self.drawn = 0           # how many live lines are on screen (current + bar)
        self.phase = ""
        self.lock = threading.Lock()

    def _cols(self) -> int:
        return self.columns or max(40, shutil.get_terminal_size((100, 20)).columns - 1)

    def _write(self, s: str):
        try:
            self.out.write(s)
        except UnicodeEncodeError:
            self.out.write(s.encode("ascii", "replace").decode())
        self.out.flush()

    def _bar_line(self) -> str:
        if not self.total:
            return ""
        pct = int(100 * self.done / self.total)
        full, empty = ("█", "░") if self.fancy else ("#", "-")
        return f"[{bar(self.done, self.total, 24, full, empty)}] {self.done}/{self.total} books  {pct}%"

    def _erase(self):
        if self.ansi and self.drawn:
            self._write("\r\x1b[2K" + "\x1b[1A\r\x1b[2K" * (self.drawn - 1))
        self.drawn = 0

    def _draw(self):
        if not self.ansi:
            return
        lines = [l for l in (self.current, self._bar_line()) if l]
        self._write("\n".join(fit(l, self._cols()) for l in lines))
        self.drawn = len(lines)

    def keep(self, line: str):
        """Print a line that stays (above the live lines)."""
        self._erase()
        self._write((fit(line, self._cols()) if self.ansi else line) + "\n")
        self._draw()

    def live(self, line: str):
        """Replace the in-place line."""
        self.current = line
        if self.ansi:
            self._erase()
            self._draw()

    def __call__(self, kind: str, d: dict):
        with self.lock:
            if kind == "start":
                self.total, self.done = d.get("total", 0), 0
                self.keep(f"Processing {self.total} book(s) in {d.get('sets', 1)} set(s)")
            elif kind == "note":
                self.keep(("  ! " if d.get("warning") else "") + d.get("text", ""))
            elif kind == "book":
                self.current = ""
                self.keep(f"Book {d['n']}/{d['total']}  {d.get('set_id', '')} #{d.get('position', '')}  "
                          f"ISBN {d.get('isbn') or '(barcode not read)'}")
            elif kind == "step":
                phase = d.get("phase") or self.phase
                self.phase = phase
                label = LABEL.get(phase, phase)
                self.live(f"  {label:<13} {self.busy} {d.get('text', '')}")
            elif kind == "result":
                label = LABEL.get(d.get("phase"), d.get("phase"))
                self.current = ""
                self.keep(f"  {label:<13} {self.ok if d.get('ok') else self.bad} {d.get('text', '')}")
            elif kind == "book_done":
                self.done = d.get("n", self.done + 1)
                if d.get("status") == "needs_manual":
                    self.keep(f"  {'status':<13} {self.bad} NEEDS MANUAL ({d.get('errors') or 'see review window'})")
                elif d.get("status") == "enriched":
                    self.keep(f"  {'status':<13} {self.bad} still to fill in the review window: {d.get('errors')}")
                else:
                    self._erase()
                    self._draw()
            elif kind == "finish":
                self.current = ""
                self._erase()
                self._draw()
                if self.ansi and self.drawn:
                    self._write("\n")
                self.drawn = 0


@contextmanager
def terminal(stream=None):
    """While the block runs: show the live display, and route the lookup/market log lines into it (instead of
    printing them as separate lines that would break the in-place display)."""
    display = TerminalDisplay(stream)
    handler = LogToProgress(level=logging.INFO)
    names = ("pipeline.lookup", "pipeline.market")
    saved = []
    for name in names:
        lg = logging.getLogger(name)
        saved.append((lg, lg.level, lg.propagate))
        lg.setLevel(logging.INFO)
        lg.propagate = False       # don't ALSO print them through the normal log output
        lg.addHandler(handler)
    try:
        with listening(display):
            yield display
    finally:
        for lg, level, prop in saved:
            lg.removeHandler(handler)
            lg.setLevel(level)
            lg.propagate = prop


class ChatProgress:
    """The progress message in the chat while /process runs, e.g.

        Processing  ▓▓▓▓▓▓░░░░░░░░░░  4/10 books (40%)
        Book 5 (S01 #5): market price - eslite: searching

    It is ONE message, edited as the work goes on (at most every EDIT_EVERY seconds - Telegram limits how often a
    message may be edited). A book that ends up needs_manual gets its own message straight away.
    Events arrive from the worker thread and are handled here on the bot's event loop."""

    EDIT_EVERY = 3.0

    def __init__(self, reply, loop=None, edit_every: float = None):
        self.reply = reply                       # async fn(text) -> Message: a new message in the chat
        self.loop = loop
        self.queue: asyncio.Queue = asyncio.Queue()
        self.every = self.EDIT_EVERY if edit_every is None else edit_every
        self.message = None
        self.done = self.total = 0
        self.now = "reading barcodes..."
        self.shown = ""
        self.manual = []
        self.finished = False

    # called from the worker thread
    def __call__(self, kind: str, data: dict):
        self.loop.call_soon_threadsafe(self.queue.put_nowait, (kind, dict(data)))

    def close(self):
        self.loop.call_soon_threadsafe(self.queue.put_nowait, ("_end", {}))

    def text(self) -> str:
        if not self.total:
            return f"Processing...\n{self.now}"
        pct = int(100 * self.done / self.total)
        head = f"Processing  {bar(self.done, self.total, 16, '▓', '░')}  {self.done}/{self.total} books ({pct}%)"
        if self.finished:
            return head + (f"\nDone - {len(self.manual)} book(s) need manual fixing." if self.manual else "\nDone.")
        return f"{head}\n{self.now}"

    def handle(self, kind: str, d: dict):
        """Update the state; returns a needs_manual notice to send, or None."""
        if kind == "start":
            self.total, self.done = d.get("total", 0), 0
        elif kind == "book":
            self.book = f"Book {d['n']} ({d.get('set_id', '')} #{d.get('position', '')})"
            self.now = f"{self.book}: starting"
        elif kind in ("step", "result"):
            label = LABEL.get(d.get("phase"), d.get("phase") or "")
            where = getattr(self, "book", "")
            line = " ".join(str(d.get("text", "")).split())[:90]
            self.now = f"{where}: {label} - {line}" if where else f"{label}: {line}"
        elif kind == "book_done":
            self.done = d.get("n", self.done + 1)
            if d.get("status") == "needs_manual":
                why = d.get("reason") or (f"missing {d['errors']}" if d.get("errors") else "see the review window")
                note = (f"NEEDS MANUAL - set {d.get('set_id', '')}, book {d.get('position', '')}: {why}. "
                        f"ISBN {d.get('isbn') or 'not read'}"
                        + (f", {d['title']}" if d.get("title") else "")
                        + ". Fix it in the review window.")
                self.manual.append(note)
                return note
        elif kind == "finish":
            self.done, self.finished = self.total, True
        return None

    async def show(self):
        text = self.text()
        if text == self.shown:
            return
        try:
            if self.message is None:
                self.message = await self.reply(text)
            else:
                await self.message.edit_text(text)
            self.shown = text
        except Exception as exc:   # an edit that fails (time-out, 'not modified') is not worth stopping for
            logging.getLogger('pipeline.bot').debug("progress message not updated: %s", exc)

    async def _send(self, text: str, attempts: int = 3):
        for attempt in range(attempts):
            try:
                await self.reply(text)
                return
            except Exception as exc:
                logging.getLogger('pipeline.bot').warning("could not send '%s...' (%s)", text[:30], exc)
                await asyncio.sleep(2 ** attempt)

    async def run(self):
        """Consume events until close(); edit the message at most every `every` seconds, and once at the end."""
        await self.show()
        last = asyncio.get_running_loop().time()
        dirty = False
        while True:
            try:
                kind, d = await asyncio.wait_for(self.queue.get(), timeout=max(self.every, 0.2))
            except asyncio.TimeoutError:
                kind, d = None, None
            if kind == "_end":
                break
            if kind is not None:
                note = self.handle(kind, d)
                dirty = True
                if note:
                    await self._send(note)
            now = asyncio.get_running_loop().time()
            if dirty and (now - last >= self.every or kind in ("start", "finish")):
                await self.show()
                last, dirty = now, False
        await self.show()
