from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel,
    QListWidget, QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout,
)

from question_tracker.application.library_service import LibraryError, LibraryService
from question_tracker.domain.library import DocumentType, FileAvailability, FileRole
from question_tracker.ui.layout_spacing import PAGE_MARGIN, compact_layout


class ReferenceDialog(QDialog):
    """Edit associations only; removing a reference never deletes its PDF."""

    def __init__(self, service: LibraryService, question_set_id: str, parent=None):
        super().__init__(parent)
        self.service = service
        self.question_set_id = question_set_id
        self.setWindowTitle("Manage References")
        self.resize(620, 360)
        self.references = QListWidget()
        self.available = QComboBox()
        self.add_button = QPushButton("Add reference")
        self.remove_button = QPushButton("Remove association")
        self.up_button = QPushButton("Move up")
        self.down_button = QPushButton("Move down")
        self.add_button.clicked.connect(self._add)
        self.remove_button.clicked.connect(self._remove)
        self.up_button.clicked.connect(lambda: self._move(-1))
        self.down_button.clicked.connect(lambda: self._move(1))
        self.references.currentRowChanged.connect(self._update_actions)
        actions = QHBoxLayout()
        for button in (self.remove_button, self.up_button, self.down_button):
            actions.addWidget(button)
        actions.addStretch()
        add_row = QHBoxLayout()
        add_row.addWidget(self.available, 1)
        add_row.addWidget(self.add_button)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.accept)
        layout = QVBoxLayout(self)
        hint = QLabel("Changes are saved immediately. Removing an association keeps the PDF and tracking.")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addWidget(self.references, 1)
        layout.addLayout(actions)
        layout.addWidget(QLabel("Existing managed solution PDFs"))
        layout.addLayout(add_row)
        layout.addWidget(close)
        compact_layout(layout, margin=PAGE_MARGIN)
        compact_layout(actions)
        compact_layout(add_row)
        self._refresh()

    @staticmethod
    def _label(file):
        suffix = " [Missing]" if file.availability is FileAvailability.MISSING else ""
        return file.relative_path.as_posix() + suffix

    def _refresh(self, selected_id=None):
        files = self.service.files_for_question_set(self.question_set_id, FileRole.REFERENCE)
        self.references.clear()
        for file in files:
            item = QListWidgetItem(self._label(file))
            item.setData(Qt.ItemDataRole.UserRole, file.id)
            self.references.addItem(item)
            if file.id == selected_id:
                self.references.setCurrentItem(item)
        associated = {file.id for file in files}
        self.available.clear()
        for file in self.service.list_files():
            if file.document_type is DocumentType.SOLUTION and file.id not in associated:
                self.available.addItem(self._label(file), file.id)
        self.add_button.setEnabled(self.available.count() > 0)
        self._update_actions()

    def _update_actions(self, *_):
        row = self.references.currentRow()
        self.remove_button.setEnabled(row >= 0)
        self.up_button.setEnabled(row > 0)
        self.down_button.setEnabled(0 <= row < self.references.count() - 1)

    def _apply(self, operation, selected_id=None):
        try:
            operation()
            self._refresh(selected_id)
        except LibraryError as error:
            QMessageBox.warning(self, "Manage References", str(error))

    def _add(self):
        file_id = self.available.currentData()
        if file_id is not None:
            self._apply(lambda: self.service.associate_reference(self.question_set_id, file_id), file_id)

    def _remove(self):
        item = self.references.currentItem()
        if item is not None:
            file_id = item.data(Qt.ItemDataRole.UserRole)
            self._apply(lambda: self.service.remove_reference(self.question_set_id, file_id))

    def _move(self, offset):
        item = self.references.currentItem()
        if item is not None:
            file_id = item.data(Qt.ItemDataRole.UserRole)
            index = self.references.currentRow() + offset
            self._apply(lambda: self.service.reorder_reference(self.question_set_id, file_id, index), file_id)
