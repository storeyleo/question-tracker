from __future__ import annotations

import sys
from collections.abc import Sequence

from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QUndoStack
from PySide6.QtWidgets import QApplication, QMainWindow

from question_tracker.application.library_service import LibraryService
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
    library_service = LibraryService(database, paths.managed_library_root)

    tracking_service = TrackingService(database)
    window = MainWindow(paths, tracking_service, library_service)
    window.show()
    return application.exec()
