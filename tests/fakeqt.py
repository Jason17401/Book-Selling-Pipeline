"""A tiny stand-in for PyQt6, used ONLY when PyQt6 is not installed (e.g. on a build server), so the review window's own
logic still gets exercised. Widgets keep their text/values and signals call their slots straight away; drawing does
nothing. With real PyQt6 installed, tests/test_gui.py uses the real thing instead."""

from __future__ import annotations

import sys
import types


class _Any:
    """Accepts any call/attribute: stands in for layout/styling calls the tests don't care about."""

    def __init__(self, *a, **k):
        pass

    def __getattr__(self, name):
        return _Any()

    def __call__(self, *a, **k):
        return _Any()

    def __or__(self, other):
        return self

    def __iter__(self):
        return iter(())


class _Stable:
    """Qt.ItemDataRole.UserRole etc.: the same object every time it is asked for."""

    def __init__(self):
        self._cache = {}

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return self._cache.setdefault(name, _Stable())

    def __or__(self, other):
        return self

    def __add__(self, n):
        return self._cache.setdefault(f"+{n}", _Stable())


class _Bound:
    def __init__(self, owner):
        self.owner, self.slots = owner, []

    def connect(self, fn):
        self.slots.append(fn)

    def emit(self, *args):
        if getattr(self.owner, "_blocked", False):
            return
        for fn in list(self.slots):
            try:
                fn(*args)
            except TypeError:
                fn()  # like PyQt: a slot may take fewer arguments than the signal sends


class pyqtSignal:
    def __init__(self, *types_):
        pass

    def __set_name__(self, owner, name):
        self.name = name

    def __get__(self, obj, objtype=None):
        if obj is None:
            return self
        key = "_sig_" + self.name
        if key not in obj.__dict__:
            obj.__dict__[key] = _Bound(obj)
        return obj.__dict__[key]


def pyqtSlot(*a, **k):
    return lambda fn: fn


class QObject:
    def __init__(self, *a, **k):
        self._blocked = False
        self._props = {}

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return _Any()

    def blockSignals(self, b):
        self._blocked = b

    def setProperty(self, k, v):
        self._props[k] = v

    def property(self, k):
        return self._props.get(k)

    def objectName(self):
        return ""


class QWidget(QObject):
    def __init__(self, *a, **k):
        super().__init__()
        self._enabled, self._visible = True, True

    def setEnabled(self, b):
        self._enabled = b

    def isEnabled(self):
        return self._enabled

    def setVisible(self, b):
        self._visible = b

    def isHidden(self):
        return not self._visible

    def reject(self):  # QDialog: closing it
        self._visible = False

    def findChildren(self, *a):
        return []


class QLabel(QWidget):
    clicked = pyqtSignal()

    def __init__(self, text="", *a):
        super().__init__()
        self._text = text

    def setText(self, t):
        self._text = t

    def text(self):
        return self._text


class QLineEdit(QWidget):
    textEdited = pyqtSignal(str)
    textChanged = pyqtSignal(str)
    returnPressed = pyqtSignal()

    def __init__(self, *a):
        super().__init__()
        self._text = ""

    def setText(self, t):
        self._text = t
        self.textChanged.emit(t)

    def text(self):
        return self._text

    def clear(self):
        self.setText("")


class QPlainTextEdit(QWidget):
    textChanged = pyqtSignal()

    def __init__(self, *a):
        super().__init__()
        self._text = ""

    def setPlainText(self, t):
        self._text = t
        self.textChanged.emit()

    def toPlainText(self):
        return self._text


class QComboBox(QWidget):
    activated = pyqtSignal(int)

    def __init__(self, *a):
        super().__init__()
        self._items, self._i = [], 0

    def addItem(self, text, data=None):
        self._items.append((text, data))

    def findData(self, d):
        return next((i for i, (_, x) in enumerate(self._items) if x == d), -1)

    def count(self):
        return len(self._items)

    def setItemData(self, i, value, role=None):
        pass

    def itemText(self, i):
        return self._items[i][0]

    def setCurrentIndex(self, i):
        self._i = i

    def currentData(self):
        return self._items[self._i][1] if self._items else None


class QPushButton(QWidget):
    clicked = pyqtSignal(bool)

    def __init__(self, text="", *a):
        super().__init__()
        self._text = text

    def setText(self, t):
        self._text = t

    def text(self):
        return self._text

    def click(self):
        self.clicked.emit(False)


class QCheckBox(QPushButton):
    toggled = pyqtSignal(bool)

    def __init__(self, text="", *a):
        super().__init__(text)
        self._checked = False

    def isChecked(self):
        return self._checked

    def setChecked(self, b):
        self._checked = b
        self.toggled.emit(b)


class QListWidgetItem:
    def __init__(self, text=""):
        self._text, self._data = text, {}

    def text(self):
        return self._text

    def setText(self, t):
        self._text = t

    def setData(self, role, v):
        self._data[role] = v

    def data(self, role):
        return self._data.get(role)

    def setIcon(self, icon):
        pass


class QListWidget(QWidget):
    currentItemChanged = pyqtSignal(object, object)

    def __init__(self, *a):
        super().__init__()
        self._items, self._cur = [], None

    def addItem(self, item):
        self._items.append(item)

    def clear(self):
        self._items, self._cur = [], None

    def count(self):
        return len(self._items)

    def item(self, i):
        return self._items[i]

    def currentItem(self):
        return self._cur

    def currentRow(self):
        return self._items.index(self._cur) if self._cur in self._items else -1

    def setCurrentItem(self, item):
        prev, self._cur = self._cur, item
        if item is not prev:
            self.currentItemChanged.emit(item, prev)

    def setCurrentRow(self, i):
        self.setCurrentItem(self._items[i])


class _LayoutItem:
    def __init__(self, w):
        self._w = w

    def widget(self):
        return self._w


class _Layout(QObject):
    def __init__(self, *a):
        super().__init__()
        self._items = []

    def addWidget(self, w, *a):
        self._items.append(w)

    def count(self):
        return len(self._items)

    def takeAt(self, i):
        return _LayoutItem(self._items.pop(i))


class QFileDialog:
    chosen: list = []  # what the next "open file" dialog returns

    @staticmethod
    def getOpenFileNames(*a, **k):
        return list(QFileDialog.chosen), ""


class QMessageBox:
    class StandardButton:
        Save, Discard, Cancel = 1, 2, 4

    shown: list = []

    @staticmethod
    def question(*a, **k):
        return QMessageBox.StandardButton.Save

    @staticmethod
    def warning(parent, title, text):
        QMessageBox.shown.append((title, text))

    @staticmethod
    def information(parent, title, text):
        QMessageBox.shown.append((title, text))


class QThreadPool:
    @staticmethod
    def globalInstance():
        return QThreadPool()

    def start(self, task):
        task.run()  # synchronous: results are delivered before start() returns

    def waitForDone(self, *a):
        return True


class QRunnable:
    def __init__(self, *a):
        pass


class QTimer(QObject):
    timeout = pyqtSignal()

    @staticmethod
    def singleShot(ms, fn):
        fn()

    def start(self, *a):
        pass


class QShortcut(QObject):
    activated = pyqtSignal()


class QPixmap(_Any):
    pass


class QPainter(_Any):
    RenderHint = _Stable()


class QSize:
    def __init__(self, w=0, h=0):
        self._w, self._h = w, h

    def width(self):
        return self._w

    def height(self):
        return self._h

    def expandedTo(self, other):
        return QSize(max(self._w, other.width()), max(self._h, other.height()))


class QSizePolicy(_Any):
    Policy = _Stable()


class QTreeWidgetItem:
    def __init__(self, texts=None):
        self._text = {0: (texts or [""])[0]}
        self._data, self._children, self._parent, self._expanded = {}, [], None, False

    def text(self, col=0):
        return self._text.get(col, "")

    def setText(self, col, t):
        self._text[col] = t

    def setData(self, col, role, v):
        self._data[(col, role)] = v

    def data(self, col, role):
        return self._data.get((col, role))

    def addChild(self, c):
        c._parent = self
        self._children.append(c)

    def child(self, i):
        return self._children[i]

    def childCount(self):
        return len(self._children)

    def parent(self):
        return self._parent

    def setExpanded(self, b):
        self._expanded = b

    def isExpanded(self):
        return self._expanded

    def __getattr__(self, name):  # setIcon, setFont, setFlags, setToolTip ...
        if name.startswith("__"):
            raise AttributeError(name)
        return _Any()


class QTreeWidget(QWidget):
    currentItemChanged = pyqtSignal(object, object)
    itemClicked = pyqtSignal(object, int)
    itemCollapsed = pyqtSignal(object)
    itemExpanded = pyqtSignal(object)

    def __init__(self, *a):
        super().__init__()
        self._top, self._cur = [], None

    def addTopLevelItem(self, item):
        self._top.append(item)

    def topLevelItem(self, i):
        return self._top[i]

    def topLevelItemCount(self):
        return len(self._top)

    def clear(self):
        self._top, self._cur = [], None

    def currentItem(self):
        return self._cur

    def setCurrentItem(self, item):
        prev, self._cur = self._cur, item
        if item is not prev:
            self.currentItemChanged.emit(item, prev)


def install(monkeypatch) -> None:
    """Put fake PyQt6 modules in sys.modules (undone automatically after the test)."""
    pkg = types.ModuleType("PyQt6")
    core, gui, widgets = (types.ModuleType(f"PyQt6.{n}") for n in ("QtCore", "QtGui", "QtWidgets"))
    core.QEvent = _Stable()
    for name in ("QObject", "QRunnable", "QThreadPool", "QTimer", "pyqtSignal", "pyqtSlot"):
        setattr(core, name, globals()[name])
    core.Qt = _Stable()
    core.QUrl = core.QRect = core.QPoint = _Any
    core.QSize = QSize
    for name in ("QPixmap", "QShortcut", "QPainter"):
        setattr(gui, name, globals()[name])
    gui.QColor = gui.QIcon = gui.QKeySequence = gui.QFont = gui.QPen = _Any
    gui.QDesktopServices = _Any()
    for name in (
        "QWidget",
        "QLabel",
        "QLineEdit",
        "QPlainTextEdit",
        "QComboBox",
        "QPushButton",
        "QCheckBox",
        "QListWidget",
        "QListWidgetItem",
        "QMessageBox",
        "QTreeWidget",
        "QTreeWidgetItem",
        "QSizePolicy",
        "QFileDialog",
    ):
        setattr(widgets, name, globals()[name])
    widgets.QToolButton = type("QToolButton", (QPushButton,), {})
    widgets.QStyledItemDelegate = _Any
    widgets.QFrame = type("QFrame", (QWidget,), {})
    for name in ("QHBoxLayout", "QVBoxLayout", "QFormLayout", "QLayout"):
        setattr(widgets, name, _Layout)
    for name in ("QApplication", "QDialog", "QGroupBox", "QMainWindow", "QScrollArea", "QSplitter", "QToolBar"):
        setattr(widgets, name, type(name, (QWidget,), {}))
    pkg.QtCore, pkg.QtGui, pkg.QtWidgets = core, gui, widgets
    for mod in (pkg, core, gui, widgets):
        monkeypatch.setitem(sys.modules, mod.__name__, mod)
    monkeypatch.delitem(sys.modules, "pipeline.apps.gui", raising=False)
