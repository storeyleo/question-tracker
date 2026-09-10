from __future__ import annotations

import sys
from collections.abc import Sequence

from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtGui import QUndoStack
from PySide6.QtWidgets import QApplication, QFileDialog, QMainWindow, QMessageBox

from question_tracker.application.library_service import LibraryService
from question_tracker.application.backup_service import BackupError, BackupService, RecoveryError
from question_tracker.application.tracking_service import TrackingService
from question_tracker.paths import ApplicationPaths
from question_tracker.persistence.database import Database
from question_tracker.ui.home import HomeScreen
from question_tracker.ui.marking import MarkingWorkspace
from question_tracker.ui.tracker import TrackerScreen


class MainWindow(QMainWindow):
    """Application shell hosting the current Home screen."""

    def __init__(
        self,
        paths: ApplicationPaths,
        tracking_service: TrackingService,
        library_service: LibraryService | None = None,
    ) -> None:
        super().__init__()
        self.paths = paths
        self.tracking_service = tracking_service
        self.library_service = library_service or LibraryService(
            tracking_service.database, paths.managed_library_root
        )
        self.undo_stack = QUndoStack(self)
        self.setWindowTitle("Question Tracker")
        self.resize(1200, 750)
        self.backups = BackupService(paths)
        data_menu = self.menuBar().addMenu("Data")
        self.export_database_action = data_menu.addAction("Export Database...")
        self.export_bundle_action = data_menu.addAction("Export Portable Bundle...")
        self.import_bundle_action = data_menu.addAction("Import Portable Bundle...")
        self.export_database_action.triggered.connect(lambda: self._export_data(False))
        self.export_bundle_action.triggered.connect(lambda: self._export_data(True))
        self.import_bundle_action.triggered.connect(self._import_bundle)
        self.show_home()

    def _export_data(self, portable: bool) -> None:
        title = "Export Portable Bundle" if portable else "Export Database"
        filename, _ = QFileDialog.getSaveFileName(
            self, title, "", "ZIP bundles (*.zip)" if portable else "SQLite database (*.sqlite3)",
        )
        if not filename:
            return
        try:
            operation = self.backups.export_bundle if portable else self.backups.export_database
            operation(Path(filename))
        except BackupError as error:
            QMessageBox.warning(self, title, str(error))
            return
        QMessageBox.information(self, title, "Export completed.")

    def _import_bundle(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(self, "Import Portable Bundle", "", "ZIP bundles (*.zip)")
        if not filename:
            return
        released = False
        try:
            with self.backups.prepare_import(Path(filename)) as staging:
                if QMessageBox.question(
                    self, "Replace local library?",
                    "Importing this bundle will replace the current Question Tracker database "
                    "and managed files. A safety database backup will be kept. Continue?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                    QMessageBox.StandardButton.Cancel,
                ) != QMessageBox.StandardButton.Yes:
                    return
                # Import is a whole-library replacement; no old screen/undo command may survive.
                widget = self.takeCentralWidget()
                if widget is not None:
                    widget.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                self.undo_stack.clear()
                released = True
                self.backups.replace_from_staging(staging)
        except BackupError as error:
            QMessageBox.warning(self, "Import Portable Bundle", str(error))
            if isinstance(error, RecoveryError):
                self.close()
                return
            if released:
                self._reopen_home()
            return
        self._reopen_home()
        QMessageBox.information(self, "Import Portable Bundle", "Import completed.")

    def _reopen_home(self) -> None:
        database = Database(self.paths.database_path)
        self.tracking_service = TrackingService(database)
        self.library_service = LibraryService(database, self.paths.managed_library_root)
        self.show_home()

    def show_home(self) -> None:
        self.setCentralWidget(
            HomeScreen(
                self.tracking_service,
                self.paths,
                self,
                open_tracker=self.show_tracker,
                open_marking=self.show_marking,
                open_file_marking=self.show_file_in_marking,
                library_service=self.library_service,
            )
        )

    def show_tracker(self, question_set_id: str) -> None:
        self.setCentralWidget(
            TrackerScreen(
                self.tracking_service,
                self.library_service,
                self.undo_stack,
                on_back=self.show_home,
                on_open_marking=self.show_marking,
                initial_question_set_id=question_set_id,
                parent=self,
            )
        )

    def show_file_in_marking(self, file_id: str) -> None:
        self.setCentralWidget(
            MarkingWorkspace(
                self.tracking_service,
                self.library_service,
                self.undo_stack,
                on_back=self.show_home,
                initial_file_id=file_id,
                parent=self,
            )
        )

    def show_marking(
        self,
        question_set_id: str | None = None,
        selected_question_id: str | None = None,
    ) -> None:
        self.setCentralWidget(
            MarkingWorkspace(
                self.tracking_service,
                self.library_service,
                self.undo_stack,
                on_back=self.show_home,
                initial_question_set_id=question_set_id,
                initial_question_id=selected_question_id,
                parent=self,
            )
        )


def main(argv: Sequence[str] | None = None) -> int:
    QCoreApplication.setOrganizationName("Question Tracker")
    QCoreApplication.setApplicationName("Question Tracker")

    application = QApplication(list(argv) if argv is not None else sys.argv)
    paths = ApplicationPaths.default()
    paths.ensure_created()
    database = Database(paths.database_path)
    database.initialize()
    BackupService(paths).automatic_backup()
    library_service = LibraryService(database, paths.managed_library_root)

    tracking_service = TrackingService(database)
    window = MainWindow(paths, tracking_service, library_service)
    window.show()
    return application.exec()
