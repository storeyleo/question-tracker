from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from question_tracker.application.library_service import (
    DestinationConflictError,
    LibraryError,
    LibraryService,
)
from question_tracker.domain.library import DocumentType, LibraryModule, ManagedFile


DOCUMENT_TYPE_LABELS = {
    DocumentType.PROBLEM_SHEET: "Problem Sheet",
    DocumentType.SOLUTION: "Solution",
    DocumentType.PAST_PAPER: "Past Paper",
}


class ImportPdfDialog(QDialog):
    """Select and copy one PDF into a managed Year/Module category."""

    def __init__(
        self,
        library_service: LibraryService,
        document_type: DocumentType,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.library_service = library_service
        self.document_type = document_type
        self.imported_file: ManagedFile | None = None
        self._modules: tuple[LibraryModule, ...] = ()

        label = DOCUMENT_TYPE_LABELS[document_type]
        self.setWindowTitle(f"Add {label}")
        self.setMinimumWidth(500)

        self.source_edit = QLineEdit()
        self.source_edit.setObjectName("importSourceEdit")
        self.source_edit.setReadOnly(True)
        browse_button = QPushButton("Browse...")
        browse_button.setObjectName("browseImportSourceButton")
        browse_button.clicked.connect(self._browse_source)
        source_row = QHBoxLayout()
        source_row.addWidget(self.source_edit, 1)
        source_row.addWidget(browse_button)

        self.destination_name_edit = QLineEdit()
        self.destination_name_edit.setObjectName("importDestinationNameEdit")
        self.destination_name_edit.setPlaceholderText("Filename.pdf")

        self.year_combo = QComboBox()
        self.year_combo.setObjectName("importYearCombo")
        self.year_combo.currentTextChanged.connect(self._populate_modules)
        new_year_button = QPushButton("New Year...")
        new_year_button.setObjectName("createImportYearButton")
        new_year_button.clicked.connect(self._create_year)
        year_row = QHBoxLayout()
        year_row.addWidget(self.year_combo, 1)
        year_row.addWidget(new_year_button)

        self.module_combo = QComboBox()
        self.module_combo.setObjectName("importModuleCombo")
        new_module_button = QPushButton("New Module...")
        new_module_button.setObjectName("createImportModuleButton")
        new_module_button.clicked.connect(self._create_module)
        module_row = QHBoxLayout()
        module_row.addWidget(self.module_combo, 1)
        module_row.addWidget(new_module_button)

        form = QFormLayout()
        form.addRow("Source PDF", source_row)
        form.addRow("Destination filename", self.destination_name_edit)
        form.addRow("Year", year_row)
        form.addRow("Module", module_row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        self.import_button = buttons.button(QDialogButtonBox.StandardButton.Save)
        self.import_button.setText(f"Add {label}")
        self.import_button.setObjectName("confirmImportButton")
        buttons.accepted.connect(self._import)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"Copy a {label.lower()} PDF into the managed library."))
        layout.addLayout(form)
        layout.addWidget(buttons)

        self._reload_targets()

    def set_source_path(self, path: Path) -> None:
        source = Path(path)
        self.source_edit.setText(str(source))
        self.destination_name_edit.setText(source.name)

    def _reload_targets(
        self,
        *,
        selected_year: str | None = None,
        selected_module: str | None = None,
    ) -> None:
        try:
            result = self.library_service.refresh()
        except LibraryError as error:
            QMessageBox.warning(self, "Refresh Library", str(error))
            return
        self._modules = result.modules
        current_year = selected_year or self.year_combo.currentText()
        self.year_combo.blockSignals(True)
        self.year_combo.clear()
        self.year_combo.addItems(result.years)
        year_index = self.year_combo.findText(current_year)
        if year_index >= 0:
            self.year_combo.setCurrentIndex(year_index)
        self.year_combo.blockSignals(False)
        self._populate_modules(self.year_combo.currentText(), selected_module)

    def _populate_modules(
        self, year_name: str, selected_module: str | None = None
    ) -> None:
        current_module = selected_module or self.module_combo.currentText()
        self.module_combo.clear()
        self.module_combo.addItems(
            [
                module.module_name
                for module in self._modules
                if module.year_name == year_name
            ]
        )
        module_index = self.module_combo.findText(current_module)
        if module_index >= 0:
            self.module_combo.setCurrentIndex(module_index)

    def _browse_source(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(
            self, "Select PDF", "", "PDF files (*.pdf)"
        )
        if filename:
            self.set_source_path(Path(filename))

    def _create_year(self) -> None:
        name, accepted = QInputDialog.getText(self, "Create Year", "Year name")
        if not accepted:
            return
        try:
            self.library_service.create_year(name)
        except LibraryError as error:
            QMessageBox.warning(self, "Create Year", str(error))
            return
        self._reload_targets(selected_year=name)

    def _create_module(self) -> None:
        year_name = self.year_combo.currentText()
        if not year_name:
            QMessageBox.warning(self, "Create Module", "Create or select a Year first.")
            return
        name, accepted = QInputDialog.getText(
            self, "Create Module", "Module name"
        )
        if not accepted:
            return
        try:
            self.library_service.create_module(year_name, name)
        except LibraryError as error:
            QMessageBox.warning(self, "Create Module", str(error))
            return
        self._reload_targets(selected_year=year_name, selected_module=name)

    def _import(self) -> None:
        source_text = self.source_edit.text()
        if not source_text:
            QMessageBox.warning(self, "Add PDF", "Select a source PDF first.")
            return
        try:
            self.imported_file = self.library_service.import_pdf(
                Path(source_text),
                self.year_combo.currentText(),
                self.module_combo.currentText(),
                self.document_type,
                destination_name=self.destination_name_edit.text(),
            )
        except DestinationConflictError as error:
            if error.file_id is None:
                QMessageBox.warning(self, "File already exists", str(error))
                return
            if QMessageBox.question(
                self, "Replace existing PDF?",
                f"{error}\n\nReplace its contents and keep the existing questions and tracking?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            ) != QMessageBox.StandardButton.Yes:
                return
            try:
                self.imported_file = self.library_service.replace_file(error.file_id, Path(source_text))
            except LibraryError as replacement_error:
                QMessageBox.warning(self, "Replace PDF", str(replacement_error))
                return
        except LibraryError as error:
            QMessageBox.warning(self, "Add PDF", str(error))
            return
        self.accept()
