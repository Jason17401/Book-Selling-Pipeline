"""Review window (PyQt6): check and fix the books of each set, then sell them as products.

    python -m pipeline review        or double-click review.bat

Layout:  [toolbar: books to fix / to check | Show checked books too | Refresh (Ctrl+R) | Sell products (Ctrl+L)]
         [books grouped by set, each set folds open/closed] | [editor for the selected book]
'Sell products' opens the Sell window: one row per product - what you put on Shopee: a set of books (later also
single books) - with its title, total price,
description and listing status, and the button that makes the Shopee upload file.

All reading/writing of data goes through pipeline/apps/editing.py (no Qt there), so the rules are the same everywhere:
a Save writes only that book's edited fields into data/books.csv, under a file lock shared with the Telegram bot.
Slow work (online look-ups, loading big photos, making the upload file) runs on a background thread so the window never
freezes.
"""

from __future__ import annotations

import html
import sys
import traceback
from pathlib import Path

from PyQt6.QtCore import (
    QEvent,
    QObject,
    QPoint,
    QRect,
    QRunnable,
    QSize,
    Qt,
    QThreadPool,
    QTimer,
    QUrl,
    pyqtSignal,
    pyqtSlot,
)
from PyQt6.QtGui import QColor, QDesktopServices, QFont, QIcon, QKeySequence, QPainter, QPen, QPixmap, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStyledItemDelegate,
    QToolBar,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.config import Config
from ..core.validate import CONDITION_GRADES
from ..sources.genre import join_genres, split_genres
from . import editing

# ---- look -------------------------------------------------------------------------------------
# A calm blue theme. Status colours are dark enough to read on white (WCAG AA) and always come with words too.
RED, GREEN, AMBER, BLUE = "#b42318", "#15803d", "#b45309", "#1d4ed8"
INK, MUTED, LINE, PAGE, ACCENT, ACCENT_DARK, ACCENT_SOFT = (
    "#0f172a",
    "#475569",
    "#cbd5e1",
    "#f3f6fb",
    "#2563eb",
    "#1e40af",
    "#dbeafe",
)
SET_ROLE = Qt.ItemDataRole.UserRole.value + 1  # where a set heading in the book tree keeps its set code
LINE_FIELDS = [
    ("isbn13", "ISBN (13 digits)"),
    ("title", "Title"),
    ("author", "Author"),
    ("publisher", "Publisher"),
    ("year", "Year"),
    ("pages", "Pages"),
    ("genre", "Genres"),
]
STYLE = f"""
QMainWindow, QDialog {{ background: {PAGE}; }}
QWidget {{ color: {INK}; font-size: 10pt; }}
QScrollArea, QScrollArea > QWidget#qt_scrollarea_viewport,
QScrollArea > QWidget#qt_scrollarea_viewport > QWidget {{ background: transparent; border: none; }}
QToolBar {{ background: #ffffff; border: none; border-bottom: 1px solid {LINE}; padding: 6px 10px; spacing: 8px; }}
QLabel#heading {{ font-size: 15pt; font-weight: 600; color: {INK}; }}
QLabel#title {{ font-size: 13pt; font-weight: 600; color: {ACCENT_DARK}; }}
QLabel#muted, QLabel#caption {{ color: {MUTED}; }}
QLabel#caption {{ font-size: 9pt; }}
QLabel#issues {{ color: {RED}; background: #fef2f2; border: 1px solid #fecaca; border-radius: 6px; padding: 6px 8px; }}
QLabel#banner {{ color: {RED}; font-weight: 600; }}
QLabel#price {{ font-size: 13pt; font-weight: 700; color: {ACCENT_DARK}; }}
QLineEdit#setPrice {{ font-size: 12pt; font-weight: 700; color: {ACCENT_DARK}; }}
QFrame#card {{ background: #ffffff; border: 1px solid {LINE}; border-radius: 10px; }}
QLabel#cardTitle {{ font-size: 11pt; font-weight: 600; color: {ACCENT_DARK}; padding-bottom: 6px;
                    border-bottom: 1px solid #e6ecf5; }}
QLineEdit, QComboBox, QPlainTextEdit {{ background: #ffffff; border: 1px solid {LINE}; border-radius: 6px;
                                        padding: 5px 7px; selection-background-color: {ACCENT_SOFT};
                                        selection-color: {INK}; }}
QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus {{ border: 1px solid {ACCENT}; background: #f8fbff; }}
QLineEdit:disabled, QLineEdit:read-only {{ background: #f6f8fb; color: {MUTED}; }}
QComboBox {{ padding-right: 30px; combobox-popup: 0; }}
QComboBox:hover {{ border-color: #93b4f5; }}
QComboBox::drop-down {{ subcontrol-origin: padding; subcontrol-position: center right; width: 28px; border: none;
                        background: transparent; }}
QComboBoxPrivateContainer {{ background: #ffffff; border: none; padding: 0; }}
QComboBox QAbstractItemView {{ background: #ffffff; border: 1px solid {LINE}; border-radius: 6px; padding: 4px;
                               outline: 0; selection-background-color: {ACCENT_SOFT}; selection-color: {INK}; }}
QComboBox QAbstractItemView::item {{ min-height: 30px; padding: 0 10px; border-radius: 4px; color: {INK}; }}
QComboBox QAbstractItemView::item:hover {{ background: #eff5ff; }}
QComboBox QAbstractItemView::item:selected {{ background: {ACCENT_SOFT}; color: {INK}; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #c3cfe0; border-radius: 4px; min-height: 36px; }}
QScrollBar::handle:horizontal {{ background: #c3cfe0; border-radius: 4px; min-width: 36px; }}
QScrollBar::handle:hover {{ background: #9fb3cf; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QToolTip {{ background: #ffffff; color: {INK}; border: 1px solid {LINE}; padding: 4px 6px; }}
QLineEdit[bad="true"], QComboBox[bad="true"], QPlainTextEdit[bad="true"], GenreEditor[bad="true"] {{
    background: #fef2f2; border: 1px solid {RED}; }}
QPushButton, QToolButton {{ background: #ffffff; border: 1px solid {LINE}; border-radius: 6px; padding: 6px 12px; }}
QPushButton:hover, QToolButton:hover {{ background: #eff5ff; border-color: #93b4f5; }}
QPushButton:pressed, QToolButton:pressed {{ background: {ACCENT_SOFT}; }}
QPushButton:focus, QToolButton:focus {{ border: 1px solid {ACCENT}; }}
QCheckBox:focus {{ color: {ACCENT_DARK}; }}
QPushButton:disabled {{ color: #94a3b8; background: #f1f5f9; }}
QPushButton[primary="true"] {{ background: {ACCENT}; color: #ffffff; border: 1px solid {ACCENT}; font-weight: 600; }}
QPushButton[primary="true"]:hover {{ background: {ACCENT_DARK}; border-color: {ACCENT_DARK}; }}
QPushButton[primary="true"]:disabled {{ background: #93b4f5; border-color: #93b4f5; }}
QTreeWidget {{ background: #ffffff; border: 1px solid {LINE}; border-radius: 10px; padding: 6px; outline: 0;
               show-decoration-selected: 0; }}
QTreeWidget::branch {{ background: transparent; border: none; }}
QTreeWidget::item {{ padding: 5px 6px; border-radius: 6px; border: none; }}
QTreeWidget::item:hover {{ background: #eff5ff; }}
QTreeWidget::item:selected {{ background: {ACCENT_SOFT}; color: {INK}; }}
QFrame#chip {{ background: #e8f0fe; border: 1px solid #bfd3fb; border-radius: 13px; }}
QFrame#chip QLabel {{ color: {ACCENT_DARK}; }}
QToolButton#chipX {{ border: none; background: transparent; padding: 0 4px; color: {ACCENT_DARK}; font-weight: 700; }}
QToolButton#chipX:hover {{ color: {RED}; }}
QToolButton#addChip {{ border: 1px dashed #93b4f5; border-radius: 13px; padding: 3px 10px; color: {ACCENT_DARK}; }}
QLabel#thumb {{ background: #e9eef6; border: 1px solid {LINE}; border-radius: 6px; }}
QLabel#thumb:hover {{ border: 1px solid {ACCENT}; }}
QSplitter::handle {{ background: transparent; width: 8px; }}
"""


# ---- background work ---------------------------------------------------------------------------
_PENDING: set = set()  # keeps result relays alive until their answer has been delivered


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
    """A small round status dot."""
    pm = QPixmap(32, 32)  # drawn big, shown small: sharp at any Windows scaling
    pm.fill(QColor(0, 0, 0, 0))
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(color))
    p.drawEllipse(6, 6, 20, 20)
    p.end()
    return QIcon(pm)


class Card(QFrame):
    """A white panel with its title inside, at the top (instead of a group box's title cut into its border)."""

    def __init__(self, title: str = ""):
        super().__init__()
        self.setObjectName("card")
        self.outer = QVBoxLayout(self)
        self.outer.setContentsMargins(18, 12, 18, 16)
        self.outer.setSpacing(10)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("cardTitle")
        self.outer.addWidget(self.title_label)

    def setTitle(self, title: str) -> None:
        self.title_label.setText(title)


def flat_combo(box: QComboBox) -> QComboBox:
    """A dropdown in the window's flat look: its list styled like the rest (the item delegate lets the style sheet
    reach each row) and without the shadow Windows adds to pop-ups."""
    box.setItemDelegate(QStyledItemDelegate(box))
    try:
        popup = box.view().window()
        popup.setWindowFlags(popup.windowFlags() | Qt.WindowType.NoDropShadowWindowHint)
    except Exception:
        pass
    return box


UPLOAD_TEXT = "Make Shopee upload file"


def open_folder(path) -> None:
    """Show a folder in Explorer (Finder on a Mac)."""
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(path).resolve())))


def hbox(spacing: int = 8) -> QHBoxLayout:
    """A row of widgets with a little room between them (fields, labels and buttons never touch)."""
    row = QHBoxLayout()
    row.setSpacing(spacing)
    row.setContentsMargins(0, 0, 0, 0)
    return row


def with_note(row, note: QLabel) -> QVBoxLayout:
    """A row with a small note right under it, in ONE form row (a form row of its own left a big gap around it)."""
    box = QVBoxLayout()
    box.setSpacing(3)
    box.setContentsMargins(0, 0, 0, 0)
    if isinstance(row, QWidget):
        box.addWidget(row)
    else:
        box.addLayout(row)
    box.addWidget(note)
    return box


def show_note(note: QLabel, text: str) -> None:
    """Set a note's text; an empty note takes no room at all."""
    note.setText(text)
    note.setVisible(bool(text))


def form_layout() -> QFormLayout:
    form = QFormLayout()
    form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
    form.setHorizontalSpacing(14)
    form.setVerticalSpacing(10)
    return form


def set_bad(widget: QWidget, bad: bool) -> None:
    widget.setProperty("bad", "true" if bad else "false")
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def primary(button: QPushButton) -> QPushButton:
    """The main action of its area: filled blue."""
    button.setProperty("primary", "true")
    return button


def no_default_buttons(dialog) -> None:
    """In a dialog, Enter in a text box would 'click' the first button (e.g. start the Shopee upload): not here."""
    for b in dialog.findChildren(QPushButton):
        b.setAutoDefault(False)
        b.setDefault(False)


def screen_size(widget: QWidget, fraction: float = 0.9) -> QSize:
    """`fraction` of the screen the widget is on (so a window always fits, whatever the screen)."""
    try:
        screen = widget.screen() or QApplication.primaryScreen()
        g = screen.availableGeometry()
        return QSize(int(g.width() * fraction), int(g.height() * fraction))
    except Exception:  # no screen known yet
        return QSize(1200, 800)


# ---- photos ------------------------------------------------------------------------------------
class ZoomLabel(QLabel):
    """The picture in the photo viewer: left click = bigger, right click = smaller (at the point clicked)."""

    zoom_in = pyqtSignal(object)
    zoom_out = pyqtSignal(object)

    def mousePressEvent(self, event):
        pos = event.position().toPoint()
        if event.button() == Qt.MouseButton.LeftButton:
            self.zoom_in.emit(pos)
        elif event.button() == Qt.MouseButton.RightButton:
            self.zoom_out.emit(pos)


class PhotoViewer(QDialog):
    """A photo, big. It always opens showing the WHOLE photo fitted to the window (so every photo appears at the same
    size), in a window sized to your screen. Bigger / smaller: the buttons, + and -, left / right click on the photo,
    or Ctrl + mouse wheel. 0 = fit again, 1 = actual size."""

    STEP = 1.25

    def __init__(
        self, cfg: Config, path: str, title: str, parent=None, pos: int = 0, numbered: bool = False, mark: int = 0
    ):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)  # its big photo is freed when you close it
        self.setWindowTitle(title)
        self.pix = None
        self.zoom = None  # None = fit the whole photo into the window
        self.label = ZoomLabel("Loading...")
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.zoom_in.connect(lambda p: self.zoom_by(self.STEP, p))
        self.label.zoom_out.connect(lambda p: self.zoom_by(1 / self.STEP, p))
        self.area = QScrollArea()
        self.area.setWidget(self.label)
        self.area.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.area.viewport().installEventFilter(self)
        self.area.setStyleSheet(
            "QScrollArea, QScrollArea > QWidget#qt_scrollarea_viewport { background: #1e293b; border-radius: 6px; }"
        )
        self.smaller_btn = QPushButton("−  Smaller")
        self.smaller_btn.setToolTip("Zoom out (-, or right-click the photo)")
        self.smaller_btn.clicked.connect(lambda: self.zoom_by(1 / self.STEP))
        self.fit_btn = QPushButton("Fit to window  (0)")
        self.fit_btn.clicked.connect(self.fit)
        self.bigger_btn = QPushButton("+  Bigger")
        self.bigger_btn.setToolTip("Zoom in (+, or left-click the photo)")
        self.bigger_btn.clicked.connect(lambda: self.zoom_by(self.STEP))
        self.actual_btn = QPushButton("Actual size  (1)")
        self.actual_btn.clicked.connect(lambda: self.set_zoom(1.0))
        self.scale = QLabel("")
        self.scale.setObjectName("muted")
        hint = QLabel("Left-click: bigger · Right-click: smaller · Ctrl+wheel: zoom")
        hint.setObjectName("muted")
        bar = hbox()
        for w in (self.smaller_btn, self.fit_btn, self.bigger_btn, self.actual_btn, self.scale):
            bar.addWidget(w)
        bar.addStretch(1)
        bar.addWidget(hint)
        lay = QVBoxLayout(self)
        lay.addLayout(bar)
        lay.addWidget(self.area, 1)
        for keys, fn in (
            ("+", lambda: self.zoom_by(self.STEP)),
            ("=", lambda: self.zoom_by(self.STEP)),
            ("-", lambda: self.zoom_by(1 / self.STEP)),
            ("0", self.fit),
            ("1", lambda: self.set_zoom(1.0)),
        ):
            QShortcut(QKeySequence(keys), self).activated.connect(fn)
        self.resize(screen_size(self, 0.9))
        no_default_buttons(self)
        run_in_background(
            editing.thumbnail_bytes, cfg, path, 2400, pos, numbered, mark, on_done=self._show, on_fail=self._failed
        )

    def _failed(self, message: str):
        if alive(self.label):
            self.label.setText(f"Could not open the photo: {message}")
            self.label.adjustSize()

    def _show(self, data: bytes):
        if not alive(self.label):
            return
        pm = QPixmap()
        pm.loadFromData(data)
        self.pix = pm
        self._apply()

    def fit_scale(self) -> float:
        if self.pix is None or self.pix.width() <= 0 or self.pix.height() <= 0:
            return 1.0
        vp = self.area.viewport().size()
        return max(0.02, min((vp.width() - 4) / self.pix.width(), (vp.height() - 4) / self.pix.height()))

    def current_scale(self) -> float:
        return self.fit_scale() if self.zoom is None else self.zoom

    def _apply(self) -> None:
        if self.pix is None:
            return
        s = self.current_scale()
        w, h = max(1, int(self.pix.width() * s)), max(1, int(self.pix.height() * s))
        scaled = self.pix.scaled(w, h, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        self.label.setPixmap(scaled)
        self.label.resize(scaled.size())
        self.scale.setText(f"{s:.0%}" + ("  (fitted)" if self.zoom is None else ""))

    def set_zoom(self, value: float, anchor=None) -> None:
        old = self.current_scale()
        self.zoom = max(0.05, min(8.0, value))
        h, v = self.area.horizontalScrollBar(), self.area.verticalScrollBar()
        ax = anchor.x() if anchor is not None else h.value() + self.area.viewport().width() // 2
        ay = anchor.y() if anchor is not None else v.value() + self.area.viewport().height() // 2
        off_x, off_y = ax - h.value(), ay - v.value()  # where that point is on screen: keep it there
        self._apply()
        r = self.zoom / old if old else 1.0
        h.setValue(int(ax * r - off_x))
        v.setValue(int(ay * r - off_y))

    def zoom_by(self, factor: float, anchor=None) -> None:
        self.set_zoom(self.current_scale() * factor, anchor)

    def fit(self) -> None:
        self.zoom = None
        self._apply()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.zoom is None:
            self._apply()

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self._apply)  # the window has its real size now: fit to it

    def eventFilter(self, obj, event):
        """Ctrl + mouse wheel over the photo zooms (before the scroll bars can take the wheel)."""
        if event.type() == QEvent.Type.Wheel and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.zoom_by(self.STEP if event.angleDelta().y() > 0 else 1 / self.STEP)
            return True
        return super().eventFilter(obj, event)


class Thumb(QLabel):
    """A clickable photo thumbnail - every one the same size, the photo fitted inside."""

    clicked = pyqtSignal()
    SIZE = 160

    def __init__(self, caption: str):
        super().__init__("...")
        self.setObjectName("thumb")
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setToolTip(f"{caption} - click to see it big")
        self.setAccessibleName(caption)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_image(self, data: bytes):
        pm = QPixmap()
        pm.loadFromData(data)
        self.setPixmap(
            pm.scaled(
                self.SIZE - 8,
                self.SIZE - 8,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def mousePressEvent(self, event):
        self.clicked.emit()


# ---- genres: one chip per genre ------------------------------------------------------------------
class FlowLayout(QLayout):
    """Lays its widgets out left to right and wraps to the next line at the right edge (like words in a text)."""

    def __init__(self, parent=None, spacing: int = 6):
        super().__init__(parent)
        self._flow_items = []
        self.setSpacing(spacing)
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):
        self._flow_items.append(item)

    def count(self):
        return len(self._flow_items)

    def itemAt(self, i):
        return self._flow_items[i] if 0 <= i < len(self._flow_items) else None

    def takeAt(self, i):
        return self._flow_items.pop(i) if 0 <= i < len(self._flow_items) else None

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._arrange(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._arrange(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._flow_items:
            size = size.expandedTo(item.minimumSize())
        return size

    def _arrange(self, rect, test_only: bool) -> int:
        x, y, line = rect.x(), rect.y(), 0
        space = self.spacing()
        for item in self._flow_items:
            if item.widget() is not None and item.widget().isHidden():  # e.g. the add box while not adding
                continue
            hint = item.sizeHint()
            if x + hint.width() > rect.right() + 1 and line > 0:  # does not fit: next line
                x, y, line = rect.x(), y + line + space, 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + space
            line = max(line, hint.height())
        return y + line - rect.y()


class GenreEditor(QWidget):
    """The book's genres as chips: x removes one, '+ Add genre' adds one (Enter to add, Esc to cancel). Chips run
    left to right and wrap downwards. Behaves like a line edit for the rest of the window: text() / setText() give
    the genres as stored in books.csv ('童書 > 冒險／驚悚小說')."""

    textEdited = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._genres: list = []
        self._chips: list = []
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)  # so the red 'to fix' look shows
        self.flow = FlowLayout(self)
        policy = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)
        self.input = QLineEdit()
        self.input.setPlaceholderText("new genre - Enter to add")
        self.input.setMinimumWidth(190)
        self.input.returnPressed.connect(self._finish_add)
        self.input.setVisible(False)
        esc = QShortcut(QKeySequence("Escape"), self.input)
        esc.setContext(Qt.ShortcutContext.WidgetShortcut)  # only while typing a genre
        esc.activated.connect(self._cancel_add)
        self.add_btn = QToolButton()
        self.add_btn.setObjectName("addChip")
        self.add_btn.setText("+  Add genre")
        self.add_btn.setAccessibleName("Add a genre")
        self.add_btn.clicked.connect(self._start_add)
        self._rebuild()

    # the line-edit-like interface
    def text(self) -> str:
        return join_genres(self._genres)

    def setText(self, text: str) -> None:
        self._genres = split_genres(text)
        self._rebuild()

    def genres(self) -> list:
        return list(self._genres)

    def add(self, text: str) -> None:
        new = [g for g in split_genres(text) if g not in self._genres]
        if new:
            self._genres += new
            self._rebuild()
            self.textEdited.emit(self.text())

    def remove(self, genre: str) -> None:
        if genre in self._genres:
            self._genres.remove(genre)
            self._rebuild()
            self.textEdited.emit(self.text())

    def _start_add(self) -> None:
        self.input.clear()
        self.input.setVisible(True)
        self.add_btn.setVisible(False)
        self.input.setFocus()
        self.flow.invalidate()

    def _finish_add(self) -> None:
        text = self.input.text()
        self._cancel_add()
        if text.strip():
            self.add(text)

    def _cancel_add(self) -> None:
        self.input.setVisible(False)
        self.add_btn.setVisible(True)
        self.flow.invalidate()

    def _chip(self, genre: str) -> QFrame:
        chip = QFrame()
        chip.setObjectName("chip")
        row = QHBoxLayout(chip)
        row.setContentsMargins(10, 2, 4, 2)
        row.setSpacing(2)
        row.addWidget(QLabel(genre))
        x = QToolButton()
        x.setObjectName("chipX")
        x.setText("×")
        x.setToolTip(f"Remove '{genre}'")
        x.setAccessibleName(f"Remove genre {genre}")
        x.clicked.connect(lambda _=False, g=genre: self.remove(g))
        row.addWidget(x)
        chip.remove_button = x
        return chip

    def _rebuild(self) -> None:
        while self.flow.count():
            self.flow.takeAt(0)
        for chip in self._chips:  # Qt deletes them later (never while a chip's own x is being clicked)
            chip.hide()
            chip.deleteLater()
        self._chips = [self._chip(g) for g in self._genres]
        for chip in self._chips:
            self.flow.addWidget(chip)
        self.flow.addWidget(self.input)
        self.flow.addWidget(self.add_btn)
        self.flow.invalidate()
        self.updateGeometry()


# ---- the editor for one book ---------------------------------------------------------------------
class BookEditor(QWidget):
    saved = pyqtSignal(dict)  # the book as stored after a save
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
        self.status.setTextFormat(Qt.TextFormat.RichText)
        self.issues = QLabel("")
        self.issues.setObjectName("issues")
        self.issues.setTextFormat(Qt.TextFormat.RichText)
        self.issues.setWordWrap(True)
        self.issues.setVisible(False)
        self.photos = hbox()
        self.photos.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.photos.setSpacing(10)

        self.fields: dict = {}
        book_box = Card("Book")
        form = form_layout()
        book_box.outer.addLayout(form)
        for name, label in LINE_FIELDS:
            edit = GenreEditor() if name == "genre" else QLineEdit()
            edit.textEdited.connect(self._changed)
            self.fields[name] = edit
            if name in ("year", "pages"):  # short numbers: short boxes
                edit.setMaximumWidth(140)
            if name == "isbn13":
                edit.setMaximumWidth(260)
                self.lookup_btn = QPushButton("Look up")
                self.lookup_btn.setToolTip("Fetch title, author, ... for this ISBN and fill the EMPTY fields")
                self.lookup_btn.clicked.connect(self.lookup)
                row = hbox()
                row.addWidget(edit)
                row.addWidget(self.lookup_btn)
                row.addStretch(1)
                form.addRow(label, row)
            else:
                form.addRow(label, edit)

        # market price of a NEW copy (from eslite / books.com.tw / NCL) and your price from it
        price_box = Card("Price")
        pform = form_layout()
        price_box.outer.addLayout(pform)
        self.market_price = QLineEdit()
        self.market_price.setPlaceholderText("e.g. 300")
        self.market_price.setMaximumWidth(160)
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
        self.market_link.setWordWrap(False)  # wrapping made Qt reserve extra height above and below it
        self.market_link.setObjectName("muted")
        market_row = hbox()
        market_row.addWidget(self.market_price)
        market_row.addWidget(QLabel("Currency"))
        market_row.addWidget(self.market_currency)
        market_row.addWidget(self.find_btn)
        market_row.addStretch(1)
        pform.addRow("Market price, new", with_note(market_row, self.market_link))
        show_note(self.market_link, "")
        # not found automatically (e.g. out of print)? search yourself, paste the shop's link - free, no limits
        self.web_btn = QPushButton("Search the web")
        self.web_btn.setToolTip("Opens a search for this ISBN on eslite / books.com.tw in your browser")
        self.web_btn.clicked.connect(self.search_web)
        self.link_edit = QLineEdit()
        self.link_edit.setPlaceholderText("paste an eslite.com/product/... or books.com.tw/products/... link")
        self.link_btn = QPushButton("Use link")
        self.link_btn.setToolTip("Read the list price (定價) from the shop page you pasted")
        self.link_btn.clicked.connect(self.use_link)
        link_row = hbox()
        link_row.addWidget(self.web_btn)
        link_row.addWidget(self.link_edit, 1)
        link_row.addWidget(self.link_btn)
        pform.addRow("Shop link", link_row)
        self._market_extra = {"market_source": "", "market_url": "", "market_match": "", "market_isbn": ""}

        # required: condition, price + currency
        self.condition = self._condition_box("- not set -")
        self.condition.setMaximumWidth(300)
        self.condition.activated.connect(self._changed)
        self.fields["condition"] = self.condition
        pform.addRow("Condition", self.condition)
        self.price = QLineEdit()
        self.price.setPlaceholderText("e.g. 120")
        self.price.setMaximumWidth(160)
        self.currency = QLineEdit()
        self.currency.setPlaceholderText(cfg.default_currency)
        self.currency.setMaxLength(3)
        self.currency.setMaximumWidth(70)
        self.currency.setToolTip(f"3-letter code: TWD, NZD, AUD ... Empty = {cfg.default_currency}")
        for name, w in (("price", self.price), ("currency", self.currency)):
            w.textEdited.connect(self._changed)
            self.fields[name] = w
        price_row = hbox()
        price_row.addWidget(self.price)
        price_row.addWidget(QLabel("Currency"))
        price_row.addWidget(self.currency)
        self.ratio_btn = QPushButton(f"= {cfg.price_ratio:.0%} of market")
        self.ratio_btn.setToolTip(
            f"Your price = market price x {cfg.price_ratio:g}, in the market price's currency (PRICE_RATIO in .env)"
        )
        self.ratio_btn.clicked.connect(self.use_ratio)
        price_row.addWidget(self.ratio_btn)
        price_row.addStretch(1)
        self.price.textEdited.connect(self._price_typed)
        self.currency.textEdited.connect(self._price_typed)
        self.basis = QLabel("")
        self.basis.setObjectName("muted")
        pform.addRow("Your price", with_note(price_row, self.basis))
        show_note(self.basis, "")
        self._basis = ""

        notes = QPlainTextEdit()
        notes.setFixedHeight(60)
        notes.textChanged.connect(self._changed)
        self.fields["notes"] = notes
        pform.addRow("Your notes", notes)

        self.save_btn = primary(QPushButton("Save / looks right  (Ctrl+S)"))
        self.save_btn.setToolTip("Saves your changes. With nothing missing, this also marks the book as checked.")
        self.save_btn.clicked.connect(self.save)
        self.next_btn = QPushButton("Save && next  (Ctrl+Enter)")
        self.next_btn.clicked.connect(lambda: self.save() and self.save_and_next.emit())
        self.revert_btn = QPushButton("Undo my changes")
        self.revert_btn.clicked.connect(lambda: self.load(self.row))
        self.message = QLabel("")
        self.message.setObjectName("muted")
        self.message.setWordWrap(True)
        buttons = hbox()
        for w in (self.save_btn, self.next_btn, self.revert_btn):
            buttons.addWidget(w)
        buttons.addWidget(self.message, 1)

        # whole-set helper
        self.set_box = Card("Whole set")
        self.set_condition = self._condition_box("Condition...")
        self.set_condition.setMinimumWidth(200)
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
        hint = QLabel("Give every book of this set that has no condition / price yet:")
        hint.setObjectName("muted")
        self.set_box.outer.addWidget(hint)
        set_row = hbox()
        self.set_box.outer.addLayout(set_row)
        for w in (self.set_condition, self.set_price, self.set_currency, fill_btn):
            set_row.addWidget(w)
        set_row.addStretch(1)
        # how this set is sold: as one product (all its books together), or each book as its own product
        self.sell_mode = flat_combo(QComboBox())
        self.sell_mode.addItem("As one product - all its books together", "set")
        self.sell_mode.addItem("Each book on its own - a product per book", "book")
        self.sell_mode.setMinimumWidth(320)
        self.sell_mode.setToolTip(
            "Only this set. Each book on its own: in Sell products every book of the set is its "
            "own product - title = the book's title, its own price, photos = its front (cut out "
            "of the set photo) and its back. Can't be changed while the set or one of its books "
            "is listed, ordered or sold."
        )
        self.sell_mode.activated.connect(self._sell_mode_chosen)
        mode_row = hbox()
        mode_row.addWidget(QLabel("Sell this set:"))
        mode_row.addWidget(self.sell_mode)
        mode_row.addStretch(1)
        self.set_box.outer.addLayout(mode_row)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 14)
        lay.setSpacing(10)
        top = hbox()
        top.addWidget(self.heading)
        top.addStretch(1)
        top.addWidget(self.status)
        lay.addLayout(top)
        lay.addLayout(self.photos)
        lay.addWidget(self.issues)
        lay.addWidget(book_box)
        lay.addWidget(price_box)
        lay.addLayout(buttons)
        lay.addWidget(self.set_box)
        lay.addStretch(1)

        QShortcut(QKeySequence("Ctrl+S"), self).activated.connect(self.save)
        QShortcut(QKeySequence("Ctrl+Return"), self).activated.connect(self.next_btn.click)
        self.setEnabled(False)

    @staticmethod
    def _condition_box(empty_label: str) -> QComboBox:
        box = flat_combo(QComboBox())
        box.addItem(empty_label, "")
        for code, zh, en, grade, meaning, meaning_zh in CONDITION_GRADES:
            box.addItem(f"{zh}  {en}", code)
            box.setItemData(
                box.count() - 1, f"TAAZE grade {grade}: {meaning}\n{meaning_zh}", Qt.ItemDataRole.ToolTipRole
            )
        return box

    # -- showing a book --
    def load(self, row: dict) -> None:
        self._loading = True
        self.row = row
        self.setEnabled(True)
        self.heading.setText(
            f"Book #{row['position']}  ·  {row.get('title') or row.get('isbn13') or 'not identified yet'}"
            if row.get("set_id")
            else row.get("sku", "")
        )
        self.set_box.setTitle(f"Whole set {row.get('set_id') or ''}  ·  batch {row.get('batch') or ''}")
        self.set_box.setVisible(bool(row.get("set_id")))
        self._show_sell_mode(row)
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
        self.message.setText(f"Book details from: {row.get('book_source') or '-'}")
        self._loading = False

    def _show_issues(self, row: dict) -> None:
        issues = row.get("issues") or []
        bad = {i["field"] for i in issues}
        for name, widget in self.fields.items():
            set_bad(widget, name in bad)
        if issues:
            self.issues.setVisible(True)
            self.issues.setText(
                "<ul style='margin:0; padding-left:14px'>"
                + "".join(f"<li>{html.escape(i['msg'])}</li>" for i in issues)
                + "</ul>"
            )
            self.status.setText(f"<span style='color:{RED}; font-weight:600'>● {len(issues)} thing(s) to fix</span>")
        elif row.get("to_check"):
            self.issues.setVisible(False)
            self.issues.setText("")
            self.status.setText(
                f"<span style='color:{AMBER}; font-weight:600'>● To check: are the photos the same "
                "book, title, condition and price right? Then Save</span>"
            )
        else:
            self.issues.setVisible(False)
            self.issues.setText("")
            word = {"listed": "On sale (its set is listed)", "sold": "Sold"}.get(
                row.get("status"), "Checked - ready to sell"
            )
            self.status.setText(f"<span style='color:{GREEN}; font-weight:600'>● {word}</span>")

    def _open_photo(self, p: dict) -> None:
        """One photo big, in its own window (fit to the window, zoom in and out)."""
        PhotoViewer(
            self.cfg, p["path"], p["label"], self, p.get("pos", 0), p.get("numbered", False), p.get("mark", 0)
        ).exec()

    def _show_photos(self, row: dict) -> None:
        while self.photos.count():
            item = self.photos.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        sku = row.get("sku")
        for p in row.get("photos") or []:
            box = QVBoxLayout()
            box.setSpacing(4)
            thumb = Thumb(p["label"])
            thumb.clicked.connect(lambda p=p: self._open_photo(p))
            caption = QLabel(p["label"])
            caption.setObjectName("caption")
            caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
            holder = QWidget()
            box.setContentsMargins(0, 0, 0, 0)
            box.addWidget(thumb)
            box.addWidget(caption)
            holder.setLayout(box)
            self.photos.addWidget(holder)
            run_in_background(
                editing.thumbnail_bytes,
                self.cfg,
                p["path"],
                400,
                p.get("pos", 0),
                p.get("numbered", False),
                p.get("mark", 0),
                on_done=lambda data, t=thumb, s=sku: self._thumb_ready(t, s, data),
                on_fail=lambda m, t=thumb: alive(t) and t.setText("photo\nmissing"),
            )

    def _thumb_ready(self, thumb: "Thumb", sku: str, data: bytes) -> None:
        if self.row.get("sku") == sku and alive(thumb):
            thumb.set_image(data)

    def _changed(self, *_):
        if not self._loading:
            self.dirty = True
            self.message.setText("Unsaved changes")

    def _show_market_link(self) -> None:
        src, url = self._market_extra.get("market_source", ""), self._market_extra.get("market_url", "")
        text = (
            f"<a href='{html.escape(url, quote=True)}'>{html.escape(src or 'shop page')}</a>"
            if url
            else html.escape(src)
        )
        text = f"Source: {text}" if text else ""
        match = self._market_extra.get("market_match", "")
        if match in ("similar", "ebook"):  # not this exact book: say so plainly
            what = (
                "price of ANOTHER EDITION (same title and author)"
                if match == "similar"
                else "price of the E-BOOK (same ISBN)"
            )
            text += f"<br><b style='color:#b45309'>Note: {what} - check it</b>"
        show_note(self.market_link, text)

    def _set_basis(self, basis: str) -> None:
        self._basis = basis or ""
        show_note(
            self.basis,
            {"": "", "manual": "price typed by you", "caption": "price from the Telegram caption"}.get(
                self._basis, f"auto: {self._basis}"
            ),
        )

    def _market_typed(self, *_):
        self._market_extra = {
            "market_source": "typed by you",
            "market_url": "",
            "market_match": "manual",
            "market_isbn": "",
        }
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
        self.message.setText("Saved - checked and ready!" if not left else f"Saved - still {left} thing(s) to fix")
        self.saved.emit(stored)
        return True

    def lookup(self) -> None:
        isbn = self.fields["isbn13"].text()
        self.lookup_btn.setEnabled(False)
        self.lookup_btn.setText("Looking...")
        sku = self.row.get("sku")
        run_in_background(
            editing.lookup_fields,
            self.cfg,
            isbn,
            on_done=lambda res: self._lookup_done(res, sku),
            on_fail=self._lookup_failed,
        )

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
        self.message.setText(
            f"Found via {res.get('source') or '-'}: filled {filled} empty field(s). Check, then Save.{note}"
        )

    def _apply_market(self, fields: dict, overwrite: bool) -> int:
        """Put market price fields (and, if the price is still empty, the suggested price) into the form."""
        n = 0
        if fields.get("market_price") and (overwrite or not self.market_price.text().strip()):
            self.market_price.setText(fields["market_price"])
            self.market_currency.setText(fields.get("market_currency", "TWD"))
            self._market_extra = {
                k: fields.get(k, "") for k in ("market_source", "market_url", "market_match", "market_isbn")
            }
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
        # you asked for it: search again now (a "not found" remembered from processing is not repeated)
        run_in_background(editing.market_for, self.cfg, isbn, title, False, author, on_done=done, on_fail=failed)

    def search_web(self) -> None:
        from ..sources.websearch import browser_search_url

        isbn = self.fields["isbn13"].text().strip()
        if not isbn:
            self.message.setText("Type the ISBN first.")
            return
        try:
            QDesktopServices.openUrl(QUrl(browser_search_url(isbn)))
        except ValueError as exc:
            self.message.setText(str(exc))
            return
        self.message.setText(
            "Search opened in your browser: copy the product link, paste it into 'Shop link', press Use link."
        )

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

    def _show_sell_mode(self, row: dict) -> None:
        i = self.sell_mode.findData("book" if (row.get("sell_as") or "") == "book" else "set")
        self.sell_mode.blockSignals(True)
        self.sell_mode.setCurrentIndex(i if i >= 0 else 0)
        self.sell_mode.blockSignals(False)

    def _sell_mode_chosen(self, *_) -> None:
        """You chose how this set is sold: saved at once (for this set only)."""
        if not self.row.get("set_id"):
            return
        mode = self.sell_mode.currentData()
        try:
            products = editing.set_sell_mode(self.cfg, self.row["batch"], self.row["set_id"], mode)
        except Exception as exc:
            QMessageBox.warning(self, "Not changed", str(exc))
            self._show_sell_mode(self.row)  # back to how it is sold now
            return
        self.row["sell_as"] = "book" if mode == "book" else ""
        self.message.setText(
            f"Set {self.row['set_id']}: "
            + (
                f"each book is its own product now ({len(products)} in Sell products)"
                if mode == "book"
                else "sold as one product again"
            )
        )
        self.set_filled.emit()  # the list shows the new way of selling

    def fill_set(self) -> None:
        if not self.row.get("set_id"):
            return
        if self.dirty and not self.save():
            return
        try:
            n = editing.fill_set(
                self.cfg,
                self.row["batch"],
                self.row["set_id"],
                self.set_condition.currentData() or "",
                self.set_price.text(),
                self.set_currency.text(),
            )
        except Exception as exc:
            QMessageBox.warning(self, "Not saved", str(exc))
            return
        self.message.setText(f"Filled {n} empty field(s) in set {self.row['set_id']}")
        self.set_filled.emit()


# ---- the products you sell (sets of books) -------------------------------------------------------------------------
STATUS_COLOURS = {"draft": RED, "ready": AMBER, "listed": GREEN, "reserved": BLUE, "sold": MUTED, "withdrawn": MUTED}
STATUS_WORDS = {
    "draft": "Not ready",
    "ready": "Ready for Shopee",
    "listed": "Listed",
    "reserved": "Ordered",
    "sold": "Sold",
    "withdrawn": "Withdrawn",
}


def product_name(st: dict) -> str:
    """'Set S01  ·  batch 20261008-153012', or for a book sold on its own '#3 Title  ·  Set S01  ·  batch ...'."""
    code = st.get("product_code", "")
    if st.get("kind") == "book":
        head, _, pos = code.rpartition("-")
        batch, _, set_id = head.rpartition("-")
        books = st.get("books") or [{}]
        title = (books[0].get("title") or "")[:24]
        return f"#{int(pos) if pos.isdigit() else pos} {title}  ·  Set {set_id}  ·  batch {batch}"
    batch, _, set_id = code.rpartition("-")
    return f"Set {set_id}  ·  batch {batch}"


class SellWindow(QDialog):
    """'Sell products': one row per product (a set of books, later also single books) - the thing you actually put on
    Shopee. For the chosen product: its listing title,
    total price and description (all editable; your text is then kept), its Shopee status / link / order, buttons to
    change its status by hand, and - at the top - 'Make Shopee upload file' for every set that is ready."""

    changed = pyqtSignal()  # books may have changed status (listed / sold): the main list reloads

    def __init__(self, cfg: Config, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.products: list = []
        self.current: dict = {}
        self.setWindowTitle("Sell products")
        self.resize(screen_size(self, 0.85))

        # header: what this window is, and its main action
        heading = QLabel("Sell products")
        heading.setObjectName("heading")
        intro = QLabel(
            "Each product - here a set of books - is one Shopee listing. Check its title, price and "
            "description, then make the upload file for every product that is ready. When an order "
            "comes in, read Shopee's order report here too."
        )
        intro.setObjectName("muted")
        intro.setWordWrap(True)
        self.report_btn = QPushButton("Read Shopee report...")
        self.report_btn.setToolTip(
            "Choose the order report (or product list) you exported from Seller Center: "
            "ordered products become reserved, completed orders sold, cancelled ones go back "
            "on sale"
        )
        self.report_btn.clicked.connect(self.read_report)
        self.upload_btn = primary(QPushButton(UPLOAD_TEXT))
        self.upload_btn.setToolTip(
            "Makes Shopee's upload file (from your template) for every ready product, puts "
            "their photos online if photo links are set up, and opens the uploads folder. "
            "Also reads any Shopee report waiting in the import folder."
        )
        self.upload_btn.clicked.connect(self.make_upload)
        head_text = QVBoxLayout()
        head_text.setSpacing(2)
        head_text.addWidget(heading)
        head_text.addWidget(intro)
        header = hbox()
        header.addLayout(head_text, 1)
        header.addWidget(self.report_btn, 0, Qt.AlignmentFlag.AlignTop)
        header.addWidget(self.upload_btn, 0, Qt.AlignmentFlag.AlignTop)

        # left: the products
        self.list = QTreeWidget()
        self.list.setHeaderHidden(True)
        self.list.setRootIsDecorated(False)
        self.list.setMinimumWidth(300)
        self.list.currentItemChanged.connect(self._select)

        # right: the chosen set
        self.title_label = QLabel("Choose a product")
        self.title_label.setObjectName("title")
        self.state = QLabel("")
        self.state.setTextFormat(Qt.TextFormat.RichText)
        self.state.setWordWrap(True)
        self.title = QLineEdit()
        self.title.setMaxLength(60)
        self.title_count = QLabel("")
        self.title_count.setObjectName("caption")
        self.title.textChanged.connect(lambda t: self.title_count.setText(f"{len(t)} / 60"))
        title_row = hbox()
        title_row.addWidget(self.title, 1)
        title_row.addWidget(self.title_count)
        # the price: the books' prices added up, minus a whole-set discount. Type the discount and the set price
        # follows, or type the set price and the discount follows; the one you typed last is what gets saved.
        self.original = QLabel("")
        self.original.setToolTip("The books' own prices added up")
        self.discount = QLineEdit()
        self.discount.setMaximumWidth(80)
        self.discount.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.discount.setToolTip(
            "Percent off the original price, e.g. 20 (= 8 折) - for a set, SET_DISCOUNT unless "
            "you change it; a single book: none. The price follows. Saved with 'Save product'."
        )
        self.discount.textEdited.connect(self._discount_typed)
        self.price = QLineEdit()
        self.price.setObjectName("setPrice")
        self.price.setMaximumWidth(120)
        self.price.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.price.setToolTip("What it sells for, in TWD. The discount follows. Saved with 'Save product'.")
        self.price.textEdited.connect(self._price_typed)
        self._price_by = ""  # "discount" / "price": the one you typed last (the other follows it)
        discount_row = hbox()
        discount_row.addWidget(self.discount)
        discount_row.addWidget(QLabel("% off the whole set"))
        self.zhe = QLabel("")
        self.zhe.setObjectName("muted")
        discount_row.addWidget(self.zhe)
        discount_row.addStretch(1)
        price_row = hbox()
        price_row.addWidget(QLabel("NT$"))
        price_row.addWidget(self.price)
        self.price_note = QLabel("")
        self.price_note.setObjectName("banner")
        price_row.addWidget(self.price_note)
        price_row.addStretch(1)
        # how many you have, and the fewest a buyer may order (Shopee's 庫存 / 最低購買數量) - both 1 for a set
        self.stock = QLineEdit()
        self.min_purchase = QLineEdit()
        for w, tip in (
            (self.stock, "Stock (庫存): how many of this product you have - 1 for a set"),
            (
                self.min_purchase,
                "Minimum purchase quantity (最低購買數量): the fewest a buyer may order "
                "at once. 1 to 999,999, and not more than the stock",
            ),
        ):
            w.setMaximumWidth(90)
            w.setAlignment(Qt.AlignmentFlag.AlignRight)
            w.setToolTip(tip)
        quantity_row = hbox()
        quantity_row.addWidget(self.stock)
        quantity_row.addWidget(QLabel("in stock"))
        quantity_row.addSpacing(16)
        quantity_row.addWidget(QLabel("Minimum purchase"))
        quantity_row.addWidget(self.min_purchase)
        self.quantity_note = QLabel("")
        self.quantity_note.setObjectName("banner")
        quantity_row.addWidget(self.quantity_note)
        quantity_row.addStretch(1)
        self.stock.textEdited.connect(self._check_quantity)
        self.min_purchase.textEdited.connect(self._check_quantity)
        self.description = QPlainTextEdit()
        self.description.setMinimumHeight(220)
        self.count = QLabel("")
        self.count.setObjectName("caption")
        self.description.textChanged.connect(self._count)
        listing_box = Card("Listing")
        form = form_layout()
        listing_box.outer.addLayout(form)
        form.addRow("Title", title_row)
        form.addRow("Original price", self.original)
        form.addRow("Discount", discount_row)
        form.addRow("Price", price_row)
        form.addRow("Quantity", quantity_row)
        form.addRow("Description", with_note(self.description, self.count))
        self.save_btn = primary(QPushButton("Save product"))
        self.save_btn.setToolTip("What you change here is kept; the automatic text no longer replaces it")
        self.save_btn.clicked.connect(self.save)
        self.auto_btn = QPushButton("Back to automatic text")
        self.auto_btn.setToolTip("Forget your changes: title, discount, price and description worked out again")
        self.auto_btn.clicked.connect(self.automatic)
        buttons = hbox()
        buttons.addWidget(self.save_btn)
        buttons.addWidget(self.auto_btn)
        buttons.addStretch(1)
        form.addRow("", buttons)

        self.listing = QLabel("")
        self.listing.setTextFormat(Qt.TextFormat.RichText)
        self.listing.setOpenExternalLinks(True)
        self.listing.setWordWrap(True)
        self.link_edit = QLineEdit()
        self.link_edit.setPlaceholderText("after publishing on Shopee: paste the product's link here")
        self.link_btn = QPushButton("Save link")
        self.link_btn.clicked.connect(self.save_link)
        link_row = hbox()
        link_row.addWidget(self.link_edit, 1)
        link_row.addWidget(self.link_btn)
        self.status_box = flat_combo(QComboBox())
        self.status_box.setMinimumWidth(300)
        for st, words in (
            ("ready", "Ready - put it in the next upload file"),
            ("listed", "Listed (by hand)"),
            ("sold", "Sold (elsewhere / in person)"),
            ("withdrawn", "Withdrawn - not for sale"),
        ):
            self.status_box.addItem(words, st)
        self.status_btn = QPushButton("Change status")
        self.status_btn.clicked.connect(self.change_status)
        status_row = hbox()
        status_row.addWidget(self.status_box)
        status_row.addWidget(self.status_btn)
        status_row.addStretch(1)
        self.photos_btn = QPushButton("Open its photos")
        self.photos_btn.setToolTip(
            "The folder with this product's photos (01.jpg ...) - to drag into Seller Center when they have no links"
        )
        self.photos_btn.clicked.connect(self.open_photos)
        self.again_btn = QPushButton("Make a new upload file for it")
        self.again_btn.setToolTip(
            "Shopee rejected it, or you deleted it there? Puts this product into a new upload "
            "file (with what you changed here)"
        )
        self.again_btn.clicked.connect(self.upload_again)
        more_row = hbox()
        more_row.addWidget(self.photos_btn)
        more_row.addWidget(self.again_btn)
        more_row.addStretch(1)
        shop_box = Card("On Shopee")
        shop = shop_box.outer
        shop.addWidget(self.listing)
        shop.addLayout(link_row)
        shop.addLayout(status_row)
        shop.addLayout(more_row)

        self.message = QLabel("")
        self.message.setObjectName("muted")
        self.message.setWordWrap(True)
        right = QWidget()
        lay = QVBoxLayout(right)
        lay.setContentsMargins(14, 6, 14, 14)
        lay.setSpacing(10)
        lay.addWidget(self.title_label)
        lay.addWidget(self.state)
        lay.addWidget(listing_box)
        lay.addWidget(shop_box)
        lay.addWidget(self.message)
        lay.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidget(right)
        scroll.setWidgetResizable(True)
        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(self.list)
        split.addWidget(scroll)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([340, 900])

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 16)
        outer.setSpacing(12)
        outer.addLayout(header)  # takes only the height it needs ...
        outer.addWidget(split, 1)  # ... the products get all the rest
        no_default_buttons(self)
        self.link_edit.returnPressed.connect(self.save_link)
        self.reload()

    def reload(self, keep: str = None) -> None:
        keep = keep or self.current.get("product_code")
        try:
            self.products = editing.list_products(self.cfg)
        except Exception as exc:
            QMessageBox.warning(self, "Could not read the products", str(exc))
            return
        self.list.blockSignals(True)
        self.list.clear()
        select = None
        for st in self.products:
            count = "one book" if st.get("kind") == "book" else f"{st.get('book_count')} books"
            item = QTreeWidgetItem(
                [
                    f"{product_name(st)}\n"
                    f"{STATUS_WORDS.get(st['status'], st['status'])}  ·  {count}  ·  "
                    f"NT${st.get('price') or '-'}"
                    + (f"  ({st['discount']}% off)" if st.get("discount") not in ("", "0", None) else "")
                ]
            )
            item.setData(0, Qt.ItemDataRole.UserRole, st["product_code"])
            item.setIcon(0, dot_icon(STATUS_COLOURS.get(st["status"], RED)))
            item.setToolTip(0, st["product_code"])
            self.list.addTopLevelItem(item)
            if st["product_code"] == keep:
                select = item
        if select is None and self.list.topLevelItemCount():
            select = self.list.topLevelItem(0)
        if select is not None:
            self.list.setCurrentItem(select)
        self.list.blockSignals(False)
        if select is not None:
            self._select(select, None)
        else:
            self.title_label.setText("No products yet - send photos to the bot first")

    def _select(self, current, previous=None) -> None:
        if current is None:
            return
        code = current.data(0, Qt.ItemDataRole.UserRole)
        if self.current.get("product_code") != code and self.unsaved():
            if not self._ask_save():

                def back(p=previous):  # after Qt has finished handling the click
                    if p is not None:
                        self.list.blockSignals(True)
                        self.list.setCurrentItem(p)
                        self.list.blockSignals(False)

                QTimer.singleShot(0, back)
                return
            self.current = {}
            self.reload(keep=code)  # the saved text is read back
            return
        st = next((x for x in self.products if x["product_code"] == code), None)
        if st:
            self.load(st)

    def unsaved(self) -> bool:
        """Has the chosen set been edited here without 'Save product'?"""
        st = self.current
        if not st:
            return False
        return (
            bool(self._price_by)
            or self.title.text() != (st.get("title") or "")
            or self.description.toPlainText() != (st.get("description") or "")
            or self.stock.text().strip() != (st.get("stock") or "1")
            or self.min_purchase.text().strip() != (st.get("min_purchase") or "1")
        )

    def _ask_save(self) -> bool:
        """The set has unsaved edits: Save / Discard / Cancel. Returns False on Cancel (or if saving failed)."""
        answer = QMessageBox.question(
            self,
            "Unsaved changes",
            "Save your changes to this product first?",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if answer == QMessageBox.StandardButton.Cancel:
            return False
        if answer == QMessageBox.StandardButton.Save:
            try:
                editing.save_product(self.cfg, self.current["product_code"], self._fields())
            except Exception as exc:
                QMessageBox.warning(self, "Not saved", str(exc))
                return False
        self._price_by = ""
        return True

    def _settle(self) -> bool:
        """Before anything that reloads the products: your unsaved edits saved (or discarded). False = stop."""
        if not self.unsaved():
            return True
        if not self._ask_save():
            return False
        self.current = {
            **self.current,
            "title": self.title.text(),
            "description": self.description.toPlainText(),
            "stock": self.stock.text().strip() or "1",
            "min_purchase": self.min_purchase.text().strip() or "1",
        }
        return True

    def reject(self) -> None:
        """Closing the window (x or Esc) with unsaved edits: Save / Discard / Cancel first."""
        if self.unsaved() and not self._ask_save():
            return
        super().reject()

    def load(self, st: dict) -> None:
        self.current = st
        self.title_label.setText(product_name(st))
        colour = STATUS_COLOURS.get(st["status"], RED)
        text = f"<b style='color:{colour}'>● {html.escape(STATUS_WORDS.get(st['status'], st['status']))}</b>"
        if st.get("errors"):
            text += f"<br><span style='color:{RED}'>{html.escape(st['errors'])}</span>"
        books = " · ".join(f"#{b['position']} {b['title'][:14]} NT${b['price'] or '-'}" for b in st.get("books", []))
        text += f"<br><span style='color:{MUTED}'>{html.escape(books)}</span>"
        if st.get("custom"):
            text += f"<br><span style='color:{MUTED}'>Your own text: {html.escape(st['custom'])}</span>"
        self.state.setText(text)
        self.title.setText(st.get("title", ""))
        self.title_count.setText(f"{len(st.get('title', ''))} / 60")
        self._price_by = ""
        self.discount.setText(st.get("discount", ""))
        self.price.setText(st.get("price", ""))
        fixed = st["status"] not in ("draft", "ready")  # listed: the price on Shopee is set
        self.discount.setReadOnly(fixed)
        self.price.setReadOnly(fixed)
        self._recount_price()
        self.stock.setText(st.get("stock") or "1")
        self.min_purchase.setText(st.get("min_purchase") or "1")
        self.quantity_note.setText("")
        self.description.setPlainText(st.get("description", ""))
        li = st.get("shopee") or {}
        if li:
            parts = [f"Status: <b>{html.escape(li.get('status') or '-')}</b>"]
            if li.get("url"):
                parts.append(f"<a href='{html.escape(li['url'], quote=True)}'>Open on Shopee</a>")
            if li.get("export_file"):
                parts.append(f"In upload file: {html.escape(li['export_file'])}")
            if li.get("shopee_order_sn") or li.get("shopee_order_status"):
                parts.append(
                    f"Order {html.escape(li.get('shopee_order_sn', ''))} "
                    f"{html.escape(li.get('shopee_order_status', ''))}"
                )
            if li.get("errors"):
                parts.append(html.escape(li["errors"]))
            self.listing.setText("<br>".join(parts))
        else:
            self.listing.setText("Not on Shopee yet.")
        self.link_edit.setText("")
        i = self.status_box.findData(st["status"])
        self.status_box.setCurrentIndex(i if i >= 0 else 0)
        self.again_btn.setEnabled(st["status"] in ("listed", "withdrawn"))  # ready ones go in the next file anyway
        self.photos_btn.setEnabled((self.cfg.photos_dir / st["product_code"]).is_dir())
        self.message.setText("")

    def _discount_typed(self, *_) -> None:
        self._price_by = "discount"
        self._recount_price()

    def _price_typed(self, *_) -> None:
        self._price_by = "price"
        self._recount_price()

    def _total(self):
        """The books' prices added up (None if a book has no TWD price yet)."""
        try:
            total = float(self.current.get("original_price") or "x")
        except ValueError:
            return None
        return total if total > 0 else None

    def _recount_price(self, *_) -> None:
        """Original price - discount = set price. Shown as you type: the discount changes the price, the price
        changes the discount."""
        from .products import discount_for, pct, zhe

        st = self.current
        total = self._total()
        self.price_note.setText("")
        if total is None:
            self.original.setText("-  (a book has no TWD price yet)")
            self.zhe.setText("")
            return
        self.original.setText(
            f"NT${total:,.0f}  (the book's own price)"
            if st.get("kind") == "book"
            else f"NT${total:,.0f}  (the {st.get('book_count')} books' prices added up)"
        )
        if self._price_by == "price":
            try:
                price = float(self.price.text().replace(",", "").replace("NT$", "").replace("$", "").strip())
                if price <= 0:
                    raise ValueError
            except ValueError:
                self.price_note.setText("type a price above 0")
                self.zhe.setText("")
                return
            d = discount_for(price, total)
            if d < 0:
                self.price_note.setText("more than the original price")
                d = 0.0
            self.discount.setText(f"{d:g}")
        else:
            try:
                txt = self.discount.text().strip()
                d = pct(txt) if txt else self.cfg.set_discount * 100  # empty = the usual discount (SET_DISCOUNT)
                if not 0 <= d < 100:
                    raise ValueError
            except ValueError:
                self.price_note.setText("type a discount from 0 to 99")
                self.zhe.setText("")
                return
            if self._price_by == "discount":
                self.price.setText(f"{max(1, round(total * (1 - d / 100)))}")
        self.zhe.setText(f"= {zhe(d)}" if zhe(d) else "")

    def _count(self) -> None:
        n = len(self.description.toPlainText())
        self.count.setText(f"{n} / 3000 characters" + ("  - TOO LONG for Shopee" if n > 3000 else ""))

    def _check_quantity(self, *_) -> None:
        """Stock and minimum purchase: whole numbers, and a buyer must be able to order (minimum <= stock)."""
        try:
            stock, least = int(self.stock.text().strip() or "1"), int(self.min_purchase.text().strip() or "1")
        except ValueError:
            self.quantity_note.setText("whole numbers only")
            return
        if not (1 <= stock <= 999_999 and 1 <= least <= 999_999):
            self.quantity_note.setText("from 1 to 999,999")
        elif least > stock:
            self.quantity_note.setText("minimum is more than the stock - nobody could order it")
        else:
            self.quantity_note.setText("")

    def _fields(self) -> dict:
        fields = {
            "title": self.title.text(),
            "description": self.description.toPlainText(),
            "stock": self.stock.text(),
            "min_purchase": self.min_purchase.text(),
        }
        if self._price_by == "discount":  # only the one you typed: the other follows it
            fields["discount"] = self.discount.text()
        elif self._price_by == "price":
            fields["price"] = self.price.text()
        return fields

    def save(self) -> bool:
        if not self.current:
            return False
        try:
            stored = editing.save_product(self.cfg, self.current["product_code"], self._fields())
        except Exception as exc:
            QMessageBox.warning(self, "Not saved", str(exc))
            return False
        self.reload(keep=stored["product_code"])
        note = " - change it on Shopee too" if stored["status"] in ("listed", "reserved") else ""
        self.message.setText(f"Saved{note}")
        return True

    def automatic(self) -> None:
        if not self.current:
            return
        if self.current["status"] not in ("draft", "ready"):
            self.message.setText("Only a product not yet listed gets automatic text")
            return
        try:
            editing.save_product(
                self.cfg, self.current["product_code"], {"title": "", "discount": "", "price": "", "description": ""}
            )
        except Exception as exc:
            QMessageBox.warning(self, "Not saved", str(exc))
            return
        self.reload()
        self.message.setText("Back to the automatic title, price and description")

    def save_link(self) -> None:
        if not self.current or not self._settle():
            return
        try:
            li = editing.shopee_link(self.cfg, self.current["product_code"], self.link_edit.text())
        except Exception as exc:
            self.message.setText(str(exc))
            return
        self.reload()
        self.changed.emit()
        self.message.setText(f"Saved: on Shopee as item {li['shopee_item_id']}")

    def change_status(self) -> None:
        if not self.current or not self._settle():
            return
        status = self.status_box.currentData()
        try:
            msgs = editing.product_status(self.cfg, self.current["product_code"], status)
        except Exception as exc:
            QMessageBox.warning(self, "Not changed", str(exc))
            return
        self.reload()
        self.changed.emit()
        self.message.setText(f"Status now: {STATUS_WORDS.get(self.current['status'], self.current['status'])}")
        if msgs:
            QMessageBox.information(self, "Take it down", "\n".join(msgs))

    def _shopee(self, button: QPushButton, fn, *args) -> None:
        """Run a Shopee step in the background (button shows 'Working...'), then show what happened; an upload file
        made -> its folder opens."""
        if not self._settle():  # what you typed goes into the file, not the older text
            return
        words = button.text()
        for b in (self.upload_btn, self.report_btn, self.again_btn):
            b.setEnabled(False)
        button.setText("Working...")

        def finish():
            button.setText(words)
            self.upload_btn.setEnabled(True)
            self.report_btn.setEnabled(True)
            self.reload()
            self.changed.emit()

        def done(res: dict):
            finish()
            if res.get("file"):
                open_folder(res["file"].parent)
            QMessageBox.information(self, "Shopee", res["text"])

        def failed(msg: str):
            finish()
            QMessageBox.warning(self, "Shopee", msg)

        run_in_background(fn, self.cfg, *args, on_done=done, on_fail=failed)

    def make_upload(self) -> None:
        self._shopee(self.upload_btn, editing.shopee_upload)

    def upload_again(self) -> None:
        if self.current:
            self._shopee(self.again_btn, editing.shopee_upload, [self.current["product_code"]])

    def read_report(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Shopee's order report or product list",
            str(Path.home() / "Downloads"),
            "Shopee files (*.xlsx *.xls *.csv);;All files (*)",
        )
        if files:
            self._shopee(self.report_btn, editing.shopee_read, files)

    def open_photos(self) -> None:
        if self.current:
            open_folder(self.cfg.photos_dir / self.current["product_code"])


# ---- the main window ---------------------------------------------------------------------------
class ReviewWindow(QMainWindow):
    """Books grouped by set (each set folds open / closed), the editor for the chosen book on the right."""

    def __init__(self, cfg: Config):
        super().__init__()
        self.cfg = cfg
        self.rows: list = []
        self.folded: set = set()  # sets you folded closed (kept across refreshes)
        self._csv_mtime = self._mtime()
        self.setWindowTitle(f"Book review - {cfg.csv_path.resolve()}")
        self.resize(screen_size(self, 0.9))

        bar = QToolBar()
        bar.setMovable(False)
        self.addToolBar(bar)
        self.count = QLabel("")
        self.count.setObjectName("title")
        self.show_all = QCheckBox("Show checked books too")
        self.show_all.toggled.connect(lambda _: self.reload())
        self.refresh_btn = QPushButton("Refresh  (Ctrl+R)")
        self.refresh_btn.setToolTip("Read books.csv again (new books from the bot appear by themselves too)")
        self.refresh_btn.clicked.connect(lambda: self.reload())
        self.sell_btn = primary(QPushButton("Sell products  (Ctrl+L)"))
        self.sell_btn.setToolTip(
            "Every product (set of books) for Shopee: title, total price, description, status; "
            "make the Shopee upload file"
        )
        self.sell_btn.clicked.connect(self.open_sell)
        self.sell_window = None
        self.banner = QLabel("")
        self.banner.setObjectName("banner")
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        for w in (self.count, self.show_all, self.banner, spacer, self.refresh_btn, self.sell_btn):
            bar.addWidget(w)
        QShortcut(QKeySequence("Ctrl+R"), self).activated.connect(lambda: self.reload())
        QShortcut(QKeySequence("Ctrl+L"), self).activated.connect(self.open_sell)

        self.list = QTreeWidget()
        self.list.setHeaderHidden(True)
        self.list.setMinimumWidth(340)
        self.list.setUniformRowHeights(False)
        self.list.setExpandsOnDoubleClick(False)  # one click on a set folds / unfolds it
        self.list.setRootIsDecorated(False)  # the set's own ▾ / ▸ shows whether it is open
        self.list.setIndentation(16)
        self.list.currentItemChanged.connect(self._select)
        self.list.itemClicked.connect(self._clicked)
        self.list.itemCollapsed.connect(self._folded)
        self.list.itemExpanded.connect(self._unfolded)
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
        split.setSizes([380, 1000])
        holder = QWidget()
        hl = QVBoxLayout(holder)
        hl.setContentsMargins(10, 10, 10, 10)
        hl.addWidget(split)
        self.setCentralWidget(holder)

        # the bot may add books while this window is open: check the file every few seconds
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._check_file)
        self.timer.start(3000)
        self.reload()

    # -- the book tree --
    def _folded(self, item) -> None:
        self.folded.add(item.data(0, SET_ROLE))
        self._update_set_item(item)

    def _unfolded(self, item) -> None:
        self.folded.discard(item.data(0, SET_ROLE))
        self._update_set_item(item)

    def book_items(self) -> list:
        """Every book row of the tree, top to bottom."""
        out = []
        for i in range(self.list.topLevelItemCount()):
            top = self.list.topLevelItem(i)
            out += [top.child(j) for j in range(top.childCount())]
        return out

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
        groups: dict = {}
        for r in self.rows:
            groups.setdefault((r.get("batch") or "", r.get("set_id") or ""), []).append(r)
        for (batch, set_id), books in groups.items():
            key = f"{batch}-{set_id}" if set_id else "(no set)"
            top = QTreeWidgetItem([""])
            top.setData(0, SET_ROLE, key)
            font = QFont()
            font.setBold(True)
            top.setFont(0, font)
            top.setFlags(Qt.ItemFlag.ItemIsEnabled)  # a set is a heading: click it to fold / unfold
            self.list.addTopLevelItem(top)
            for r in sorted(books, key=lambda b: int(b.get("position") or 0)):
                item = QTreeWidgetItem([self._item_text(r)])
                item.setData(0, Qt.ItemDataRole.UserRole, r["sku"])
                item.setIcon(0, dot_icon(self._colour(r)))
                top.addChild(item)
                if r["sku"] == keep:
                    select = item
            top.setExpanded(key not in self.folded)
            self._update_set_item(top)
        self._update_count()
        items = self.book_items()
        if select is None and items:
            select = items[0]
        if select is not None:
            self.list.setCurrentItem(select)
        self.list.blockSignals(False)
        if select is not None:
            self._select(select, None)
        else:
            self.editor.row = {}
            self.editor.setEnabled(False)
            self.editor.heading.setText(
                "Nothing left to check - press 'Sell products'" if not self.show_all.isChecked() else "No books yet"
            )

    def _update_set_item(self, top) -> None:
        key = top.data(0, SET_ROLE)
        skus = {top.child(j).data(0, Qt.ItemDataRole.UserRole) for j in range(top.childCount())}
        books = [r for r in self.rows if r["sku"] in skus]
        red = sum(1 for r in books if r["issues"])
        amber = sum(1 for r in books if not r["issues"] and r.get("to_check"))
        batch, _, set_id = key.rpartition("-")
        parts = [f"{red} to fix"] if red else []
        parts += [f"{amber} to check"] if amber else []
        arrow = "▾" if top.isExpanded() else "▸"
        per_book = any((r.get("sell_as") or "") == "book" for r in books)
        top.setText(
            0,
            f"{arrow}  "
            + (f"Set {set_id}  ·  batch {batch}" if batch else key)
            + "\n"
            + f"     {len(books)} book(s)"
            + (f"  ·  {', '.join(parts)}" if parts else "  ·  all checked")
            + ("  ·  sold per book" if per_book else ""),
        )
        top.setToolTip(0, f"Set code {key} - click to fold / unfold")
        top.setIcon(0, dot_icon(RED if red else AMBER if amber else GREEN))

    @staticmethod
    def _colour(r: dict) -> str:
        return RED if r["issues"] else AMBER if r.get("to_check") else GREEN

    @staticmethod
    def _item_text(r: dict) -> str:
        """'#3  Title' and what it needs - the set is the heading above, so it is not repeated."""
        name = r.get("title") or r.get("isbn13") or "(unknown book)"
        state = f"{len(r['issues'])} to fix" if r["issues"] else "quick check" if r.get("to_check") else "checked"
        return f"#{r.get('position') or '?'}  {name[:38]}\n      {state}"

    def _update_count(self) -> None:
        red = sum(1 for r in self.rows if r["issues"])
        amber = sum(1 for r in self.rows if not r["issues"] and r.get("to_check"))
        parts = [f"{red} to fix"] if red else []
        parts += [f"{amber} to check"] if amber else []
        self.count.setText(", ".join(parts) if parts else "Nothing left to check")

    def _ask_save(self) -> bool:
        """The current book has unsaved edits: Save / Discard / Cancel. Returns False on Cancel."""
        answer = QMessageBox.question(
            self,
            "Unsaved changes",
            "Save your changes to this book first?",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if answer == QMessageBox.StandardButton.Cancel:
            return False
        if answer == QMessageBox.StandardButton.Save:
            return self.editor.save()
        self.editor.dirty = False
        return True

    @staticmethod
    def _clicked(item, column=0) -> None:
        if item.data(0, Qt.ItemDataRole.UserRole) is None:  # a set heading: fold / unfold it
            item.setExpanded(not item.isExpanded())

    def _select(self, current, previous) -> None:
        if current is None:
            return
        sku = current.data(0, Qt.ItemDataRole.UserRole)
        if sku is None:  # a set heading: the editor keeps its book
            return
        if self.editor.row.get("sku") == sku and self.editor.dirty:
            return
        if self.editor.dirty and previous is not None and not self._ask_save():

            def back(p=previous):  # after Qt has finished handling the click
                self.list.blockSignals(True)
                self.list.setCurrentItem(p)
                self.list.blockSignals(False)

            QTimer.singleShot(0, back)
            return
        row = next((r for r in self.rows if r["sku"] == sku), None)
        if row:
            self.editor.load(row)

    def _saved(self, stored: dict) -> None:
        for i, r in enumerate(self.rows):
            if r["sku"] == stored["sku"]:
                self.rows[i] = stored
        for item in self.book_items():  # not necessarily the current item: 'Save' may come from switching books
            if item.data(0, Qt.ItemDataRole.UserRole) == stored["sku"]:
                item.setText(0, self._item_text(stored))
                item.setIcon(0, dot_icon(self._colour(stored)))
                self._update_set_item(item.parent())
        self._csv_mtime = self._mtime()  # our own save is not 'new data'
        self._update_count()

    def next_book(self) -> None:
        """Go to the next book that still needs something (wrapping around, into the next set)."""
        items = self.book_items()
        n = len(items)
        cur = self.list.currentItem()
        start = items.index(cur) if cur in items else -1
        for step in range(1, n + 1):
            item = items[(start + step) % n]
            sku = item.data(0, Qt.ItemDataRole.UserRole)
            if any(r["sku"] == sku and (r["issues"] or r.get("to_check")) for r in self.rows):
                item.parent().setExpanded(True)
                self.list.setCurrentItem(item)
                return

    def open_sell(self) -> None:
        if self.editor.dirty and not self._ask_save():
            return
        if self.sell_window is not None and alive(self.sell_window) and self.sell_window.isVisible():
            self.sell_window.reload()
            self.sell_window.raise_()
            self.sell_window.activateWindow()
            return
        self.sell_window = SellWindow(self.cfg, self)
        self.sell_window.changed.connect(lambda: self.reload())
        self.sell_window.show()

    def closeEvent(self, event):
        if self.editor.dirty and not self._ask_save():
            event.ignore()
            return
        event.accept()


def _chevron(path, down: bool = True) -> str:
    """A small arrow picture for the dropdowns (Qt style sheets need an image file for it)."""
    path = path.resolve()  # an absolute path: the style sheet must find it from anywhere
    pm = QPixmap(24, 24)
    pm.fill(QColor(0, 0, 0, 0))
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(ACCENT_DARK))
    pen.setWidthF(2.4)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.drawPolyline(
        *([QPoint(6, 9), QPoint(12, 15), QPoint(18, 9)] if down else [QPoint(9, 6), QPoint(15, 12), QPoint(9, 18)])
    )
    p.end()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not pm.save(str(path), "PNG"):
        raise OSError(f"could not save {path}")
    return path.as_posix()


def full_style(cfg: Config) -> str:
    """STYLE plus the dropdown arrow (its picture is made once, in data/cache/ui)."""
    try:
        arrow = _chevron(cfg.cache_dir / "ui" / "chevron_down.png")
    except Exception:
        return STYLE
    return (
        STYLE
        + f"""
QComboBox::down-arrow {{ image: url("{arrow}"); width: 12px; height: 12px; }}
QComboBox::down-arrow:on {{ top: 1px; }}
"""
    )


def run(cfg: Config) -> None:
    from PyQt6.QtWidgets import QAbstractItemView, QProxyStyle, QStyle

    class CleanStyle(QProxyStyle):
        """Fusion without the dotted focus box in the book list (the selected row shows where you are)."""

        def drawPrimitive(self, element, option, painter, widget=None):
            if element == QStyle.PrimitiveElement.PE_FrameFocusRect and isinstance(widget, QAbstractItemView):
                return
            super().drawPrimitive(element, option, painter, widget)

    cfg.ensure_dirs()
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("Book Selling Pipeline - review")
    app._clean_style = CleanStyle("Fusion")  # kept referenced; the same clean look on every Windows version
    app.setStyle(app._clean_style)
    app.setStyleSheet(full_style(cfg))

    def unexpected(kind, value, tb):  # PyQt would otherwise close the whole window on any error
        traceback.print_exception(kind, value, tb)
        QMessageBox.critical(None, "Something went wrong", f"{value}\n\nNothing was lost; try again.")

    sys.excepthook = unexpected
    window = ReviewWindow(cfg)
    window.show()
    sys.exit(app.exec())
