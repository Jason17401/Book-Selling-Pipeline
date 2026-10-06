"""Review window (PyQt6): see which books need attention, fix their fields, save, make listings.

    python -m pipeline review        or double-click review.bat

Layout:  [toolbar: show finished | Refresh | Make listings]
         [book list (red = needs something, green = ready)] | [editor for the selected book]

All reading/writing of data goes through pipeline/apps/editing.py (no Qt there), so the rules are the same everywhere:
a Save writes only that book's edited fields into data/books.csv, under a file lock shared with the Telegram bot.
Slow work (online look-ups, loading big photos, making listings) runs on a background thread so the window never freezes.
"""
from __future__ import annotations

import html
import sys
import traceback

from PyQt6.QtCore import QObject, QRunnable, Qt, QThreadPool, QTimer, QUrl, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QColor, QDesktopServices, QIcon, QKeySequence, QPixmap, QShortcut
from PyQt6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
                             QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QPlainTextEdit,
                             QPushButton, QScrollArea, QSplitter, QToolBar, QVBoxLayout, QWidget)

from . import editing
from ..core.config import Config
from ..core.validate import CONDITIONS

RED, GREEN = "#b42318", "#1e7a3c"
CONDITION_ORDER = [c for c in ("new", "like_new", "good", "acceptable", "poor") if c in CONDITIONS]
LINE_FIELDS = [("isbn13", "ISBN (13 digits)"), ("title", "Title"), ("author", "Author"), ("publisher", "Publisher"),
               ("year", "Year"), ("pages", "Pages")]
STYLE = f"""
QLineEdit[bad="true"], QComboBox[bad="true"], QPlainTextEdit[bad="true"] {{ background: #fdecea; border: 1px solid {RED}; }}
QLabel#issues {{ color: {RED}; }}
QLabel#heading {{ font-size: 18px; font-weight: 600; }}
"""


# ---- background work ---------------------------------------------------------------------------
_PENDING: set = set()   # keeps result relays alive until their answer has been delivered


class _Relay(QObject):
    """Lives in the GUI thread. The worker emits its signals; Qt queues them over to this object, so the callbacks
    (which touch widgets) always run on the GUI thread."""
    done = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, on_done, on_fail):
        super().__init__()
        self.on_done, self.on_fail = on_done, on_fail
        self.done.connect(self._deliver_done)
        self.failed.connect(self._deliver_failed)

    @pyqtSlot(object)
    def _deliver_done(self, result):
        _PENDING.discard(self)
        if self.on_done:
            self.on_done(result)

    @pyqtSlot(str)
    def _deliver_failed(self, message):
        _PENDING.discard(self)
        if self.on_fail:
            self.on_fail(message)


class Task(QRunnable):
    """Runs fn(*args) on the thread pool."""

    def __init__(self, fn, args, relay: _Relay):
        super().__init__()
        self.fn, self.args, self.relay = fn, args, relay

    def run(self):
        try:
            result = self.fn(*self.args)
        except Exception as exc:  # shown to the user, never swallowed
            traceback.print_exc()
            self.relay.failed.emit(str(exc))
        else:
            self.relay.done.emit(result)


def run_in_background(fn, *args, on_done=None, on_fail=None) -> None:
    """fn(*args) on a background thread; then on_done(result) or on_fail(message) on the GUI thread."""
    relay = _Relay(on_done, on_fail)
    _PENDING.add(relay)
    QThreadPool.globalInstance().start(Task(fn, args, relay))


def alive(widget) -> bool:
    """False if Qt already deleted the widget (e.g. you switched to another book while its photo was loading)."""
    try:
        widget.objectName()
        return True
    except RuntimeError:
        return False


def dot_icon(color: str) -> QIcon:
    pm = QPixmap(12, 12)
    pm.fill(QColor(color))
    return QIcon(pm)


def set_bad(widget: QWidget, bad: bool) -> None:
    widget.setProperty("bad", "true" if bad else "false")
    widget.style().unpolish(widget)
    widget.style().polish(widget)


# ---- photos ------------------------------------------------------------------------------------
class PhotoViewer(QDialog):
    """A big version of a photo, to read an ISBN off the back cover."""

    def __init__(self, cfg: Config, path: str, title: str, parent=None, pos: int = 0):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.label = QLabel("Loading...")
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        area = QScrollArea()
        area.setWidget(self.label)
        area.setWidgetResizable(True)
        lay = QVBoxLayout(self)
        lay.addWidget(area)
        self.resize(1000, 900)
        run_in_background(editing.thumbnail_bytes, cfg, path, 1800, pos, on_done=self._show,
                          on_fail=lambda m: alive(self.label) and self.label.setText(f"Could not open the photo: {m}"))

    def _show(self, data: bytes):
        if not alive(self.label):
            return
        pm = QPixmap()
        pm.loadFromData(data)
        self.label.setPixmap(pm)


class Thumb(QLabel):
    """A clickable photo thumbnail with a caption."""
    clicked = pyqtSignal()

    def __init__(self, caption: str):
        super().__init__("...")
        self.setFixedSize(150, 200)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet("background: #eee; border-radius: 4px;")
        self.setToolTip(f"{caption} - click to enlarge")
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_image(self, data: bytes):
        pm = QPixmap()
        pm.loadFromData(data)
        self.setPixmap(pm.scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio,
                                 Qt.TransformationMode.SmoothTransformation))

    def mousePressEvent(self, event):
        self.clicked.emit()


# ---- the editor for one book ---------------------------------------------------------------------
class BookEditor(QWidget):
    saved = pyqtSignal(dict)          # the book as stored after a save
    save_and_next = pyqtSignal()
    set_filled = pyqtSignal()

    def __init__(self, cfg: Config):
        super().__init__()
        self.cfg = cfg
        self.row: dict = {}
        self.dirty = False
        self._loading = False

        self.heading = QLabel("Choose a book on the left")
        self.heading.setObjectName("heading")
        self.status = QLabel("")
        self.issues = QLabel("")
        self.issues.setObjectName("issues")
        self.issues.setTextFormat(Qt.TextFormat.RichText)
        self.issues.setWordWrap(True)
        self.photos = QHBoxLayout()
        self.photos.setAlignment(Qt.AlignmentFlag.AlignLeft)

        self.fields: dict = {}
        form = QFormLayout()
        for name, label in LINE_FIELDS:
            edit = QLineEdit()
            edit.textEdited.connect(self._changed)
            self.fields[name] = edit
            if name == "isbn13":
                self.lookup_btn = QPushButton("Look up")
                self.lookup_btn.setToolTip("Fetch title, author, ... for this ISBN and fill the EMPTY fields")
                self.lookup_btn.clicked.connect(self.lookup)
                row = QHBoxLayout()
                row.addWidget(edit)
                row.addWidget(self.lookup_btn)
                form.addRow(label, row)
            else:
                form.addRow(label, edit)

        # market price of a NEW copy (from eslite / books.com.tw / NCL) and your price from it
        self.market_price = QLineEdit()
        self.market_price.setPlaceholderText("e.g. 300")
        self.market_currency = QLineEdit()
        self.market_currency.setPlaceholderText("TWD")
        self.market_currency.setMaxLength(3)
        self.market_currency.setMaximumWidth(70)
        for name, w in (("market_price", self.market_price), ("market_currency", self.market_currency)):
            w.textEdited.connect(self._changed)
            w.textEdited.connect(self._market_typed)
            self.fields[name] = w
        self.find_btn = QPushButton("Find")
        self.find_btn.setToolTip("Look up the price of a NEW copy on eslite / books.com.tw (by ISBN, then by title)")
        self.find_btn.clicked.connect(self.find_market)
        self.market_link = QLabel("")
        self.market_link.setOpenExternalLinks(True)
        self.market_link.setTextFormat(Qt.TextFormat.RichText)
        market_row = QHBoxLayout()
        market_row.addWidget(self.market_price)
        market_row.addWidget(QLabel("Currency"))
        market_row.addWidget(self.market_currency)
        market_row.addWidget(self.find_btn)
        market_row.addWidget(self.market_link, 1)
        form.addRow("Market price, new", market_row)
        # not found automatically (e.g. out of print)? search yourself, paste the shop's link - free, no limits
        self.web_btn = QPushButton("Search the web")
        self.web_btn.setToolTip("Opens a search for this ISBN on eslite / books.com.tw in your browser")
        self.web_btn.clicked.connect(self.search_web)
        self.link_edit = QLineEdit()
        self.link_edit.setPlaceholderText("paste an eslite.com/product/... or books.com.tw/products/... link")
        self.link_btn = QPushButton("Use link")
        self.link_btn.setToolTip("Read the list price (定價) from the shop page you pasted")
        self.link_btn.clicked.connect(self.use_link)
        link_row = QHBoxLayout()
        link_row.addWidget(self.web_btn)
        link_row.addWidget(self.link_edit, 1)
        link_row.addWidget(self.link_btn)
        form.addRow("Shop link", link_row)
        self._market_extra = {"market_source": "", "market_url": "", "market_match": "", "market_isbn": ""}

        # required: condition, price + currency
        self.condition = self._condition_box("- not set -")
        self.condition.activated.connect(self._changed)
        self.fields["condition"] = self.condition
        form.addRow("Condition", self.condition)
        self.price = QLineEdit()
        self.price.setPlaceholderText("e.g. 12.50")
        self.currency = QLineEdit()
        self.currency.setPlaceholderText(cfg.default_currency)
        self.currency.setMaxLength(3)
        self.currency.setMaximumWidth(70)
        self.currency.setToolTip(f"3-letter code: NZD, TWD, AUD ... Empty = {cfg.default_currency}")
        for name, w in (("price", self.price), ("currency", self.currency)):
            w.textEdited.connect(self._changed)
            self.fields[name] = w
        price_row = QHBoxLayout()
        price_row.addWidget(self.price)
        price_row.addWidget(QLabel("Currency"))
        price_row.addWidget(self.currency)
        self.ratio_btn = QPushButton(f"= {cfg.price_ratio:.0%} of market")
        self.ratio_btn.setToolTip(f"Your price = market price x {cfg.price_ratio:g}, in the market price's currency "
                                  "(PRICE_RATIO in .env)")
        self.ratio_btn.clicked.connect(self.use_ratio)
        price_row.addWidget(self.ratio_btn)
        self.price.textEdited.connect(self._price_typed)
        self.currency.textEdited.connect(self._price_typed)
        form.addRow("Price", price_row)
        self.basis = QLabel("")
        self.basis.setStyleSheet("color: #6b6a66;")
        form.addRow("", self.basis)
        self._basis = ""

        notes = QPlainTextEdit()
        notes.setFixedHeight(55)
        notes.textChanged.connect(self._changed)
        self.fields["notes"] = notes
        form.addRow("Your notes", notes)

        self.save_btn = QPushButton("Save  (Ctrl+S)")
        self.save_btn.clicked.connect(self.save)
        self.next_btn = QPushButton("Save && next  (Ctrl+Enter)")
        self.next_btn.clicked.connect(lambda: self.save() and self.save_and_next.emit())
        self.revert_btn = QPushButton("Undo my changes")
        self.revert_btn.clicked.connect(lambda: self.load(self.row))
        self.message = QLabel("")
        buttons = QHBoxLayout()
        for w in (self.save_btn, self.next_btn, self.revert_btn):
            buttons.addWidget(w)
        buttons.addWidget(self.message, 1)

        # whole-set helper
        self.set_box = QGroupBox("Whole set")
        self.set_condition = self._condition_box("condition...")
        self.set_price = QLineEdit()
        self.set_price.setPlaceholderText("price each")
        self.set_price.setMaximumWidth(110)
        self.set_currency = QLineEdit()
        self.set_currency.setPlaceholderText(cfg.default_currency)
        self.set_currency.setMaxLength(3)
        self.set_currency.setMaximumWidth(70)
        fill_btn = QPushButton("Fill empty ones")
        fill_btn.setToolTip("Give every book of this set that has NO condition / price yet these values")
        fill_btn.clicked.connect(self.fill_set)
        set_row = QHBoxLayout(self.set_box)
        for w in (self.set_condition, self.set_price, self.set_currency, fill_btn):
            set_row.addWidget(w)
        set_row.addStretch(1)

        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(self.heading)
        top.addStretch(1)
        top.addWidget(self.status)
        lay.addLayout(top)
        lay.addLayout(self.photos)
        lay.addWidget(self.issues)
        lay.addLayout(form)
        lay.addLayout(buttons)
        lay.addWidget(self.set_box)
        lay.addStretch(1)

        QShortcut(QKeySequence("Ctrl+S"), self).activated.connect(self.save)
        QShortcut(QKeySequence("Ctrl+Return"), self).activated.connect(self.next_btn.click)
        self.setEnabled(False)

    @staticmethod
    def _condition_box(empty_label: str) -> QComboBox:
        box = QComboBox()
        box.addItem(empty_label, "")
        for c in CONDITION_ORDER:
            box.addItem(c.replace("_", " "), c)
        return box

    # -- showing a book --
    def load(self, row: dict) -> None:
        self._loading = True
        self.row = row
        self.setEnabled(True)
        where = f"{row['set_id']} #{row['position']}" if row.get("set_id") else row.get("sku", "")
        self.heading.setText(where)
        self.set_box.setTitle(f"Whole set {row.get('set_id') or ''}")
        self.set_box.setVisible(bool(row.get("set_id")))
        for name, widget in self.fields.items():
            value = row.get(name, "") or ""
            if isinstance(widget, QComboBox):
                i = widget.findData(value)
                widget.setCurrentIndex(i if i >= 0 else 0)
            elif isinstance(widget, QPlainTextEdit):
                widget.setPlainText(value)
            else:
                widget.setText(value)
        self._market_extra = {k: row.get(k, "") for k in ("market_source", "market_url", "market_match", "market_isbn")}
        self._show_market_link()
        self._set_basis(row.get("price_basis", ""))
        self._show_issues(row)
        self._show_photos(row)
        self.dirty = False
        self.message.setText(f"Source: {row.get('source') or '-'}")
        self._loading = False

    def _show_issues(self, row: dict) -> None:
        issues = row.get("issues") or []
        bad = {i["field"] for i in issues}
        for name, widget in self.fields.items():
            set_bad(widget, name in bad)
        if issues:
            self.issues.setText("<ul style='margin:0'>" + "".join(f"<li>{html.escape(i['msg'])}</li>" for i in issues) + "</ul>")
            self.status.setText(f"<span style='color:{RED}'>{len(issues)} thing(s) to fix</span>")
        else:
            self.issues.setText("")
            self.status.setText(f"<span style='color:{GREEN}'>Ready</span>")

    def _show_photos(self, row: dict) -> None:
        while self.photos.count():
            item = self.photos.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        sku = row.get("sku")
        for p in row.get("photos") or []:
            box = QVBoxLayout()
            thumb = Thumb(p["label"])
            thumb.clicked.connect(lambda path=p["path"], label=p["label"], pos=p.get("pos", 0):
                                  PhotoViewer(self.cfg, path, label, self, pos).exec())
            caption = QLabel(p["label"])
            caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
            holder = QWidget()
            box.addWidget(thumb)
            box.addWidget(caption)
            holder.setLayout(box)
            self.photos.addWidget(holder)
            run_in_background(editing.thumbnail_bytes, self.cfg, p["path"], 400, p.get("pos", 0),
                              on_done=lambda data, t=thumb, s=sku: self._thumb_ready(t, s, data),
                              on_fail=lambda m, t=thumb: alive(t) and t.setText("photo\nmissing"))

    def _thumb_ready(self, thumb: "Thumb", sku: str, data: bytes) -> None:
        if self.row.get("sku") == sku and alive(thumb):
            thumb.set_image(data)

    def _changed(self, *_):
        if not self._loading:
            self.dirty = True
            self.message.setText("Unsaved changes")

    def _show_market_link(self) -> None:
        src, url = self._market_extra.get("market_source", ""), self._market_extra.get("market_url", "")
        text = f"<a href='{html.escape(url, quote=True)}'>{html.escape(src or 'shop page')}</a>" if url else html.escape(src)
        match = self._market_extra.get("market_match", "")
        if match in ("similar", "ebook"):   # not this exact book: say so plainly
            what = ("price of ANOTHER EDITION (same title and author)" if match == "similar"
                    else "price of the E-BOOK (same ISBN)")
            text += f"<br><b style='color:#b45309'>Note: {what} - check it</b>"
        self.market_link.setText(text)

    def _set_basis(self, basis: str) -> None:
        self._basis = basis or ""
        self.basis.setText({"": "", "manual": "price typed by you", "caption": "price from the Telegram caption"}
                           .get(self._basis, f"auto: {self._basis}"))

    def _market_typed(self, *_):
        self._market_extra = {"market_source": "typed by you", "market_url": "", "market_match": "manual",
                              "market_isbn": ""}
        self._show_market_link()

    def _price_typed(self, *_):
        self._set_basis("manual")

    def values(self) -> dict:
        out = {"price_basis": self._basis, **self._market_extra}
        for name, widget in self.fields.items():
            if isinstance(widget, QComboBox):
                out[name] = widget.currentData() or ""
            elif isinstance(widget, QPlainTextEdit):
                out[name] = widget.toPlainText()
            else:
                out[name] = widget.text()
        return out

    # -- actions --
    def save(self) -> bool:
        if not self.row:
            return False
        try:
            stored = editing.save_row(self.cfg, self.row["sku"], self.values())
        except Exception as exc:
            QMessageBox.warning(self, "Not saved", str(exc))
            return False
        self.load(stored)
        left = len(stored["issues"])
        self.message.setText("Saved - ready!" if not left else f"Saved - still {left} thing(s) to fix")
        self.saved.emit(stored)
        return True

    def lookup(self) -> None:
        isbn = self.fields["isbn13"].text()
        self.lookup_btn.setEnabled(False)
        self.lookup_btn.setText("Looking...")
        sku = self.row.get("sku")
        run_in_background(editing.lookup_fields, self.cfg, isbn,
                          on_done=lambda res: self._lookup_done(res, sku), on_fail=self._lookup_failed)

    def _lookup_done(self, res: dict, sku: str) -> None:
        self.lookup_btn.setEnabled(True)
        self.lookup_btn.setText("Look up")
        if self.row.get("sku") != sku:
            return  # you moved on to another book meanwhile
        if res.get("error"):
            self.message.setText(res["error"])
            return
        self.fields["isbn13"].setText(res["isbn13"])
        filled = self._apply_market(res["fields"], overwrite=False)
        for name, value in res["fields"].items():
            if name.startswith("market_") or name in ("suggested_price", "suggested_currency", "price_basis"):
                continue
            widget = self.fields.get(name)
            if widget is None:
                continue
            current = widget.toPlainText() if isinstance(widget, QPlainTextEdit) else widget.text()
            if not current.strip():
                widget.setPlainText(value) if isinstance(widget, QPlainTextEdit) else widget.setText(value)
                filled += 1
        self._changed()
        note = f"  {res['market_note']}" if res.get("market_note") else ""
        self.message.setText(f"Found via {res.get('source') or '-'}: filled {filled} empty field(s). Check, then Save.{note}")

    def _apply_market(self, fields: dict, overwrite: bool) -> int:
        """Put market price fields (and, if the price is still empty, the suggested price) into the form."""
        n = 0
        if fields.get("market_price") and (overwrite or not self.market_price.text().strip()):
            self.market_price.setText(fields["market_price"])
            self.market_currency.setText(fields.get("market_currency", "TWD"))
            self._market_extra = {k: fields.get(k, "") for k in ("market_source", "market_url", "market_match",
                                                                  "market_isbn")}
            self._show_market_link()
            n += 1
        if fields.get("suggested_price") and not self.price.text().strip() and self.cfg.auto_price:
            self.price.setText(fields["suggested_price"])
            self.currency.setText(fields["suggested_currency"])
            self._set_basis(fields.get("price_basis", ""))
            n += 1
        return n

    def find_market(self) -> None:
        isbn, title = self.fields["isbn13"].text(), self.fields["title"].text()
        self.find_btn.setEnabled(False)
        self.find_btn.setText("Finding...")
        sku = self.row.get("sku")

        def done(res: dict):
            self.find_btn.setEnabled(True)
            self.find_btn.setText("Find")
            if self.row.get("sku") != sku:
                return
            if self._apply_market(res.get("fields", {}), overwrite=True):
                self._changed()
            self.message.setText(res.get("note", ""))

        def failed(msg: str):
            self.find_btn.setEnabled(True)
            self.find_btn.setText("Find")
            self.message.setText(f"Market price look up failed: {msg}")
        author = self.fields["author"].text().strip() if "author" in self.fields else ""
        run_in_background(editing.market_for, self.cfg, isbn, title, True, author, on_done=done, on_fail=failed)

    def search_web(self) -> None:
        from ..sources.websearch import browser_search_url
        isbn = self.fields["isbn13"].text().strip()
        if not isbn:
            self.message.setText("Type the ISBN first.")
            return
        QDesktopServices.openUrl(QUrl(browser_search_url(isbn)))
        self.message.setText("Search opened in your browser: copy the product link, paste it into 'Shop link', press Use link.")

    def use_link(self) -> None:
        url, isbn = self.link_edit.text().strip(), self.fields["isbn13"].text()
        self.link_btn.setEnabled(False)
        self.link_btn.setText("Reading...")
        sku = self.row.get("sku")

        def done(res: dict):
            self.link_btn.setEnabled(True)
            self.link_btn.setText("Use link")
            if self.row.get("sku") != sku:
                return
            if self._apply_market(res.get("fields", {}), overwrite=True):
                self._changed()
                self.link_edit.clear()
            self.message.setText(res.get("note", ""))

        def failed(msg: str):
            self.link_btn.setEnabled(True)
            self.link_btn.setText("Use link")
            self.message.setText(f"Could not read that link: {msg}")
        run_in_background(editing.market_from_link, self.cfg, url, isbn, on_done=done, on_fail=failed)

    def use_ratio(self) -> None:
        res = editing.suggestion(self.cfg, self.market_price.text(), self.market_currency.text())
        if res.get("error"):
            self.message.setText(res["error"])
            return
        self.price.setText(res["price"])
        self.currency.setText(res["currency"])
        self._set_basis(res["price_basis"])
        self._changed()
        self.message.setText(f"Price set to {res['price']} {res['currency']} ({res['price_basis']})")

    def _lookup_failed(self, msg: str) -> None:
        self.lookup_btn.setEnabled(True)
        self.lookup_btn.setText("Look up")
        self.message.setText(f"Look up failed: {msg}")

    def fill_set(self) -> None:
        if not self.row.get("set_id"):
            return
        if self.dirty and not self.save():
            return
        try:
            n = editing.fill_set(self.cfg, self.row["batch"], self.row["set_id"],
                                 self.set_condition.currentData() or "", self.set_price.text(),
                                 self.set_currency.text())
        except Exception as exc:
            QMessageBox.warning(self, "Not saved", str(exc))
            return
        self.message.setText(f"Filled {n} empty field(s) in set {self.row['set_id']}")
        self.set_filled.emit()


# ---- the main window ---------------------------------------------------------------------------
class ReviewWindow(QMainWindow):
    def __init__(self, cfg: Config):
        super().__init__()
        self.cfg = cfg
        self.rows: list = []
        self._csv_mtime = self._mtime()
        self.setWindowTitle(f"Book review - {cfg.csv_path.resolve()}")
        self.resize(1250, 900)

        bar = QToolBar()
        bar.setMovable(False)
        self.addToolBar(bar)
        self.count = QLabel("")
        self.count.setStyleSheet("font-weight: 600; padding: 0 12px;")
        self.show_all = QCheckBox("Show finished books too")
        self.show_all.toggled.connect(lambda _: self.reload())
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(lambda: self.reload())
        self.listings_btn = QPushButton("Make listings for finished sets")
        self.listings_btn.clicked.connect(self.make_listings)
        self.banner = QLabel("")
        self.banner.setStyleSheet(f"color: {RED}; padding-left: 12px;")
        for w in (self.count, self.show_all, refresh, self.listings_btn, self.banner):
            bar.addWidget(w)

        self.list = QListWidget()
        self.list.setMinimumWidth(320)
        self.list.currentItemChanged.connect(self._select)
        self.editor = BookEditor(cfg)
        self.editor.saved.connect(self._saved)
        self.editor.save_and_next.connect(self.next_book)
        self.editor.set_filled.connect(lambda: self.reload(keep=self.editor.row.get("sku")))
        scroll = QScrollArea()
        scroll.setWidget(self.editor)
        scroll.setWidgetResizable(True)
        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(self.list)
        split.addWidget(scroll)
        split.setStretchFactor(1, 1)
        self.setCentralWidget(split)

        # the bot may add books while this window is open: check the file every few seconds
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._check_file)
        self.timer.start(3000)
        self.reload()

    def _mtime(self) -> float:
        try:
            return self.cfg.csv_path.stat().st_mtime
        except OSError:
            return 0.0

    def _check_file(self) -> None:
        m = self._mtime()
        if m == self._csv_mtime:
            return
        self._csv_mtime = m
        if self.editor.dirty:
            self.banner.setText("New data arrived (bot?) - press Refresh after saving")
        else:
            self.reload(keep=self.editor.row.get("sku"))

    def reload(self, keep: str = None) -> None:
        keep = keep or (self.editor.row.get("sku") if self.editor.row else None)
        if self.editor.dirty and not self._ask_save():
            return
        try:
            self.rows = editing.list_rows(self.cfg, only_attention=not self.show_all.isChecked())
        except Exception as exc:
            QMessageBox.warning(self, "Could not read books", str(exc))
            return
        self._csv_mtime = self._mtime()
        self.banner.setText("")
        self.list.blockSignals(True)
        self.list.clear()
        select = None
        for r in self.rows:
            item = QListWidgetItem(self._item_text(r))
            item.setData(Qt.ItemDataRole.UserRole, r["sku"])
            item.setIcon(dot_icon(RED if r["issues"] else GREEN))
            self.list.addItem(item)
            if r["sku"] == keep:
                select = item
        self.list.blockSignals(False)
        self._update_count()
        if select is None and self.list.count():
            select = self.list.item(0)
        if select is not None:
            self.list.setCurrentItem(select)
            self._select(select, None)
        else:
            self.editor.row = {}
            self.editor.setEnabled(False)
            self.editor.heading.setText("Nothing left to fix - press 'Make listings'" if not self.show_all.isChecked()
                                        else "No books yet")

    @staticmethod
    def _item_text(r: dict) -> str:
        where = f"{r['set_id']} #{r['position']}" if r.get("set_id") else r["sku"]
        name = r.get("title") or r.get("isbn13") or "(unknown book)"
        state = f"{len(r['issues'])} to fix" if r["issues"] else "ready"
        return f"{where}   {name[:34]}\n      {state}"

    def _update_count(self) -> None:
        red = sum(1 for r in self.rows if r["issues"])
        self.count.setText(f"{red} book(s) need attention" if red else "Nothing left to fix")

    def _ask_save(self) -> bool:
        """The current book has unsaved edits: Save / Discard / Cancel. Returns False on Cancel."""
        answer = QMessageBox.question(
            self, "Unsaved changes", "Save your changes to this book first?",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save)
        if answer == QMessageBox.StandardButton.Cancel:
            return False
        if answer == QMessageBox.StandardButton.Save:
            return self.editor.save()
        self.editor.dirty = False
        return True

    def _select(self, current, previous) -> None:
        if current is None:
            return
        sku = current.data(Qt.ItemDataRole.UserRole)
        if self.editor.row.get("sku") == sku and self.editor.dirty:
            return
        if self.editor.dirty and previous is not None and not self._ask_save():
            self.list.blockSignals(True)
            self.list.setCurrentItem(previous)
            self.list.blockSignals(False)
            return
        row = next((r for r in self.rows if r["sku"] == sku), None)
        if row:
            self.editor.load(row)

    def _saved(self, stored: dict) -> None:
        for i, r in enumerate(self.rows):
            if r["sku"] == stored["sku"]:
                self.rows[i] = stored
        for i in range(self.list.count()):   # not necessarily the current item: 'Save' may come from switching books
            item = self.list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == stored["sku"]:
                item.setText(self._item_text(stored))
                item.setIcon(dot_icon(RED if stored["issues"] else GREEN))
        self._csv_mtime = self._mtime()   # our own save is not 'new data'
        self._update_count()

    def next_book(self) -> None:
        """Go to the next book that still needs something (wrapping around)."""
        n = self.list.count()
        start = self.list.currentRow()
        for step in range(1, n + 1):
            i = (start + step) % n
            sku = self.list.item(i).data(Qt.ItemDataRole.UserRole)
            if any(r["sku"] == sku and r["issues"] for r in self.rows):
                self.list.setCurrentRow(i)
                return

    def make_listings(self) -> None:
        if self.editor.dirty and not self._ask_save():
            return
        self.listings_btn.setEnabled(False)
        self.listings_btn.setText("Making listings...")

        def done(res: dict):
            self._listings_finished()
            text = f"Made {len(res['made'])} listing(s) in:\n{res['folder']}"
            if res["skipped"]:
                text += "\n\nNot made:\n" + "\n".join(res["skipped"])
            QMessageBox.information(self, "Listings", text)

        def failed(msg: str):
            self._listings_finished()
            QMessageBox.warning(self, "Listings", msg)
        run_in_background(editing.make_listings, self.cfg, on_done=done, on_fail=failed)

    def _listings_finished(self) -> None:
        self.listings_btn.setEnabled(True)
        self.listings_btn.setText("Make listings for finished sets")

    def closeEvent(self, event):
        if self.editor.dirty and not self._ask_save():
            event.ignore()
            return
        event.accept()


def run(cfg: Config) -> None:
    cfg.ensure_dirs()
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("Book Selling Pipeline - review")
    app.setStyleSheet(STYLE)
    window = ReviewWindow(cfg)
    window.show()
    sys.exit(app.exec())
