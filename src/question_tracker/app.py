from __future__ import annotations

import sys
from collections.abc import Sequence

from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtGui import QUndoStack
from PySide6.QtWidgets import QApplication, QFileDialog, QMainWindow, QMessageBox, QPushButton

from question_tracker.application.library_service import LibraryService
from question_tracker.application.backup_service import BackupError, BackupService, RecoveryError
from question_tracker.application.tracking_service import TrackingService
from question_tracker.application.review_service import ReviewFilter, ReviewService
from question_tracker.paths import ApplicationPaths
from question_tracker.persistence.database import Database
from question_tracker.ui.home import HomeScreen
from question_tracker.ui.review import ReviewScreen
from question_tracker.ui.marking import MarkingWorkspace
from question_tracker.ui.tracker import TrackerScreen
from question_tracker.ui.preferences import UiPreferences
from question_tracker.ui.navigation import Destination, NavigationHistory


class MainWindow(QMainWindow):
    """Application shell hosting the current Home screen."""

    def __init__(
        self,
        paths: ApplicationPaths,
        tracking_service: TrackingService,
        library_service: LibraryService | None = None,
        *, preferences: UiPreferences | None = None,
    ) -> None:
        super().__init__()
        self.preferences = preferences or UiPreferences()
        self.expanded_home_roots: set[tuple[str, ...]] = set()
        self.history = NavigationHistory()
        self._restoring_history = False
        self.paths = paths
        self.tracking_service = tracking_service
        self.library_service = library_service or LibraryService(
            tracking_service.database, paths.managed_library_root
        )
        self.undo_stack = QUndoStack(self)
        # QObject emits destroyed before deleting its children. The stack was
        # created before the screens and can therefore be deleted first; its
        # destructor clears commands and emits indexChanged. Do not refresh
        # still-alive screen children from those teardown notifications.
        self.destroyed.connect(
            lambda _=None, stack=self.undo_stack: stack.blockSignals(True)
        )
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

    def _capture_destination(self) -> None:
        current = self.history.current
        widget = self.centralWidget()
        if current is None:
            return
        if isinstance(widget, TrackerScreen):
            current = Destination("tracker", (widget.current_question_set_id, widget.selected_question_id), current.on_back)
        elif isinstance(widget, ReviewScreen):
            current = Destination("review", (widget.module_combo.currentData(), widget.filter_combo.currentData()))
        elif isinstance(widget, MarkingWorkspace):
            # Preserve file-only destinations, but also capture an explicitly
            # cleared tracking selection instead of retaining a stale set ID.
            if widget.active_question_set_id is not None or current.screen != "file_marking":
                current = Destination("marking", (widget.active_question_set_id, widget.selected_question_id), current.on_back)
        self.history.replace_current(current)

    def _visit(self, destination: Destination) -> None:
        # Explicit Home/Back-to-Review actions remain ordinary destinations.
        # Only thumb-button navigation moves the history cursor directly.
        if not self._restoring_history:
            self._capture_destination()
            self.history.visit(destination)

    def navigate(self, offset: int) -> None:
        self._capture_destination()
        destination = self.history.move(offset)
        if destination is None:
            return
        self._restoring_history = True
        try:
            opener = {
                "home": self.show_home, "review": self.show_review,
                "tracker": self.show_tracker, "marking": self.show_marking,
                "file_marking": self.show_file_in_marking,
            }[destination.screen]
            kwargs = {"on_back": destination.on_back} if destination.screen in ("tracker", "marking") else {}
            opener(*destination.args, **kwargs)
        finally:
            self._restoring_history = False

    def mouseReleaseEvent(self, event) -> None:
        # Qt propagates ignored mouse events up to the window. A child that
        # accepts the event keeps it, so embedded controls get first refusal.
        if event.button() in (Qt.MouseButton.BackButton, Qt.MouseButton.ForwardButton):
            self.navigate(-1 if event.button() == Qt.MouseButton.BackButton else 1)
            event.accept()
            return
        super().mouseReleaseEvent(event)

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
        self.history = NavigationHistory()
        self.expanded_home_roots.clear()
        self.show_home()

    def show_home(self) -> None:
        self._visit(Destination("home"))
        self.setCentralWidget(
            HomeScreen(
                self.tracking_service,
                self.paths,
                self,
                open_tracker=self.show_tracker,
                open_review=self.show_review,
                open_marking=self.show_marking,
                open_file_marking=self.show_file_in_marking,
                library_service=self.library_service,
                preferences=self.preferences,
                expanded_roots=self.expanded_home_roots,
            )
        )

    def show_review(self, module=None, filter=ReviewFilter.REATTEMPT) -> None:
        self._visit(Destination("review", (module, filter)))
        self.setCentralWidget(ReviewScreen(
            ReviewService(self.tracking_service.database), on_back=self.show_home,
            initial_module=module, initial_filter=filter, parent=self,
            open_tracker=lambda item, selected_filter: self.show_tracker(
                item.question_set.id, item.question.id,
                on_back=lambda: self.show_review(item.module, selected_filter),
            ),
            open_marking=lambda item, selected_filter: self.show_marking(
                item.question_set.id, item.question.id,
                on_back=lambda: self.show_review(item.module, selected_filter),
            ),
        ))

    def show_tracker(self, question_set_id: str, selected_question_id: str | None = None, *, on_back=None) -> None:
        self._visit(Destination("tracker", (question_set_id, selected_question_id), on_back))
        self.setCentralWidget(
            TrackerScreen(
                self.tracking_service,
                self.library_service,
                self.undo_stack,
                on_back=on_back or self.show_home,
                on_open_marking=self.show_marking,
                initial_question_set_id=question_set_id,
                preferences=self.preferences,
                parent=self,
            )
        )
        if selected_question_id is not None:
            self.centralWidget().select_question(selected_question_id, reveal=True)
        if on_back is not None:
            self.centralWidget().findChild(QPushButton, "backToHomeButton").setText("Back to Review")

    def show_file_in_marking(self, file_id: str) -> None:
        self._visit(Destination("file_marking", (file_id,)))
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
        *, on_back=None,
    ) -> None:
        self._visit(Destination("marking", (question_set_id, selected_question_id), on_back))
        self.setCentralWidget(
            MarkingWorkspace(
                self.tracking_service,
                self.library_service,
                self.undo_stack,
                on_back=on_back or self.show_home,
                initial_question_set_id=question_set_id,
                initial_question_id=selected_question_id,
                parent=self,
            )
        )
        if on_back is not None:
            self.centralWidget().findChild(QPushButton, "markingBackButton").setText("Back to Review")


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
