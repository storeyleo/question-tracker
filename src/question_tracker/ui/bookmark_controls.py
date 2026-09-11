"""Small shared bookmark menu; no tracker layout changes."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QHBoxLayout, QInputDialog, QListWidget, QMessageBox, QPushButton, QVBoxLayout, QMenu, QToolButton

from question_tracker.application.tracking_service import TrackingError
from question_tracker.ui.tracker_commands import SetBookmarkCommand


def toggle_default_bookmark(service, stack, question_id, parent):
    types = service.list_bookmark_types()
    if not types:
        BookmarkTypesDialog(service, stack, parent).exec()
        return
    type_id = types[0].id
    before = type_id in {item.id for item in service.get_question_bookmarks(question_id)}
    stack.push(SetBookmarkCommand(service, question_id, before, not before, bookmark_type_id=type_id))


def build_bookmark_menu(button, service, stack, question_id, menu=None, after_manage=None):
    """Build the real actions independently of native popup presentation."""
    menu = menu if menu is not None else QMenu(button)
    menu.clear()
    assigned = {item.id for item in service.get_question_bookmarks(question_id)}
    for item in service.list_bookmark_types():
        action = menu.addAction(item.name)
        action.setCheckable(True)
        action.setChecked(item.id in assigned)
        action.triggered.connect(lambda checked, item=item: stack.push(SetBookmarkCommand(
            service, question_id, item.id in {t.id for t in service.get_question_bookmarks(question_id)}, checked, bookmark_type_id=item.id,
        )))
    menu.addSeparator()
    manage = menu.addAction("Manage bookmark types…")
    def manage_types():
        BookmarkTypesDialog(service, stack, button).exec()
        if after_manage is not None:
            after_manage()
    manage.triggered.connect(manage_types)
    return menu


class BookmarkButton(QToolButton):
    def __init__(self):
        super().__init__()
        self.setText("☆ Bookmark")
        self.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self._state = None

    def refresh_state(self):
        if self._state is None:
            return
        service, selected_question = self._state
        question_id = selected_question()
        active = service.get_question_bookmarks(question_id) if question_id else ()
        types = service.list_bookmark_types()
        default = types[0] if types else None
        default_on = default is not None and default.id in {t.id for t in active}
        self.setText("★ Bookmarked" if default_on else "★ Other bookmarks" if active else "☆ Bookmark")
        self.setToolTip(
            "Active: " + (", ".join(t.name for t in active) or "None") + "\n" +
            (f"Click to {'remove' if default_on else 'add'} {default.name}. " if default else "Click to create a bookmark type. ") +
            "Use the arrow to choose types."
        )


def install_bookmark_menu(button, service, stack, selected_question, refresh):
    button._state = service, selected_question
    menu = QMenu(button)
    button.setMenu(menu)

    def prepare():
        question_id = selected_question()
        if question_id is not None:
            build_bookmark_menu(button, service, stack, question_id, menu, refresh)

    menu.aboutToShow.connect(prepare)
    menu.aboutToHide.connect(button.refresh_state)
    button.refresh_state()


class BookmarkTypesDialog(QDialog):
    def __init__(self, service, stack, parent=None):
        super().__init__(parent)
        self.service, self.stack = service, stack
        self.setWindowTitle("Bookmark types")
        layout = QVBoxLayout(self)
        self.list = QListWidget()
        layout.addWidget(self.list)
        row = QHBoxLayout()
        for text, handler in (("Create…", self.create), ("Rename…", self.rename), ("Delete…", self.delete)):
            button = QPushButton(text)
            button.clicked.connect(handler)
            row.addWidget(button)
        layout.addLayout(row)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)
        layout.addWidget(close)
        self.reload()

    def reload(self):
        self.types = self.service.list_bookmark_types()
        self.list.clear()
        self.list.addItems([item.name for item in self.types])
        if self.types:
            self.list.setCurrentRow(0)

    def selected(self):
        index = self.list.currentRow()
        return self.types[index] if index >= 0 else None

    def run(self, operation, *, clears_history=False):
        try:
            operation()
        except TrackingError as error:
            QMessageBox.warning(self, "Bookmark types", str(error))
            return
        # Commands referencing a deleted type must not recreate it on redo.
        if clears_history:
            self.stack.clear()
        self.reload()

    def create(self):
        name, accepted = QInputDialog.getText(self, "Create bookmark type", "Name:")
        if accepted:
            self.run(lambda: self.service.create_bookmark_type(name))

    def rename(self):
        item = self.selected()
        if item is None:
            return
        name, accepted = QInputDialog.getText(self, "Rename bookmark type", "Name:", text=item.name)
        if accepted:
            self.run(lambda: self.service.rename_bookmark_type(item.id, name))

    def delete(self):
        item = self.selected()
        if item is None:
            return
        if QMessageBox.question(
            self, "Delete bookmark type", f'Delete "{item.name}" and its assignments? '
            "Questions, attempts, notes and other bookmark types are kept. This clears Undo/Redo history.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        ) == QMessageBox.StandardButton.Yes:
            self.run(lambda: self.service.delete_bookmark_type(item.id), clears_history=True)
