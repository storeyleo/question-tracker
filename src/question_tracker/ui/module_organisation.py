"""Home-only module drag/drop; payloads identify modules, never file paths."""
import json

from PySide6.QtCore import QMimeData, Qt, Signal
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import QApplication, QLabel, QWidget

from question_tracker.domain.library import LibraryModule

MODULE_MIME = "application/x-question-tracker-module"


def module_mime(module: LibraryModule) -> QMimeData:
    mime = QMimeData()
    mime.setData(MODULE_MIME, json.dumps([module.year_name, module.module_name]).encode("utf-8"))
    return mime


def read_module(mime: QMimeData) -> LibraryModule | None:
    try:
        data = json.loads(bytes(mime.data(MODULE_MIME)))
        if isinstance(data, list) and len(data) == 2 and all(isinstance(part, str) for part in data):
            return LibraryModule(*data)
    except (ValueError, UnicodeError):
        pass
    return None


class ModuleDropArea(QWidget):
    module_dropped = Signal(object, object)

    def __init__(self, year: str, group_id: str | None, parent=None):
        super().__init__(parent)
        self.year = year
        self.group_id = group_id
        self.setAcceptDrops(True)
        self.setProperty("moduleGroupId", group_id or "")
        self.setProperty("yearName", year)

    def accepts(self, mime):
        module = read_module(mime)
        return module is not None and module.year_name.casefold() == self.year.casefold()

    def dragEnterEvent(self, event):
        if self.accepts(event.mimeData()):
            event.setDropAction(Qt.DropAction.MoveAction)
            event.accept()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        self.dragEnterEvent(event)

    def dropEvent(self, event):
        if not self.accepts(event.mimeData()):
            event.ignore()
            return
        event.setDropAction(Qt.DropAction.MoveAction)
        event.accept()
        self.module_dropped.emit(read_module(event.mimeData()), self.group_id)


class ModuleDragHandle(QLabel):
    """A drag surface also works for disabled, not-yet-ready module cards."""
    def __init__(self, module: LibraryModule, parent=None):
        super().__init__(parent)
        self.module = module
        self._press = None
        self.setText("⋮⋮ Drag to group")
        self.setToolTip("Drag this module to a group or Ungrouped in the same year")
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._press = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._press is not None and event.buttons() & Qt.MouseButton.LeftButton:
            if (event.position().toPoint() - self._press).manhattanLength() >= QApplication.startDragDistance():
                self._press = None
                drag = QDrag(self)
                drag.setMimeData(module_mime(self.module))
                drag.exec(Qt.DropAction.MoveAction)
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._press = None
        super().mouseReleaseEvent(event)
