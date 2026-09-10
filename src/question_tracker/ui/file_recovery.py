"""Shared confirmation for replacing an existing managed PDF."""

from PySide6.QtWidgets import QMessageBox, QWidget

from question_tracker.application.library_service import LibraryService


def confirm_pdf_replacement(parent: QWidget, service: LibraryService, file_id: str) -> bool:
    path = service.path_for_file(file_id)
    if not path.exists():
        return True
    return QMessageBox.question(
        parent,
        "Replace PDF",
        f"Replace the managed contents of {path.name}?\n\n"
        "Questions, tracking and references will be kept. The source PDF is not changed.",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        QMessageBox.StandardButton.Cancel,
    ) == QMessageBox.StandardButton.Yes
