from __future__ import annotations

import sys
from collections.abc import Sequence

from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtWidgets import QApplication, QLabel, QMainWindow, QVBoxLayout, QWidget

from question_tracker.paths import ApplicationPaths
from question_tracker.persistence.database import Database


class MainWindow(QMainWindow):
    """Minimal application shell for the first milestone."""

    def __init__(self, paths: ApplicationPaths) -> None:
        super().__init__()
        self.setWindowTitle("Question Tracker")
        self.resize(900, 600)

        heading = QLabel("Question Tracker")
        heading.setAlignment(Qt.AlignmentFlag.AlignCenter)

        library_path = QLabel(f"Managed library: {paths.managed_library_root}")
        library_path.setAlignment(Qt.AlignmentFlag.AlignCenter)
        library_path.setWordWrap(True)

        layout = QVBoxLayout()
        layout.addStretch()
        layout.addWidget(heading)
        layout.addWidget(library_path)
        layout.addStretch()

        home = QWidget()
        home.setLayout(layout)
        self.setCentralWidget(home)


def main(argv: Sequence[str] | None = None) -> int:
    QCoreApplication.setOrganizationName("Question Tracker")
    QCoreApplication.setApplicationName("Question Tracker")

    application = QApplication(list(argv) if argv is not None else sys.argv)
    paths = ApplicationPaths.default()
    paths.ensure_created()
    Database(paths.database_path).initialize()

    window = MainWindow(paths)
    window.show()
    return application.exec()

