from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut, QUndoStack
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLayout,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from question_tracker.application.library_service import LibraryService
from question_tracker.application.tracker_view import (
    TrackerSnapshot,
    adjacent_leaf_id,
    build_tracker_snapshot,
)
from question_tracker.application.tracking_service import TrackingService
from question_tracker.domain.library import DocumentType, FileRole, LibraryModule
from question_tracker.domain.tracking import AttemptStatus, SetupState
from question_tracker.ui.colours import contrasting_text_colour
from question_tracker.ui.layout_spacing import (
    CONTROL_SPACING,
    PAGE_MARGIN,
    SECTION_SPACING,
    compact_layout,
)
from question_tracker.ui.pdf_pane import PdfPane
from question_tracker.ui.tracker import (
    ATTEMPT_STYLES,
    STATUS_SHORTCUTS,
    TRACKER_METRICS,
    AttemptEditorDialog,
    QuestionAttemptsDialog,
    QuestionCell,
    text_entry_has_focus,
)
from question_tracker.ui.tracker_commands import (
    AddAttemptCommand,
    DeleteAttemptCommand,
    EditAttemptCommand,
    EditQuestionNoteCommand,
    SetBookmarkCommand,
)


COMPACT_TRACKER_SCALE = 0.65


def _compact_scaled(value: int) -> int:
    return max(1, round(value * COMPACT_TRACKER_SCALE))


class CompactQuestionStrip(QWidget):
    """A single-set rendering of the existing tracker snapshot model."""

    question_selected = Signal(str)
    attempt_requested = Signal(str, str)
    details_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.question_cells: dict[str, QuestionCell] = {}
        self._scroll = QScrollArea()
        self._scroll.setObjectName("compactTrackerScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._scroll)

    def render(
        self, snapshot: TrackerSnapshot | None, selected_question_id: str | None
    ) -> None:
        content = QWidget()
        grid = QGridLayout(content)
        grid.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        metrics = TRACKER_METRICS
        grid.setHorizontalSpacing(_compact_scaled(metrics.horizontal_spacing))
        grid.setVerticalSpacing(_compact_scaled(metrics.vertical_spacing))
        self.question_cells = {}
        if snapshot is None or not snapshot.columns:
            label = QLabel("Choose a ready question set with markable questions.")
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            grid.addWidget(label, 0, 0)
            self._scroll.setWidget(content)
            return

        row = 0
        topics = sorted(
            {span.topic for span in snapshot.topic_spans},
            key=lambda topic: topic.sort_order,
        )
        topic_rows = {topic.id: row + index for index, topic in enumerate(topics)}
        for span in snapshot.topic_spans:
            label = QLabel(span.topic.title)
            label.setObjectName(
                f"compactTopicSpan-{span.topic.id}-{span.start_column}-{span.end_column}"
            )
            label.setAlignment(
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
            )
            label.setFixedHeight(_compact_scaled(metrics.topic_row_height))
            label.setStyleSheet(
                f"color: {contrasting_text_colour(span.topic.colour)}; "
                f"background: {span.topic.colour}; "
                "border-bottom: 1px solid #6f7780; padding: 0px 3px;"
            )
            grid.addWidget(
                label,
                topic_rows[span.topic.id],
                span.start_column,
                1,
                span.end_column - span.start_column + 1,
            )
        row += len(topics)

        for heading_row in range(snapshot.heading_row_count):
            grid.setRowMinimumHeight(
                row + heading_row, _compact_scaled(metrics.heading_row_height)
            )
        for heading in snapshot.hierarchy_headings:
            label = QLabel(heading.question.local_label)
            label.setObjectName(f"compactHierarchyHeading-{heading.question.id}")
            label.setToolTip(heading.visible_label)
            label.setAlignment(
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
            )
            label.setFixedHeight(_compact_scaled(metrics.heading_row_height))
            label.setStyleSheet(
                "border-bottom: 1px solid #91a4b7; padding: 0px 2px;"
            )
            grid.addWidget(
                label,
                row + heading.depth,
                heading.start_column,
                1,
                heading.end_column - heading.start_column + 1,
            )

        attempt_row = row + snapshot.heading_row_count
        for column_index, column in enumerate(snapshot.columns):
            cell = QuestionCell(column, COMPACT_TRACKER_SCALE)
            cell.selected.connect(self.question_selected)
            cell.attempt_requested.connect(self.attempt_requested)
            cell.details_requested.connect(self.details_requested)
            cell.set_selected(column.question.id == selected_question_id)
            grid.addWidget(
                cell,
                attempt_row,
                column_index,
                1,
                1,
                Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter,
            )
            grid.setColumnMinimumWidth(
                column_index, _compact_scaled(metrics.cell_width)
            )
            self.question_cells[column.question.id] = cell
        self._scroll.setWidget(content)
        if selected_question_id in self.question_cells:
            selected = self.question_cells[selected_question_id]
            self._scroll.ensureWidgetVisible(selected, 12, 12)


class CompactTrackerWidget(QWidget):
    """Single-question-set marking controls backed by existing services and undo."""

    def __init__(
        self,
        service: TrackingService,
        library_service: LibraryService,
        undo_stack: QUndoStack,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.service = service
        self.library_service = library_service
        self.undo_stack = undo_stack
        self.active_question_set_id: str | None = None
        self.selected_question_id: str | None = None
        self.snapshot: TrackerSnapshot | None = None
        self.status_buttons: dict[AttemptStatus, QPushButton] = {}
        self._shortcuts: list[QShortcut] = []
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumWidth(0)
        self.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )

        self.active_selector = QComboBox()
        self.active_selector.setObjectName("activeMarkingQuestionSet")
        self.active_selector.currentIndexChanged.connect(
            self._active_selector_changed
        )
        self.position_label = QLabel("No active tracker")
        self.position_label.setObjectName("compactQuestionPosition")
        self.progress_label = QLabel("")
        self.progress_label.setObjectName("compactProgress")
        self.undo_button = QPushButton("Undo")
        self.undo_button.setObjectName("markingUndoButton")
        self.undo_button.clicked.connect(undo_stack.undo)
        self.redo_button = QPushButton("Redo")
        self.redo_button.setObjectName("markingRedoButton")
        self.redo_button.clicked.connect(undo_stack.redo)
        undo_stack.canUndoChanged.connect(self.undo_button.setEnabled)
        undo_stack.canRedoChanged.connect(self.redo_button.setEnabled)
        undo_stack.indexChanged.connect(self._undo_stack_changed)

        header = QHBoxLayout()
        header.addWidget(QLabel("Active tracker:"))
        header.addWidget(self.active_selector, 1)
        header.addWidget(self.position_label)
        header.addWidget(self.progress_label)
        header.addWidget(self.undo_button)
        header.addWidget(self.redo_button)

        controls = QHBoxLayout()
        for shortcut, status in STATUS_SHORTCUTS.items():
            style = ATTEMPT_STYLES[status]
            button = QPushButton(f"{shortcut}  {style.label}")
            button.setObjectName(f"compactMark{status.value.title().replace('_', '')}Button")
            button.setStyleSheet(
                f"background: {style.colour}; color: {style.text_colour}; "
                "font-weight: bold; padding: 4px;"
            )
            button.clicked.connect(
                lambda checked=False, selected_status=status: self.mark_selected(
                    selected_status, advance=True
                )
            )
            controls.addWidget(button)
            self.status_buttons[status] = button
        controls.addStretch()
        self.bookmark_button = QPushButton("☆ Bookmark")
        self.bookmark_button.setObjectName("compactBookmarkButton")
        self.bookmark_button.clicked.connect(self._toggle_bookmark)
        self.note_button = QPushButton("Note")
        self.note_button.setObjectName("compactNoteButton")
        self.note_button.clicked.connect(self._edit_question_note)
        controls.addWidget(self.bookmark_button)
        controls.addWidget(self.note_button)

        self.strip = CompactQuestionStrip()
        self.strip.setObjectName("compactQuestionStrip")
        # Keep the PDFs dominant; longer topic/attempt content remains scrollable.
        self.strip.setMaximumHeight(160)
        self.strip.question_selected.connect(self.select_question)
        self.strip.attempt_requested.connect(self._attempt_requested)
        self.strip.details_requested.connect(self._open_question_attempts)

        layout = QVBoxLayout(self)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        compact_layout(layout, margin=CONTROL_SPACING)
        compact_layout(header)
        compact_layout(controls)
        layout.addLayout(header)
        layout.addLayout(controls)
        layout.addWidget(self.strip)

        self._populate_active_selector()
        self._set_tracking_enabled(False)

    def install_shortcuts(
        self,
        parent: QWidget,
        *,
        previous_pdf_page: Callable[[], None] | None = None,
        next_pdf_page: Callable[[], None] | None = None,
    ) -> None:
        actions: dict[str, Callable[[], None]] = {
            "Left": lambda: self.navigate(-1),
            "Right": lambda: self.navigate(1),
            "Ctrl+Z": self.undo_stack.undo,
            "Ctrl+Y": self.undo_stack.redo,
        }
        if previous_pdf_page is not None:
            actions["Up"] = previous_pdf_page
        if next_pdf_page is not None:
            actions["Down"] = next_pdf_page
        actions.update(
            {
                key: lambda status=status: self.mark_selected(status, advance=True)
                for key, status in STATUS_SHORTCUTS.items()
            }
        )
        for key, action in actions.items():
            shortcut = QShortcut(QKeySequence(key), parent)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(
                lambda selected_action=action: self._route_shortcut(selected_action)
            )
            self._shortcuts.append(shortcut)

    def set_active_question_set(
        self, question_set_id: str | None, selected_question_id: str | None = None
    ) -> None:
        if question_set_id is None:
            self.active_selector.setCurrentIndex(0)
            return
        index = self.active_selector.findData(question_set_id)
        if index < 0:
            return
        self.active_selector.blockSignals(True)
        self.active_selector.setCurrentIndex(index)
        self.active_selector.blockSignals(False)
        self.active_question_set_id = question_set_id
        self.selected_question_id = selected_question_id
        self.refresh()

    def refresh(self) -> None:
        if self.active_question_set_id is None:
            self.snapshot = None
            self.selected_question_id = None
            self.position_label.setText("No active tracker")
            self.progress_label.setText("")
            self.strip.render(None, None)
            self._set_tracking_enabled(False)
            return
        self.snapshot = build_tracker_snapshot(
            self.service, self.active_question_set_id
        )
        question_ids = [
            column.question.id for column in self.snapshot.columns
        ]
        if self.selected_question_id not in question_ids:
            self.selected_question_id = question_ids[0] if question_ids else None
        self.strip.render(self.snapshot, self.selected_question_id)
        self._update_selected_state()
        progress = self.snapshot.progress
        if progress.mastered:
            progress_text = "Mastered"
        elif progress.complete:
            progress_text = "Complete"
        else:
            progress_text = f"{self.snapshot.attempted_count}/{len(self.snapshot.columns)} attempted"
        self.progress_label.setText(progress_text)
        self._set_tracking_enabled(self.selected_question_id is not None)

    def select_question(self, question_id: str) -> None:
        if self.snapshot is None or question_id not in self.strip.question_cells:
            return
        self.selected_question_id = question_id
        for cell_id, cell in self.strip.question_cells.items():
            cell.set_selected(cell_id == question_id)
        self._update_selected_state()
        self.setFocus(Qt.FocusReason.OtherFocusReason)

    def navigate(self, offset: int) -> None:
        if self.snapshot is None:
            return
        target = adjacent_leaf_id(self.snapshot, self.selected_question_id, offset)
        if target is not None:
            self.select_question(target)

    def mark_selected(
        self, status: AttemptStatus, *, advance: bool = False
    ) -> None:
        if self.selected_question_id is None:
            return
        self.undo_stack.push(
            AddAttemptCommand(self.service, self.selected_question_id, status)
        )
        if advance:
            self.navigate(1)

    def _populate_active_selector(self) -> None:
        selected_id = self.active_question_set_id
        self.active_selector.blockSignals(True)
        self.active_selector.clear()
        self.active_selector.addItem("Select a ready question set…", None)
        for question_set in self.service.list_question_sets(SetupState.READY):
            module = self.library_service.module_for_question_set(question_set.id)
            label = (
                f"{module.module_name} — {question_set.display_name}"
                if module is not None
                else question_set.display_name
            )
            self.active_selector.addItem(label, question_set.id)
        index = self.active_selector.findData(selected_id)
        self.active_selector.setCurrentIndex(max(0, index))
        self.active_selector.blockSignals(False)

    def _active_selector_changed(self, index: int) -> None:
        question_set_id = self.active_selector.itemData(index)
        self.active_question_set_id = (
            str(question_set_id) if question_set_id is not None else None
        )
        self.selected_question_id = None
        self.refresh()

    def _update_selected_state(self) -> None:
        if self.snapshot is None or self.selected_question_id is None:
            self.position_label.setText("No selected question")
            self.bookmark_button.setText("☆ Bookmark")
            self.note_button.setText("Note")
            return
        columns = list(self.snapshot.columns)
        index = next(
            index
            for index, column in enumerate(columns)
            if column.question.id == self.selected_question_id
        )
        column = columns[index]
        self.position_label.setText(
            f"{index + 1}/{len(columns)} — {column.visible_label}"
        )
        self.bookmark_button.setText(
            "★ Bookmarked" if column.question.bookmarked else "☆ Bookmark"
        )
        self.note_button.setText("Note ●" if column.question.note else "Note")
        self.note_button.setToolTip(
            column.question.note or "Edit selected question note"
        )

    def _set_tracking_enabled(self, enabled: bool) -> None:
        for button in self.status_buttons.values():
            button.setEnabled(enabled)
        self.bookmark_button.setEnabled(enabled)
        self.note_button.setEnabled(enabled)
        self.undo_button.setEnabled(self.undo_stack.canUndo())
        self.redo_button.setEnabled(self.undo_stack.canRedo())

    def _toggle_bookmark(self) -> None:
        if self.selected_question_id is None:
            return
        question = self.service.get_question(self.selected_question_id)
        self.undo_stack.push(
            SetBookmarkCommand(
                self.service,
                question.id,
                question.bookmarked,
                not question.bookmarked,
            )
        )

    def _edit_question_note(self) -> None:
        if self.selected_question_id is None:
            return
        question = self.service.get_question(self.selected_question_id)
        note, accepted = QInputDialog.getMultiLineText(
            self, "Question note", "Note", question.note
        )
        if accepted and note != question.note:
            self.undo_stack.push(
                EditQuestionNoteCommand(
                    self.service, question.id, question.note, note
                )
            )

    def _attempt_requested(self, question_id: str, attempt_id: str) -> None:
        self.select_question(question_id)
        attempt = self.service.get_attempt(attempt_id)
        editor = AttemptEditorDialog(attempt, self)
        if editor.exec() != QDialog.DialogCode.Accepted:
            return
        if editor.delete_requested:
            self.undo_stack.push(DeleteAttemptCommand(self.service, attempt))
            return
        status = editor.selected_status()
        note = editor.note()
        if status != attempt.status or note != attempt.note:
            self.undo_stack.push(
                EditAttemptCommand(self.service, attempt, status, note)
            )

    def _open_question_attempts(self, question_id: str) -> None:
        self.select_question(question_id)
        if self.snapshot is None:
            return
        column = next(
            column
            for column in self.snapshot.columns
            if column.question.id == question_id
        )
        editor = QuestionAttemptsDialog(
            self.service,
            self.undo_stack,
            question_id,
            column.visible_label,
            self,
        )
        editor.exec()
        self.refresh()

    def _route_shortcut(self, action: Callable[[], None]) -> None:
        if text_entry_has_focus():
            return
        action()

    def _undo_stack_changed(self, index: int) -> None:
        del index
        self.refresh()


class MarkingWorkspace(QWidget):
    """Two independent PDF panes with one compact active question tracker."""

    def __init__(
        self,
        service: TrackingService,
        library_service: LibraryService,
        undo_stack: QUndoStack,
        *,
        on_back: Callable[[], None] | None = None,
        initial_question_set_id: str | None = None,
        initial_question_id: str | None = None,
        initial_file_id: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.service = service
        self.library_service = library_service
        self.undo_stack = undo_stack
        self.on_back = on_back
        self.setMinimumWidth(0)
        self.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding
        )
        library_service.refresh()

        back_button = QPushButton("Back to Home")
        back_button.setObjectName("markingBackButton")
        back_button.setEnabled(on_back is not None)
        if on_back is not None:
            back_button.clicked.connect(on_back)
        heading = QLabel("Marking")
        heading.setStyleSheet("font-size: 20px; font-weight: bold;")
        self.tracker_toggle_button = QPushButton("Hide tracker")
        self.tracker_toggle_button.setObjectName("toggleCompactTrackerButton")
        self.tracker_toggle_button.setCheckable(True)
        self.tracker_toggle_button.setChecked(True)
        header = QHBoxLayout()
        header.addWidget(back_button)
        header.addWidget(heading)
        header.addStretch()
        header.addWidget(self.tracker_toggle_button)

        self.left_pane = PdfPane(library_service, "left")
        self.right_pane = PdfPane(library_service, "right")
        self.document_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.document_splitter.setObjectName("markingDocumentSplitter")
        self.document_splitter.addWidget(self.left_pane)
        self.document_splitter.addWidget(self.right_pane)
        self.document_splitter.setSizes((600, 600))
        self.document_splitter.setChildrenCollapsible(False)
        self.document_splitter.setMinimumWidth(0)
        self.document_splitter.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding
        )
        self.active_pdf_pane = self.left_pane
        self.left_pane.interaction_occurred.connect(
            lambda: self._set_active_pdf_pane(self.left_pane)
        )
        self.right_pane.interaction_occurred.connect(
            lambda: self._set_active_pdf_pane(self.right_pane)
        )
        self._set_active_pdf_pane(self.left_pane)

        self.compact_tracker = CompactTrackerWidget(
            service, library_service, undo_stack
        )
        self.compact_tracker.setObjectName("markingCompactTracker")
        self.compact_tracker.install_shortcuts(
            self,
            previous_pdf_page=lambda: self._navigate_active_pdf(-1),
            next_pdf_page=lambda: self._navigate_active_pdf(1),
        )
        self.tracker_toggle_button.toggled.connect(
            self._tracker_visibility_changed
        )

        layout = QVBoxLayout(self)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        compact_layout(layout, margin=PAGE_MARGIN)
        layout.setSpacing(SECTION_SPACING)
        compact_layout(header)
        layout.addLayout(header)
        layout.addWidget(self.document_splitter, 1)
        layout.addWidget(self.compact_tracker)

        if initial_question_set_id is not None:
            self._load_question_set_context(
                initial_question_set_id, initial_question_id
            )
        elif initial_file_id is not None:
            self._load_file_context(initial_file_id)

    def _load_question_set_context(
        self, question_set_id: str, selected_question_id: str | None
    ) -> None:
        question_set = self.service.get_question_set(question_set_id)
        if question_set.setup_state is SetupState.READY:
            self.compact_tracker.set_active_question_set(
                question_set_id, selected_question_id
            )
        module = self.library_service.module_for_question_set(question_set_id)
        main_files = self.library_service.files_for_question_set(
            question_set_id, FileRole.MAIN
        )
        reference_files = self.library_service.files_for_question_set(
            question_set_id, FileRole.REFERENCE
        )
        self.left_pane.refresh_browser(
            focus_module=module,
            highlighted_file_ids=(file.id for file in main_files),
        )
        self.right_pane.refresh_browser(
            focus_module=module,
            highlighted_file_ids=(file.id for file in reference_files),
        )
        if len(main_files) == 1:
            self.left_pane.open_managed_file(main_files[0].id)
        if len(reference_files) == 1:
            self.right_pane.open_managed_file(reference_files[0].id)

    def _load_file_context(self, file_id: str) -> None:
        managed_file = self.library_service.get_file(file_id)
        parts = managed_file.relative_path.parts
        module = (
            LibraryModule(parts[0], parts[1]) if len(parts) >= 2 else None
        )
        self.left_pane.refresh_browser(
            focus_module=module, highlighted_file_ids=(file_id,)
        )
        self.right_pane.refresh_browser(
            focus_module=module, highlighted_file_ids=(file_id,)
        )
        target = (
            self.right_pane
            if managed_file.document_type is DocumentType.SOLUTION
            else self.left_pane
        )
        target.open_managed_file(file_id)

    def _set_active_pdf_pane(self, pane: PdfPane) -> None:
        self.active_pdf_pane = pane
        self.left_pane.set_active(pane is self.left_pane)
        self.right_pane.set_active(pane is self.right_pane)

    def _navigate_active_pdf(self, offset: int) -> None:
        if self.active_pdf_pane.mode != "pdf":
            return
        if offset < 0:
            self.active_pdf_pane.previous_page()
        elif offset > 0:
            self.active_pdf_pane.next_page()

    def _tracker_visibility_changed(self, visible: bool) -> None:
        self.compact_tracker.setVisible(visible)
        self.tracker_toggle_button.setText(
            "Hide tracker" if visible else "Show tracker"
        )
