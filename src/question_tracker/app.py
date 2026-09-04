from __future__ import annotations

import sys
from collections.abc import Sequence

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication, QMainWindow

from question_tracker.application.library_service import LibraryService
from question_tracker.application.tracking_service import TrackingService
from question_tracker.paths import ApplicationPaths
from question_tracker.persistence.database import Database
from question_tracker.ui.home import HomeScreen


class MainWindow(QMainWindow):
    """Application shell hosting the current Home screen."""

    def __init__(self, paths: ApplicationPaths, tracking_service: TrackingService) -> None:
        super().__init__()
        self.setWindowTitle("Question Tracker")
        self.resize(900, 600)
        self.setCentralWidget(HomeScreen(tracking_service, paths, self))


def main(argv: Sequence[str] | None = None) -> int:
    QCoreApplication.setOrganizationName("Question Tracker")
    QCoreApplication.setApplicationName("Question Tracker")

    application = QApplication(list(argv) if argv is not None else sys.argv)
    paths = ApplicationPaths.default()
    paths.ensure_created()
    database = Database(paths.database_path)
    database.initialize()
    LibraryService(database, paths.managed_library_root).scan_library()

    tracking_service = TrackingService(database)
    window = MainWindow(paths, tracking_service)
    window.show()
    return application.exec()
