from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from question_tracker.application.library_service import LibraryService
from question_tracker.application.tracking_service import TrackingService
from question_tracker.domain.tracking import SetupState
from question_tracker.paths import ApplicationPaths
from question_tracker.ui.question_editor import QuestionEditorDialog


class HomeScreen(QWidget):
    """Small home screen for finding question sets that need setup."""

    def __init__(
        self,
        service: TrackingService,
        paths: ApplicationPaths,
        parent: QWidget | None = None,
        *,
        open_tracker: Callable[[str], None] | None = None,
        open_marking: Callable[[str | None], None] | None = None,
        library_service: LibraryService | None = None,
    ) -> None:
        super().__init__(parent)
        self.service = service
        self.paths = paths
        self.open_tracker = open_tracker
        self.open_marking = open_marking
        self.library_service = library_service or LibraryService(
            service.database, paths.managed_library_root
        )

        heading = QLabel("Question Tracker")
        heading.setObjectName("homeHeading")
        heading.setStyleSheet("font-size: 22px; font-weight: bold;")

        library_path = QLabel(f"Managed library: {paths.managed_library_root}")
        library_path.setWordWrap(True)

        self.question_set_list = QListWidget()
        self.question_set_list.setObjectName("questionSetList")
        self.question_set_list.itemDoubleClicked.connect(self._open_item)

        self.open_button = QPushButton("Open")
        self.open_button.setObjectName("openQuestionSetButton")
        self.open_button.clicked.connect(self.open_selected_question_set)
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.setObjectName("refreshHomeButton")
        self.refresh_button.clicked.connect(self.refresh)
        self.marking_button = QPushButton("Marking")
        self.marking_button.setObjectName("openMarkingFromHomeButton")
        self.marking_button.setEnabled(open_marking is not None)
        if open_marking is not None:
            self.marking_button.clicked.connect(self.open_marking_workspace)

        button_row = QHBoxLayout()
        button_row.addWidget(self.open_button)
        button_row.addWidget(self.marking_button)
        button_row.addWidget(self.refresh_button)
        button_row.addStretch()

        layout = QVBoxLayout(self)
        layout.addWidget(heading)
        layout.addWidget(library_path)
        layout.addWidget(QLabel("Question sets"))
        layout.addWidget(self.question_set_list)
        layout.addLayout(button_row)

        self.refresh()

    def refresh(self) -> None:
        self.library_service.refresh()
        selected_id = self._selected_question_set_id()
        self.question_set_list.clear()
        selected_item: QListWidgetItem | None = None
        for question_set in self.service.list_question_sets():
            state = (
                "Needs setup"
                if question_set.setup_state.value == "needs_setup"
                else "Ready"
            )
            kind = question_set.kind.value.replace("_", " ").title()
            item = QListWidgetItem(f"{question_set.display_name} — {kind} — {state}")
            item.setData(Qt.ItemDataRole.UserRole, question_set.id)
            self.question_set_list.addItem(item)
            if question_set.id == selected_id:
                selected_item = item
        if selected_item is not None:
            self.question_set_list.setCurrentItem(selected_item)
        elif self.question_set_list.count():
            self.question_set_list.setCurrentRow(0)
        self.open_button.setEnabled(self.question_set_list.count() > 0)

    def open_selected_question_set(self) -> None:
        question_set_id = self._selected_question_set_id()
        if question_set_id is None:
            return
        question_set = self.service.get_question_set(question_set_id)
        if (
            question_set.setup_state is SetupState.READY
            and self.open_tracker is not None
        ):
            self.open_tracker(question_set_id)
            return
        editor = QuestionEditorDialog(self.service, question_set_id, self)
        editor.exec()
        self.refresh()

    def open_marking_workspace(self) -> None:
        if self.open_marking is not None:
            self.open_marking(self._selected_question_set_id())

    def _open_item(self, item: QListWidgetItem) -> None:
        self.question_set_list.setCurrentItem(item)
        self.open_selected_question_set()

    def _selected_question_set_id(self) -> str | None:
        item = self.question_set_list.currentItem()
        if item is None:
            return None
        return str(item.data(Qt.ItemDataRole.UserRole))
