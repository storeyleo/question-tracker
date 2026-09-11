from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import QRect, QTimer, Qt, Signal
from PySide6.QtGui import QAction, QColor, QDrag, QKeySequence, QPainter, QPainterPath, QPalette, QPen, QShortcut, QUndoStack
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QHeaderView,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from question_tracker.application.library_service import LibraryService
from question_tracker.application.tracker_appearance import (
    DEFAULT_APPEARANCE,
    TrackerAppearance,
    load_tracker_appearance,
)
from question_tracker.application.tracker_view import (
    ModuleTrackerSnapshot,
    QuestionColumn,
    TrackerSnapshot,
    adjacent_leaf_id,
    build_module_tracker_snapshot,
)
from question_tracker.application.tracking_service import TrackingService
from question_tracker.domain.library import (
    DocumentType,
    FileAvailability,
    FileRole,
    ManagedFile,
)
from question_tracker.domain.tracking import (
    Attempt,
    AttemptStatus,
    QuestionSet,
    SetupState,
    latest_attempt,
)
from question_tracker.ui.colours import contrasting_text_colour
from question_tracker.ui.tracker_appearance_dialog import TrackerAppearanceDialog
from question_tracker.ui.question_editor import QuestionEditorDialog
from question_tracker.ui.bookmark_controls import BookmarkButton, install_bookmark_menu, toggle_default_bookmark
from question_tracker.ui.preferences import (
    UiPreferences, MIN_ZOOM_PERCENT, MAX_ZOOM_PERCENT, DEFAULT_ZOOM_PERCENT,
)
from question_tracker.ui.tracker_commands import (
    AddAttemptCommand,
    DeleteAttemptCommand,
    DeleteAttemptsCommand,
    EditAttemptCommand,
    EditQuestionNoteCommand,
)


@dataclass(frozen=True, slots=True)
class AttemptStyle:
    label: str
    colour: str
    text_colour: str = "white"


ATTEMPT_STYLES = {
    AttemptStatus.CORRECT: AttemptStyle("Correct", "#2e8b57"),
    AttemptStatus.PARTIAL: AttemptStyle("Partial", "#e69500", "black"),
    AttemptStatus.INCORRECT: AttemptStyle("Incorrect", "#c62828"),
    AttemptStatus.NO_START: AttemptStyle("No Start", "#741414"),
}

STATUS_SHORTCUTS = {
    "1": AttemptStatus.CORRECT,
    "2": AttemptStatus.PARTIAL,
    "3": AttemptStatus.INCORRECT,
    "4": AttemptStatus.NO_START,
}

ZOOM_STEP = 10


@dataclass(frozen=True, slots=True)
class TrackerMetrics:
    appearance: TrackerAppearance = DEFAULT_APPEARANCE
    cell_margin: int = 3
    stack_margin: int = 4
    attempt_max_x_steps: int = 4
    selection_outline_thickness: int = 2
    heading_row_height: int = 20
    topic_row_height: int = 20
    topic_text_size: int = 10
    topic_line_thickness: int = 1
    vertical_spacing: int = 2
    topic_spacing: int = 2
    section_padding: int = 10

    @property
    def stack_width(self) -> int:
        return (
            self.appearance.question_box_size + self.stack_margin
            + self.attempt_max_x_steps * self.appearance.attempt_stack_offset
        )

    @property
    def cell_width(self) -> int:
        # Preserve the baseline's eight pixels around the stack.
        return self.stack_width + 8

    @property
    def horizontal_spacing(self) -> int:
        return self.appearance.horizontal_question_spacing


TRACKER_METRICS = TrackerMetrics()


def _scaled(value: float, scale: float) -> int:
    return max(1, round(value * scale))


class HierarchyHeadingLabel(QLabel):
    """Main-tracker heading; its grid span, never its text, determines width."""

    def __init__(self, text: str, appearance: TrackerAppearance, scale: float) -> None:
        super().__init__(text)
        self._leaf_span: tuple[QWidget, QWidget] | None = None
        metrics = TrackerMetrics(appearance)
        self.line_thickness = _scaled(appearance.hierarchy_line_thickness, scale)
        self._label_gap = _scaled(metrics.cell_margin, scale)
        font = self.font()
        font.setPixelSize(_scaled(appearance.hierarchy_heading_text_size, scale))
        font.setBold(False)
        self.setFont(font)
        self.setFixedHeight(_scaled(metrics.heading_row_height, scale))
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)

    def set_leaf_span(self, first: QWidget, last: QWidget) -> None:
        self._leaf_span = (first, last)
        self.update()

    def visible_span_rect(self) -> QRect:
        """Paint bounds in heading coordinates, excluding allocated grid slack."""
        if self._leaf_span is None:
            return QRect()
        first, last = self._leaf_span
        # Headings and question cells are siblings in the same grid container.
        left = first.geometry().left() - self.geometry().left()
        right = last.geometry().right() - self.geometry().left()
        return QRect(left, 0, right - left + 1, self.height()).intersected(self.contentsRect())

    def paintEvent(self, event) -> None:
        rect = self.visible_span_rect()
        if rect.isEmpty():
            return
        painter = QPainter(self)
        # Clip pen caps/thickness as well as text to the descendant edges.
        painter.setClipRect(rect)
        painter.setFont(self.font())
        colour = self.palette().color(self.foregroundRole())
        painter.setPen(colour)
        text = self.fontMetrics().elidedText(
            self.text(), Qt.TextElideMode.ElideRight, rect.width()
        )
        painter.drawText(rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)
        line_start = rect.left() + self.fontMetrics().horizontalAdvance(text) + self._label_gap
        if line_start < rect.right():
            painter.setPen(QPen(colour, self.line_thickness))
            painter.drawLine(line_start, rect.center().y(), rect.right(), rect.center().y())


class TopicBandLabel(QLabel):
    """Main-tracker annotation sized by its grid span, not its title."""

    def __init__(self, text: str, colour: str, scale: float) -> None:
        super().__init__(text)
        self.topic_colour = QColor(colour)
        self.contrast_colour = QColor(contrasting_text_colour(colour))
        self.line_thickness = _scaled(TRACKER_METRICS.topic_line_thickness, scale)
        self._label_gap = _scaled(TRACKER_METRICS.cell_margin, scale)
        font = self.font()
        font.setPixelSize(_scaled(TRACKER_METRICS.topic_text_size, scale))
        font.setBold(False)
        self.setFont(font)
        self.setToolTip(text)
        self.setFixedHeight(_scaled(TRACKER_METRICS.topic_row_height, scale))
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.contentsRect().adjusted(self.line_thickness, 0, -self.line_thickness, 0)
        metrics = self.fontMetrics()
        text = metrics.elidedText(
            self.text(), Qt.TextElideMode.ElideRight,
            max(0, rect.width() - self._label_gap * 2),
        )
        baseline = rect.top() + (rect.height() - metrics.height()) / 2 + metrics.ascent()
        path = QPainterPath()
        path.addText(rect.left(), baseline, self.font(), text)
        # Outline only the glyphs/line: keep the saved colour, without a filled card.
        painter.setPen(QPen(self.contrast_colour, self.line_thickness))
        painter.setBrush(self.topic_colour)
        painter.drawPath(path)
        line_start = rect.left() + metrics.horizontalAdvance(text) + self._label_gap
        if line_start < rect.right():
            painter.setPen(QPen(self.contrast_colour, self.line_thickness * 2))
            painter.drawLine(line_start, rect.center().y(), rect.right(), rect.center().y())
            painter.setPen(QPen(self.topic_colour, self.line_thickness))
            painter.drawLine(line_start, rect.center().y(), rect.right(), rect.center().y())


def attempt_tooltip(attempt: Attempt) -> str:
    style = ATTEMPT_STYLES[attempt.status]
    timestamp = attempt.timestamp.astimezone().strftime("%d %b %Y, %H:%M")
    note = attempt.note.strip() or "No note"
    return f"{style.label}\nDate: {timestamp}\nNote: {note}"


class AttemptStackWidget(QWidget):
    attempt_clicked = Signal(str)
    details_requested = Signal()

    def __init__(
        self,
        column: QuestionColumn,
        scale: float = 1.0,
        parent: QWidget | None = None,
        *,
        appearance: TrackerAppearance = DEFAULT_APPEARANCE,
        refined: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setObjectName(f"attemptStack-{column.question.id}")
        metrics = TrackerMetrics(appearance)
        history_steps = max(0, len(column.attempts) - 1)
        visible_steps = min(history_steps, metrics.attempt_max_x_steps) if refined else history_steps
        stack_height = (
            appearance.question_box_size + metrics.stack_margin * 2
            + visible_steps * appearance.attempt_stack_offset
        )
        self.setFixedSize(_scaled(metrics.stack_width, scale), _scaled(stack_height, scale))
        if not column.attempts:
            empty = QPushButton("", self)
            empty.setObjectName(f"unattemptedButton-{column.question.id}")
            empty.setToolTip("Unattempted")
            empty.setGeometry(
                _scaled(metrics.stack_margin, scale),
                _scaled(metrics.stack_margin, scale),
                _scaled(appearance.question_box_size, scale),
                _scaled(appearance.question_box_size, scale),
            )
            empty.setStyleSheet(
                "background: palette(base); border: 1px solid palette(text);"
                if refined else "background: white; border: 1px solid #999; color: #555;"
            )
            empty.clicked.connect(self.details_requested)
            return

        for index, attempt_view in enumerate(column.attempts):
            attempt = attempt_view.attempt
            style = ATTEMPT_STYLES[attempt.status]
            horizontal_offset = min(index, metrics.attempt_max_x_steps) * appearance.attempt_stack_offset
            vertical_offset = index * appearance.attempt_stack_offset
            if refined:
                # Keep all attempt widgets/history; compress long stacks into
                # the existing horizontal offset budget on both axes.
                horizontal_offset = vertical_offset = (
                    index * visible_steps * appearance.attempt_stack_offset / max(1, history_steps)
                )
            button = QPushButton("", self)
            button.setAccessibleName(style.label)
            button.setObjectName(f"attemptButton-{attempt.id}")
            button.setProperty("attemptId", attempt.id)
            button.setGeometry(
                _scaled(metrics.stack_margin + horizontal_offset, scale),
                _scaled(metrics.stack_margin + vertical_offset, scale),
                _scaled(appearance.question_box_size, scale),
                _scaled(appearance.question_box_size, scale),
            )
            border_width = 3 if attempt_view.is_latest else 1
            button.setStyleSheet(
                f"background: {style.colour}; color: {style.text_colour}; "
                f"border: {border_width}px solid #303030; border-radius: 3px;"
            )
            button.setToolTip(attempt_tooltip(attempt))
            button.clicked.connect(
                lambda checked=False, attempt_id=attempt.id: self.attempt_clicked.emit(
                    attempt_id
                )
            )
            button.raise_()


class QuestionCell(QFrame):
    selected = Signal(str)
    attempt_requested = Signal(str, str)
    details_requested = Signal(str)

    def __init__(
        self,
        column: QuestionColumn,
        scale: float = 1.0,
        parent: QWidget | None = None,
        *,
        appearance: TrackerAppearance = DEFAULT_APPEARANCE,
        refined: bool = False,
    ) -> None:
        super().__init__(parent)
        self.column = column
        self._refined = refined
        self._selection_width = _scaled(TRACKER_METRICS.selection_outline_thickness, scale)
        self.setObjectName(f"questionCell-{column.question.id}")
        self.setFixedWidth(_scaled(TrackerMetrics(appearance).cell_width, scale))
        if refined:
            self.setToolTip("Click around the attempt boxes to view all attempts.")
            self.setContextMenuPolicy(Qt.ContextMenuPolicy.ActionsContextMenu)
            view_attempts = QAction("View Attempts…", self)
            view_attempts.setObjectName("viewQuestionAttemptsAction")
            view_attempts.triggered.connect(
                lambda: self.details_requested.emit(column.question.id)
            )
            self.addAction(view_attempts)

        # Retain the programmatic history action without rendering a leaf label;
        # visible labels live exclusively in the two hierarchy heading rows.
        self.label_button = QPushButton(column.visible_label, self)
        self.label_button.setObjectName(
            f"questionLabelButton-{column.question.id}"
        )
        self.label_button.setToolTip(f"{column.visible_label} — Open attempt history")
        self.label_button.setStyleSheet(
            "QPushButton { color: #202124; background: transparent; border: none; "
            "font-weight: bold; padding: 2px; } "
            "QPushButton:hover { color: #0b57d0; text-decoration: underline; }"
        )
        self.label_button.clicked.connect(
            lambda: self.details_requested.emit(column.question.id)
        )
        self.label_button.hide()

        self.attempt_stack = AttemptStackWidget(column, scale, appearance=appearance, refined=refined)
        self.attempt_stack.attempt_clicked.connect(
            lambda attempt_id: self.attempt_requested.emit(
                column.question.id, attempt_id
            )
        )
        self.attempt_stack.details_requested.connect(
            lambda: self.details_requested.emit(column.question.id)
        )

        layout = QVBoxLayout(self)
        margin = _scaled(TRACKER_METRICS.cell_margin, scale)
        layout.setContentsMargins(margin, margin, margin, margin)
        layout.setSpacing(0)
        layout.addWidget(
            self.attempt_stack, alignment=Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter
        )
        self.set_selected(False)

    def set_selected(self, selected: bool) -> None:
        self.is_selected = selected
        border = "2px solid #1267c4" if selected and not self._refined else "2px solid transparent"
        self.setStyleSheet(
            f"QuestionCell {{ border: {border}; background: transparent; "
            "border-radius: 4px; }"
        )
        self.update()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if not self._refined or not self.is_selected:
            return
        bounds = QRect()
        for button in self.attempt_stack.findChildren(QPushButton):
            bounds = bounds.united(button.geometry())
        bounds.translate(self.attempt_stack.pos())
        padding = self._selection_width
        inset = (self._selection_width + 1) // 2
        bounds = bounds.adjusted(-padding, -padding, padding, padding).intersected(
            self.rect().adjusted(inset, inset, -inset, -inset)
        )
        painter = QPainter(self)
        pen = QPen(self.palette().color(QPalette.ColorRole.Highlight))
        pen.setWidth(self._selection_width)
        painter.setPen(pen)
        painter.drawRect(bounds)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.selected.emit(self.column.question.id)
            if self._refined:
                event.accept()
                self.details_requested.emit(self.column.question.id)
                return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.details_requested.emit(self.column.question.id)
        super().mouseDoubleClickEvent(event)


class AttemptEditorDialog(QDialog):
    def __init__(self, attempt: Attempt, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.attempt = attempt
        self.delete_requested = False
        self.setWindowTitle("Edit attempt")

        self.status_combo = QComboBox()
        self.status_combo.setObjectName("attemptStatusCombo")
        for status, style in ATTEMPT_STYLES.items():
            self.status_combo.addItem(style.label, status.value)
        self.status_combo.setCurrentIndex(
            self.status_combo.findData(attempt.status.value)
        )

        self.note_edit = QPlainTextEdit(attempt.note)
        self.note_edit.setObjectName("attemptNoteEdit")
        self.note_edit.setMinimumWidth(340)

        delete_button = QPushButton("Delete attempt")
        delete_button.setObjectName("deleteAttemptButton")
        delete_button.clicked.connect(self._request_delete)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Status"))
        layout.addWidget(self.status_combo)
        layout.addWidget(QLabel("Attempt note"))
        layout.addWidget(self.note_edit)
        row = QHBoxLayout()
        row.addWidget(delete_button)
        row.addStretch()
        row.addWidget(buttons)
        layout.addLayout(row)

    def selected_status(self) -> AttemptStatus:
        return AttemptStatus(self.status_combo.currentData())

    def note(self) -> str:
        return self.note_edit.toPlainText()

    def _request_delete(self) -> None:
        if (
            QMessageBox.question(
                self,
                "Delete attempt",
                "Delete this attempt? Other attempts will be preserved.",
            )
            == QMessageBox.StandardButton.Yes
        ):
            self.delete_requested = True
            self.accept()


class QuestionAttemptsDialog(QDialog):
    """Compact history manager for one question's attempts."""

    ATTEMPT_ID_ROLE = int(Qt.ItemDataRole.UserRole)

    def __init__(
        self,
        service: TrackingService,
        undo_stack: QUndoStack,
        question_id: str,
        visible_label: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.service = service
        self.undo_stack = undo_stack
        self.question_id = question_id
        self.setWindowTitle(f"Question {visible_label} — attempt history")
        self.resize(720, 380)

        self.attempts_table = QTableWidget(0, 4)
        self.attempts_table.setObjectName("attemptsTable")
        self.attempts_table.setHorizontalHeaderLabels(
            ("Status", "Date", "Note", "Latest")
        )
        self.attempts_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.attempts_table.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.attempts_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        header = self.attempts_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.attempts_table.itemSelectionChanged.connect(
            self._update_action_buttons
        )
        self.attempts_table.itemDoubleClicked.connect(
            lambda _item: self._edit_selected()
        )

        self.edit_button = QPushButton("Edit selected")
        self.edit_button.setObjectName("editSelectedAttemptButton")
        self.edit_button.clicked.connect(self._edit_selected)
        self.delete_button = QPushButton("Delete selected")
        self.delete_button.setObjectName("deleteSelectedAttemptsButton")
        self.delete_button.clicked.connect(self._delete_selected)
        self.select_all_button = QPushButton("Select All")
        self.select_all_button.setObjectName("selectAllAttemptsButton")
        self.select_all_button.clicked.connect(self.attempts_table.selectAll)
        close_button = QPushButton("Close")
        close_button.setObjectName("closeAttemptsButton")
        close_button.clicked.connect(self.accept)

        actions = QHBoxLayout()
        actions.addWidget(self.select_all_button)
        actions.addWidget(self.edit_button)
        actions.addWidget(self.delete_button)
        actions.addStretch()
        actions.addWidget(close_button)

        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel("Hover an attempt in the tracker for a quick summary, or manage several here.")
        )
        layout.addWidget(self.attempts_table)
        layout.addLayout(actions)

        undo_stack.indexChanged.connect(self._reload)
        self._reload()

    def _reload(self, index: int | None = None) -> None:
        del index
        attempts = self.service.attempts_for_question(self.question_id)
        newest = latest_attempt(attempts)
        self.attempts_table.setRowCount(len(attempts))
        for row, attempt in enumerate(reversed(attempts)):
            style = ATTEMPT_STYLES[attempt.status]
            status_item = QTableWidgetItem(style.label)
            status_item.setData(self.ATTEMPT_ID_ROLE, attempt.id)
            status_item.setForeground(QColor(style.text_colour))
            status_item.setBackground(QColor(style.colour))
            self.attempts_table.setItem(row, 0, status_item)
            self.attempts_table.setItem(
                row,
                1,
                QTableWidgetItem(
                    attempt.timestamp.astimezone().strftime("%d %b %Y, %H:%M")
                ),
            )
            self.attempts_table.setItem(row, 2, QTableWidgetItem(attempt.note if attempt.note.strip() else "No note"))
            self.attempts_table.setItem(
                row,
                3,
                QTableWidgetItem(
                    "Yes" if newest is not None and newest.id == attempt.id else ""
                ),
            )
        self._update_action_buttons()

    def _selected_attempts(self) -> tuple[Attempt, ...]:
        rows = sorted(
            index.row()
            for index in self.attempts_table.selectionModel().selectedRows()
        )
        attempts: list[Attempt] = []
        for row in rows:
            item = self.attempts_table.item(row, 0)
            if item is not None:
                attempts.append(
                    self.service.get_attempt(str(item.data(self.ATTEMPT_ID_ROLE)))
                )
        return tuple(attempts)

    def _update_action_buttons(self) -> None:
        selection_count = len(
            self.attempts_table.selectionModel().selectedRows()
        )
        self.edit_button.setEnabled(selection_count == 1)
        self.delete_button.setEnabled(selection_count > 0)
        self.select_all_button.setEnabled(self.attempts_table.rowCount() > 0)

    def _edit_selected(self) -> None:
        attempts = self._selected_attempts()
        if len(attempts) != 1:
            return
        attempt = attempts[0]
        editor = AttemptEditorDialog(attempt, self)
        if editor.exec() != QDialog.DialogCode.Accepted:
            return
        if editor.delete_requested:
            self.undo_stack.push(DeleteAttemptCommand(self.service, attempt))
        elif editor.selected_status() != attempt.status or editor.note() != attempt.note:
            self.undo_stack.push(
                EditAttemptCommand(
                    self.service,
                    attempt,
                    editor.selected_status(),
                    editor.note(),
                )
            )

    def _delete_selected(self) -> None:
        attempts = self._selected_attempts()
        if not attempts:
            return
        noun = "attempt" if len(attempts) == 1 else "attempts"
        if (
            QMessageBox.question(
                self,
                "Delete attempts",
                f"Delete {len(attempts)} selected {noun}?",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        self.undo_stack.push(DeleteAttemptsCommand(self.service, attempts))


class ManagedLibraryTree(QTreeWidget):
    reorder_requested = Signal(str, int)

    def __init__(self) -> None:
        super().__init__()
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)

    def startDrag(self, supported_actions) -> None:
        item = self.currentItem()
        if item is None or item.data(0, Qt.ItemDataRole.UserRole) is None:
            return
        # The service rebuilds the tree after a drop. QAbstractItemView's default
        # launcher would also remove a source row on return from a MoveAction.
        drag = QDrag(self)
        drag.setMimeData(self.model().mimeData(self.selectedIndexes()))
        drag.setPixmap(self.viewport().grab(self.visualItemRect(item)))
        try:
            drag.exec(Qt.DropAction.MoveAction)
        finally:
            self.setState(QAbstractItemView.State.NoState)
            self.viewport().update()
            drag.deleteLater()

    def move_relative(
        self, source: QTreeWidgetItem, target: QTreeWidgetItem, *, after: bool
    ) -> bool:
        """The drop handler's single path for same-category reordering."""
        if source is target or source.parent() is None or source.parent() is not target.parent():
            return False
        source_id = source.data(0, Qt.ItemDataRole.UserRole)
        target_id = target.data(0, Qt.ItemDataRole.UserRole)
        if source_id is None or target_id is None:
            return False
        parent = source.parent()
        source_index = parent.indexOfChild(source)
        position = parent.indexOfChild(target) + int(after)
        if source_index < position:
            position -= 1
        self.reorder_requested.emit(str(source_id), position)
        return True

    def dropEvent(self, event) -> None:
        source = self.currentItem()
        target = self.itemAt(event.position().toPoint())
        if event.source() is not self or source is None or target is None:
            event.ignore()
            return
        after = self.dropIndicatorPosition() == QAbstractItemView.DropIndicatorPosition.BelowItem
        if self.move_relative(source, target, after=after):
            event.setDropAction(Qt.DropAction.MoveAction)
            event.accept()
        else:
            event.ignore()


class TrackerScreen(QWidget):
    QUESTION_SET_ID_ROLE = int(Qt.ItemDataRole.UserRole)
    SETUP_STATE_ROLE = QUESTION_SET_ID_ROLE + 1
    YEAR_NAME_ROLE = SETUP_STATE_ROLE + 1
    MODULE_NAME_ROLE = YEAR_NAME_ROLE + 1

    def __init__(
        self,
        service: TrackingService,
        library_service: LibraryService,
        undo_stack: QUndoStack,
        *,
        on_back: Callable[[], None] | None = None,
        on_open_marking: Callable[[str, str | None], None] | None = None,
        initial_question_set_id: str | None = None,
        preferences: UiPreferences | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.service = service
        self.library_service = library_service
        self.undo_stack = undo_stack
        self.on_back = on_back
        self.on_open_marking = on_open_marking
        self.current_question_set_id: str | None = None
        self.selected_question_id: str | None = None
        self.snapshot: TrackerSnapshot | None = None
        self.module_snapshot: ModuleTrackerSnapshot | None = None
        self.question_cells: dict[str, QuestionCell] = {}
        self.hierarchy_headings: dict[str, HierarchyHeadingLabel] = {}
        self.question_set_by_question: dict[str, str] = {}
        self.status_buttons: dict[AttemptStatus, QPushButton] = {}
        self._shortcuts: list[QShortcut] = []
        self.preferences = preferences or UiPreferences()
        self.zoom_percent = self.preferences.tracker_zoom()
        self.appearance = load_tracker_appearance()
        self._scroll_revision = 0
        self._attempt_history_open = False
        self._attempt_history_refresh_pending = False
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        back_button = QPushButton("Back to Home")
        back_button.setObjectName("backToHomeButton")
        back_button.setEnabled(on_back is not None)
        if on_back is not None:
            back_button.clicked.connect(on_back)

        heading = QLabel("Question Tracker")
        heading.setStyleSheet("font-size: 20px; font-weight: bold;")
        self.undo_button = QPushButton("Undo")
        self.undo_button.setObjectName("undoButton")
        self.undo_button.clicked.connect(undo_stack.undo)
        self.undo_button.setEnabled(undo_stack.canUndo())
        self.redo_button = QPushButton("Redo")
        self.redo_button.setObjectName("redoButton")
        self.redo_button.clicked.connect(undo_stack.redo)
        self.redo_button.setEnabled(undo_stack.canRedo())
        undo_stack.canUndoChanged.connect(self.undo_button.setEnabled)
        undo_stack.canRedoChanged.connect(self.redo_button.setEnabled)
        undo_stack.indexChanged.connect(self._undo_stack_changed)

        header = QHBoxLayout()
        header.addWidget(back_button)
        header.addWidget(heading)
        header.addStretch()
        self.appearance_button = QPushButton("Appearance…")
        self.appearance_button.setObjectName("trackerAppearanceButton")
        self.appearance_button.clicked.connect(self._open_appearance)
        header.addWidget(self.appearance_button)

        selected_controls = QHBoxLayout()
        for shortcut, status in STATUS_SHORTCUTS.items():
            style = ATTEMPT_STYLES[status]
            button = QPushButton(f"{shortcut}  {style.label}")
            button.setObjectName(f"mark{status.value.title().replace('_', '')}Button")
            button.setStyleSheet(
                f"background: {style.colour}; color: {style.text_colour}; "
                f"font-weight: bold; padding: {TRACKER_METRICS.cell_margin}px;"
            )
            button.clicked.connect(
                lambda checked=False, selected_status=status: self.mark_selected(
                    selected_status
                )
            )
            self.status_buttons[status] = button
            selected_controls.addWidget(button)

        self.bookmark_button = BookmarkButton()
        self.bookmark_button.setObjectName("selectedBookmarkButton")
        self.bookmark_button.clicked.connect(
            lambda: self._toggle_bookmark(self.selected_question_id)
            if self.selected_question_id is not None else None
        )
        self.note_button = QPushButton("Note")
        self.note_button.setObjectName("selectedQuestionNoteButton")
        self.note_button.clicked.connect(
            lambda: self._edit_question_note(self.selected_question_id)
            if self.selected_question_id is not None else None
        )
        selected_controls.addWidget(QLabel("Selected question:"))
        selected_controls.addWidget(self.bookmark_button)
        selected_controls.addWidget(self.note_button)
        selected_controls.addStretch()
        selected_controls.addWidget(self.undo_button)
        selected_controls.addWidget(self.redo_button)
        self.navigation_tree = ManagedLibraryTree()
        self.navigation_tree.reorder_requested.connect(self._reorder_sheet)
        self.navigation_tree.setObjectName("trackerNavigationTree")
        self.navigation_tree.setHeaderLabel("Managed library")
        self.navigation_tree.currentItemChanged.connect(
            self._navigation_item_selected
        )
        expand_button = QPushButton("Expand All")
        expand_button.setObjectName("expandAllButton")
        expand_button.clicked.connect(self.navigation_tree.expandAll)
        collapse_button = QPushButton("Collapse All")
        collapse_button.setObjectName("collapseAllButton")
        collapse_button.clicked.connect(self.navigation_tree.collapseAll)
        nav_controls = QHBoxLayout()
        nav_controls.addWidget(expand_button)
        nav_controls.addWidget(collapse_button)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(self.navigation_tree)
        left_layout.addLayout(nav_controls)

        self.title_label = QLabel("Select a ready question set")
        self.title_label.setObjectName("trackerTitleLabel")
        self.title_label.setStyleSheet("font-size: 18px; font-weight: bold;")
        self.summary_label = QLabel("")
        self.summary_label.setObjectName("progressSummaryLabel")
        self.edit_questions_button = QPushButton("Edit Questions")
        self.edit_questions_button.setObjectName("editQuestionsButton")
        self.edit_questions_button.clicked.connect(self._edit_questions)
        self.open_marking_button = QPushButton("Open Marking")
        self.open_marking_button.setObjectName("openMarkingButton")
        self.open_marking_button.setEnabled(False)
        self.open_marking_button.clicked.connect(self._open_marking)
        self.zoom_out_button = QPushButton("−")
        self.zoom_out_button.setObjectName("zoomOutButton")
        self.zoom_out_button.setToolTip("Zoom out")
        self.zoom_out_button.clicked.connect(lambda: self.change_zoom(-ZOOM_STEP))
        self.zoom_label = QLabel(f"{self.zoom_percent}%")
        self.zoom_label.setObjectName("zoomLabel")
        self.zoom_label.setMinimumWidth(44)
        self.zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.zoom_in_button = QPushButton("+")
        self.zoom_in_button.setObjectName("zoomInButton")
        self.zoom_in_button.setToolTip("Zoom in")
        self.zoom_in_button.clicked.connect(lambda: self.change_zoom(ZOOM_STEP))
        self.zoom_out_button.setEnabled(self.zoom_percent > MIN_ZOOM_PERCENT)
        self.zoom_in_button.setEnabled(self.zoom_percent < MAX_ZOOM_PERCENT)
        tracker_header = QHBoxLayout()
        tracker_header.addWidget(self.title_label)
        tracker_header.addStretch()
        tracker_header.addWidget(self.zoom_out_button)
        tracker_header.addWidget(self.zoom_label)
        tracker_header.addWidget(self.zoom_in_button)
        tracker_header.addWidget(self.edit_questions_button)
        tracker_header.addWidget(self.open_marking_button)

        self.tracker_scroll = QScrollArea()
        self.tracker_scroll.setObjectName("trackerScrollArea")
        self.tracker_scroll.setWidgetResizable(True)
        self.tracker_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.empty_label = QLabel("Select a ready question set from the library tree.")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.tracker_scroll.setWidget(self.empty_label)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addLayout(tracker_header)
        right_layout.addWidget(self.summary_label)
        right_layout.addWidget(self.tracker_scroll)

        splitter = QSplitter()
        splitter.setObjectName("trackerSplitter")
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setSizes([270, 930])

        layout = QVBoxLayout(self)
        layout.addLayout(header)
        layout.addLayout(selected_controls)
        layout.addWidget(splitter)

        self._install_shortcuts()
        install_bookmark_menu(self.bookmark_button, service, undo_stack,
                              lambda: self.selected_question_id, self.refresh_current)
        self._set_marking_enabled(False)
        self._populate_navigation(initial_question_set_id)
        if initial_question_set_id is not None:
            self.load_question_set(initial_question_set_id)

    def load_question_set(self, question_set_id: str) -> None:
        question_set = self.service.get_question_set(question_set_id)
        if question_set.setup_state is SetupState.NEEDS_SETUP:
            self._open_setup(question_set_id)
            return
        if self.module_snapshot is not None:
            existing = next((s for s in self.module_snapshot.question_sets
                             if s.question_set.id == question_set_id), None)
            if existing is not None and existing.columns:
                self.select_question(existing.columns[0].question.id, reveal=True)
                return
        changed_set = question_set_id != self.current_question_set_id
        self.current_question_set_id = question_set_id
        if changed_set:
            self.selected_question_id = None
        self.refresh_current(preferred_question_set_id=question_set_id)
        if self.selected_question_id is not None:
            self.select_question(self.selected_question_id, reveal=True)

    def refresh_current(
        self, *, preferred_question_set_id: str | None = None
    ) -> None:
        if self.current_question_set_id is None:
            return
        module_snapshot = build_module_tracker_snapshot(
            self.service,
            self.library_service,
            self.current_question_set_id,
        )
        self.module_snapshot = module_snapshot
        leaf_ids = [column.question.id for column in module_snapshot.columns]
        if self.selected_question_id not in leaf_ids:
            preferred_id = preferred_question_set_id or self.current_question_set_id
            preferred_snapshot = next(
                (
                    snapshot
                    for snapshot in module_snapshot.question_sets
                    if snapshot.question_set.id == preferred_id
                ),
                None,
            )
            preferred_columns = (
                preferred_snapshot.columns if preferred_snapshot is not None else ()
            )
            self.selected_question_id = (
                preferred_columns[0].question.id
                if preferred_columns
                else (leaf_ids[0] if leaf_ids else None)
            )
        self.snapshot = self._snapshot_for_question(self.selected_question_id)
        if self.snapshot is None and module_snapshot.question_sets:
            self.snapshot = module_snapshot.question_sets[0]
        self._render_snapshot(module_snapshot)

    def select_question(self, question_id: str, reveal: bool = False) -> None:
        if question_id not in self.question_cells:
            return
        self.selected_question_id = question_id
        question_set_id = self.question_set_by_question.get(question_id)
        if question_set_id is not None:
            self.current_question_set_id = question_set_id
        self.snapshot = self._snapshot_for_question(question_id)
        for cell_id, cell in self.question_cells.items():
            cell.set_selected(cell_id == question_id)
        question = self.question_cells[question_id].column.question
        self.bookmark_button.refresh_state()
        self.note_button.setText("Note ●" if question.note else "Note")
        self.note_button.setToolTip(question.note or "Edit selected question note")
        if reveal:
            self._scroll_revision += 1
            revision = self._scroll_revision

            def reveal_selected() -> None:
                if revision == self._scroll_revision and question_id in self.question_cells:
                    self.tracker_scroll.ensureWidgetVisible(
                        self.question_cells[question_id], 24, 24
                    )

            reveal_selected()
            QTimer.singleShot(0, self, reveal_selected)
        self.setFocus(Qt.FocusReason.OtherFocusReason)

    def navigate(self, offset: int) -> None:
        if self.snapshot is None:
            return
        target = adjacent_leaf_id(self.snapshot, self.selected_question_id, offset)
        if target is not None:
            self.select_question(target, reveal=True)

    def navigate_vertical(self, offset: int) -> None:
        if self.module_snapshot is None or self.snapshot is None:
            return
        sheets = [s for s in self.module_snapshot.question_sets if s.columns]
        current = next((i for i, s in enumerate(sheets)
                        if s.question_set.id == self.current_question_set_id), None)
        if current is None or not 0 <= current + offset < len(sheets):
            return
        index = next((i for i, c in enumerate(self.snapshot.columns)
                      if c.question.id == self.selected_question_id), 0)
        destination = sheets[current + offset]
        self.select_question(
            destination.columns[min(index, len(destination.columns) - 1)].question.id,
            reveal=True,
        )

    def change_zoom(self, offset: int) -> None:
        new_zoom = max(
            MIN_ZOOM_PERCENT,
            min(MAX_ZOOM_PERCENT, self.zoom_percent + offset),
        )
        if new_zoom == self.zoom_percent:
            return
        self.zoom_percent = new_zoom
        self.preferences.save_tracker_zoom(new_zoom)
        self.zoom_label.setText(f"{new_zoom}%")
        self.zoom_out_button.setEnabled(new_zoom > MIN_ZOOM_PERCENT)
        self.zoom_in_button.setEnabled(new_zoom < MAX_ZOOM_PERCENT)
        if self.module_snapshot is not None:
            self._render_snapshot(self.module_snapshot)

    def mark_selected(self, status: AttemptStatus, *, advance: bool = False) -> None:
        if self.selected_question_id is None:
            return
        self.undo_stack.push(
            AddAttemptCommand(self.service, self.selected_question_id, status)
        )
        if advance:
            self.navigate(1)

    def keyPressEvent(self, event) -> None:
        if text_entry_has_focus():
            super().keyPressEvent(event)
            return
        if event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            if event.key() == Qt.Key.Key_Z:
                self.undo_stack.undo()
                event.accept()
                return
            if event.key() == Qt.Key.Key_Y:
                self.undo_stack.redo()
                event.accept()
                return
        if event.modifiers() == Qt.KeyboardModifier.NoModifier:
            if event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down):
                self.navigate_vertical(-1 if event.key() == Qt.Key.Key_Up else 1)
                event.accept()
                return
            if event.key() == Qt.Key.Key_Left:
                self.navigate(-1)
                event.accept()
                return
            if event.key() == Qt.Key.Key_Right:
                self.navigate(1)
                event.accept()
                return
            status = STATUS_SHORTCUTS.get(event.text())
            if status is not None:
                self.mark_selected(status, advance=True)
                event.accept()
                return
        super().keyPressEvent(event)

    def _render_snapshot(self, snapshot: ModuleTrackerSnapshot) -> None:
        horizontal_position = self.tracker_scroll.horizontalScrollBar().value()
        vertical_position = self.tracker_scroll.verticalScrollBar().value()
        if snapshot.module is None:
            title = (
                snapshot.question_sets[0].question_set.display_name
                if snapshot.question_sets
                else "Question Tracker"
            )
        else:
            title = f"{snapshot.module.module_name} — All question sets"
        self.title_label.setText(title)
        total = len(snapshot.columns)
        complete_count = sum(
            item.progress.complete for item in snapshot.question_sets
        )
        mastered_count = sum(
            item.progress.mastered for item in snapshot.question_sets
        )
        set_count = len(snapshot.question_sets)
        self.summary_label.setText(
            f"Attempted: {snapshot.attempted_count} / {total}    "
            f"Complete sets: {complete_count} / {set_count}    "
            f"Mastered sets: {mastered_count} / {set_count}"
        )
        self.edit_questions_button.setEnabled(bool(snapshot.question_sets))
        self._set_marking_enabled(bool(snapshot.columns))

        content = QWidget()
        content.setObjectName("trackerColumnsWidget")
        grid = QGridLayout(content)
        grid.setAlignment(Qt.AlignmentFlag.AlignTop)
        scale = self.zoom_percent / 100
        metrics = TrackerMetrics(self.appearance)
        grid.setHorizontalSpacing(max(0, round(metrics.horizontal_spacing * scale)))
        grid.setVerticalSpacing(_scaled(metrics.vertical_spacing, scale))
        widest_set = max(
            (len(item.columns) for item in snapshot.question_sets), default=1
        )
        # Let section headers use the available width without stretching leaf columns.
        grid.setColumnStretch(widest_set, 1)
        row = 0
        self.question_cells = {}
        self.hierarchy_headings = {}
        self.question_set_by_question = {}
        for question_set_snapshot in snapshot.question_sets:
            grid.addWidget(
                self._section_header(question_set_snapshot),
                row,
                0,
                1,
                widest_set + 1,
            )
            row += 1

            label_band_row = row
            for span in question_set_snapshot.topic_spans:
                label = TopicBandLabel(span.topic.title, span.topic.colour, scale)
                label.setObjectName(
                    f"topicSpan-{span.topic.id}-{span.start_column}-{span.end_column}"
                )
                grid.addWidget(
                    label,
                    label_band_row,
                    span.start_column,
                    1,
                    span.end_column - span.start_column + 1,
                )
                grid.setRowMinimumHeight(
                    label_band_row,
                    _scaled(metrics.topic_row_height + metrics.topic_spacing, scale),
                )
            row += int(bool(question_set_snapshot.topic_spans))

            heading_start_row = row
            for heading_row in range(question_set_snapshot.heading_row_count):
                grid.setRowMinimumHeight(
                    heading_start_row + heading_row,
                    _scaled(metrics.heading_row_height, scale),
                )
            for span in question_set_snapshot.hierarchy_headings:
                label = HierarchyHeadingLabel(span.question.local_label, self.appearance, scale)
                label.setObjectName(
                    f"hierarchyHeading-{span.question.id}"
                    if span.is_leaf
                    else f"parentSpan-{span.question.id}"
                )
                label.setToolTip(span.visible_label)
                grid.addWidget(
                    label,
                    heading_start_row + span.depth,
                    span.start_column,
                    1,
                    span.end_column - span.start_column + 1,
                    Qt.AlignmentFlag.AlignTop,
                )
                self.hierarchy_headings[span.question.id] = label

            attempt_row = heading_start_row + question_set_snapshot.heading_row_count
            for index, column in enumerate(question_set_snapshot.columns):
                cell = QuestionCell(column, scale, appearance=self.appearance, refined=True)
                cell.selected.connect(self.select_question)
                cell.attempt_requested.connect(self._attempt_requested)
                cell.details_requested.connect(self._open_question_attempts)
                grid.addWidget(
                    cell,
                    attempt_row,
                    index,
                    1,
                    1,
                    Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter,
                )
                grid.setColumnMinimumWidth(index, _scaled(metrics.cell_width, scale))
                self.question_cells[column.question.id] = cell
                self.question_set_by_question[column.question.id] = (
                    question_set_snapshot.question_set.id
                )
            for span in question_set_snapshot.hierarchy_headings:
                self.hierarchy_headings[span.question.id].set_leaf_span(
                    self.question_cells[question_set_snapshot.columns[span.start_column].question.id],
                    self.question_cells[question_set_snapshot.columns[span.end_column].question.id],
                )
            if not question_set_snapshot.columns:
                empty = QLabel("This ready question set has no leaf questions.")
                empty.setStyleSheet("color: #202124;")
                grid.addWidget(empty, attempt_row, 0)
            row = attempt_row + 2
            grid.setRowMinimumHeight(row - 1, _scaled(metrics.section_padding, scale))

        content.setMinimumWidth(
            _scaled(
                max(1, widest_set)
                * (metrics.cell_width + metrics.horizontal_spacing),
                scale,
            )
        )
        self.tracker_scroll.setWidget(content)
        if self.selected_question_id is not None:
            self.select_question(self.selected_question_id)
        self._restore_scroll_position(
            horizontal_position, vertical_position
        )

    def _open_appearance(self) -> None:
        dialog = TrackerAppearanceDialog(self)
        dialog.appearance_applied.connect(self._apply_appearance)
        try:
            dialog.exec()
        finally:
            dialog.deleteLater()

    def _apply_appearance(self, appearance: TrackerAppearance) -> None:
        self.appearance = appearance
        if self.module_snapshot is not None:
            self._render_snapshot(self.module_snapshot)

    def _section_header(self, snapshot: TrackerSnapshot) -> QWidget:
        question_set = snapshot.question_set
        header = QWidget()
        header.setObjectName(f"questionSetSection-{question_set.id}")
        layout = QVBoxLayout(header)
        layout.setContentsMargins(0, TRACKER_METRICS.cell_margin, 0, TRACKER_METRICS.cell_margin)
        layout.setSpacing(TRACKER_METRICS.vertical_spacing)
        title = QLabel(question_set.display_name)
        title.setObjectName(f"questionSetHeading-{question_set.id}")
        title.setToolTip(question_set.display_name + "\n" + question_set.general_note)
        title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        font = title.font()
        font.setBold(True)
        title.setFont(font)
        progress = QLabel(
            f"Attempted {snapshot.attempted_count}/{len(snapshot.columns)}   "
            f"{'Complete' if snapshot.progress.complete else 'Incomplete'}   "
            f"{'Mastered' if snapshot.progress.mastered else 'Not Mastered'}"
        )
        progress.setObjectName(f"questionSetProgress-{question_set.id}")
        title_row = QHBoxLayout()
        title_row.addWidget(title, 1)
        title_row.addWidget(progress)
        layout.addLayout(title_row)
        actions = QHBoxLayout()
        edit = QPushButton("Edit Questions")
        edit.setObjectName(f"sectionEditQuestions-{question_set.id}")
        edit.clicked.connect(
            lambda: QTimer.singleShot(0, self, lambda: self._edit_question_set(question_set.id))
        )
        marking = QPushButton("Open Marking")
        marking.setObjectName(f"sectionOpenMarking-{question_set.id}")
        marking.setEnabled(self.on_open_marking is not None and bool(snapshot.columns))
        marking.clicked.connect(
            lambda: QTimer.singleShot(0, self, lambda: self._open_section_marking(question_set.id))
        )
        actions.addWidget(edit)
        actions.addWidget(marking)
        actions.addStretch()
        layout.addLayout(actions)
        return header

    def _open_section_marking(self, question_set_id: str) -> None:
        if self.on_open_marking is None or self.module_snapshot is None:
            return
        snapshot = next((item for item in self.module_snapshot.question_sets
                         if item.question_set.id == question_set_id), None)
        if snapshot is None or not snapshot.columns:
            return
        ids = {column.question.id for column in snapshot.columns}
        selected = self.selected_question_id if self.selected_question_id in ids else snapshot.columns[0].question.id
        self.on_open_marking(question_set_id, selected)

    def _restore_scroll_position(
        self, horizontal_position: int, vertical_position: int
    ) -> None:
        self._scroll_revision += 1
        revision = self._scroll_revision

        def restore() -> None:
            if revision != self._scroll_revision:
                return
            horizontal = self.tracker_scroll.horizontalScrollBar()
            vertical = self.tracker_scroll.verticalScrollBar()
            horizontal.setValue(min(horizontal_position, horizontal.maximum()))
            vertical.setValue(min(vertical_position, vertical.maximum()))

        restore()
        QTimer.singleShot(0, self, restore)

    def _snapshot_for_question(
        self, question_id: str | None
    ) -> TrackerSnapshot | None:
        if question_id is None or self.module_snapshot is None:
            return None
        return next(
            (
                snapshot
                for snapshot in self.module_snapshot.question_sets
                if any(
                    column.question.id == question_id
                    for column in snapshot.columns
                )
            ),
            None,
        )

    def _set_marking_enabled(self, enabled: bool) -> None:
        for button in self.status_buttons.values():
            button.setEnabled(enabled)
        self.bookmark_button.setEnabled(enabled)
        self.note_button.setEnabled(enabled)
        self.open_marking_button.setEnabled(
            enabled and self.on_open_marking is not None
        )

    def _open_marking(self) -> None:
        if self.current_question_set_id is None or self.on_open_marking is None:
            return
        self.on_open_marking(
            self.current_question_set_id, self.selected_question_id
        )

    def _reorder_sheet(self, question_set_id: str, position: int) -> None:
        self.library_service.reorder_question_set(question_set_id, position)
        self._populate_navigation(self.current_question_set_id)
        self.refresh_current()

    def _populate_navigation(self, selected_id: str | None = None) -> None:
        records: dict[
            str,
            dict[
                str,
                dict[DocumentType, list[tuple[QuestionSet, ManagedFile]]],
            ],
        ] = defaultdict(
            lambda: defaultdict(lambda: defaultdict(list))
        )
        for managed_file in self.library_service.list_files():
            if managed_file.document_type is DocumentType.SOLUTION:
                continue
            parts = managed_file.relative_path.parts
            if len(parts) < 4:
                continue
            question_sets = self.library_service.question_sets_for_file(
                managed_file.id, FileRole.MAIN
            )
            for question_set in question_sets:
                records[parts[0]][parts[1]][managed_file.document_type].append(
                    (question_set, managed_file)
                )

        self.navigation_tree.blockSignals(True)
        self.navigation_tree.clear()
        selected_item: QTreeWidgetItem | None = None
        for year_name in sorted(records, key=str.casefold):
            year_item = QTreeWidgetItem([year_name])
            self.navigation_tree.addTopLevelItem(year_item)
            for module_name in sorted(records[year_name], key=str.casefold):
                module_item = QTreeWidgetItem([module_name])
                module_item.setData(0, self.YEAR_NAME_ROLE, year_name)
                module_item.setData(0, self.MODULE_NAME_ROLE, module_name)
                year_item.addChild(module_item)
                for document_type, category_name in (
                    (DocumentType.PROBLEM_SHEET, "Problem Sheets"),
                    (DocumentType.PAST_PAPER, "Past Papers"),
                ):
                    category_records = records[year_name][module_name].get(
                        document_type, []
                    )
                    if not category_records:
                        continue
                    category_item = QTreeWidgetItem([category_name])
                    category_item.setData(0, self.YEAR_NAME_ROLE, year_name)
                    category_item.setData(0, self.MODULE_NAME_ROLE, module_name)
                    module_item.addChild(category_item)
                    for question_set, managed_file in sorted(
                        category_records,
                        key=lambda pair: (
                            pair[0].display_order is None,
                            pair[0].display_order or 0,
                            pair[0].display_name.casefold(),
                            pair[0].id,
                        ),
                    ):
                        indicators: list[str] = []
                        if managed_file.availability is FileAvailability.MISSING:
                            indicators.append("Missing")
                        if question_set.setup_state is SetupState.NEEDS_SETUP:
                            indicators.append("Needs setup")
                        suffix = f" [{', '.join(indicators)}]" if indicators else ""
                        item = QTreeWidgetItem(
                            [f"{question_set.display_name}{suffix}"]
                        )
                        item.setData(
                            0, self.QUESTION_SET_ID_ROLE, question_set.id
                        )
                        item.setData(
                            0, self.SETUP_STATE_ROLE, question_set.setup_state.value
                        )
                        category_item.addChild(item)
                        if question_set.id == selected_id:
                            selected_item = item
        self.navigation_tree.expandAll()
        if selected_item is not None:
            self.navigation_tree.setCurrentItem(selected_item)
        self.navigation_tree.blockSignals(False)

    def _navigation_item_selected(
        self, current: QTreeWidgetItem | None, previous: QTreeWidgetItem | None
    ) -> None:
        del previous
        if current is None:
            return
        question_set_id = current.data(0, self.QUESTION_SET_ID_ROLE)
        if question_set_id is None:
            ready_id = self._first_ready_question_set_id(current)
            if ready_id is not None:
                self.load_question_set(ready_id)
            return
        state = SetupState(current.data(0, self.SETUP_STATE_ROLE))
        if state is SetupState.NEEDS_SETUP:
            self._open_setup(str(question_set_id))
        else:
            self.load_question_set(str(question_set_id))

    def _first_ready_question_set_id(
        self, item: QTreeWidgetItem
    ) -> str | None:
        question_set_id = item.data(0, self.QUESTION_SET_ID_ROLE)
        setup_state = item.data(0, self.SETUP_STATE_ROLE)
        if (
            question_set_id is not None
            and setup_state == SetupState.READY.value
        ):
            return str(question_set_id)
        for index in range(item.childCount()):
            found = self._first_ready_question_set_id(item.child(index))
            if found is not None:
                return found
        return None

    def _open_setup(self, question_set_id: str) -> None:
        editor = QuestionEditorDialog(self.service, question_set_id, self)
        editor.exec()
        self.undo_stack.clear()
        updated = self.service.get_question_set(question_set_id)
        if updated.setup_state is SetupState.READY:
            self._populate_navigation(question_set_id)
            self.load_question_set(question_set_id)
            return
        self._populate_navigation(self.current_question_set_id)
        if self.current_question_set_id is None:
            self.title_label.setText("Setup not finished")
            self.summary_label.setText("")

    def _edit_questions(self) -> None:
        if self.current_question_set_id is None:
            return
        self._edit_question_set(self.current_question_set_id)

    def _edit_question_set(self, question_set_id: str) -> None:
        editor = QuestionEditorDialog(
            self.service, question_set_id, self
        )
        editor.exec()
        self.undo_stack.clear()
        self._populate_navigation(self.current_question_set_id)
        self.refresh_current()

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
        column = next(
            (
                column
                for snapshot in (
                    self.module_snapshot.question_sets
                    if self.module_snapshot is not None
                    else ()
                )
                for column in snapshot.columns
                if column.question.id == question_id
            ),
            None,
        )
        if column is None:
            return
        editor = QuestionAttemptsDialog(
            self.service,
            self.undo_stack,
            question_id,
            column.visible_label,
            self,
        )
        self._attempt_history_open = True
        try:
            editor.exec()
        finally:
            self._attempt_history_open = False
            editor.deleteLater()
            if self._attempt_history_refresh_pending:
                self._attempt_history_refresh_pending = False
                # Return through the initiating cell's input event before
                # replacing the scroll content and destroying that cell.
                QTimer.singleShot(0, self, self.refresh_current)

    def _toggle_bookmark(self, question_id: str) -> None:
        self.select_question(question_id)
        toggle_default_bookmark(self.service, self.undo_stack, question_id, self)

    def _edit_question_note(self, question_id: str) -> None:
        self.select_question(question_id)
        question = self.service.get_question(question_id)
        note, accepted = QInputDialog.getMultiLineText(
            self,
            "Question note",
            "Note",
            question.note,
        )
        if accepted and note != question.note:
            self.undo_stack.push(
                EditQuestionNoteCommand(
                    self.service, question_id, question.note, note
                )
            )

    def _install_shortcuts(self) -> None:
        actions: dict[str, Callable[[], None]] = {
            "Left": lambda: self.navigate(-1),
            "Right": lambda: self.navigate(1),
            "Up": lambda: self.navigate_vertical(-1),
            "Down": lambda: self.navigate_vertical(1),
            "Ctrl+Z": self.undo_stack.undo,
            "Ctrl+Y": self.undo_stack.redo,
        }
        actions.update(
            {
                key: lambda status=status: self.mark_selected(status, advance=True)
                for key, status in STATUS_SHORTCUTS.items()
            }
        )
        for key, action in actions.items():
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(
                lambda selected_action=action: self._route_shortcut(
                    selected_action
                )
            )
            self._shortcuts.append(shortcut)

    def _route_shortcut(self, action: Callable[[], None]) -> None:
        if text_entry_has_focus():
            return
        action()

    def _undo_stack_changed(self, index: int) -> None:
        del index
        if self._attempt_history_open:
            self._attempt_history_refresh_pending = True
            return
        self.refresh_current()


def text_entry_has_focus() -> bool:
    widget = QApplication.focusWidget()
    while widget is not None:
        if isinstance(
            widget,
            (QLineEdit, QPlainTextEdit, QTextEdit, QAbstractSpinBox),
        ):
            return True
        if isinstance(widget, QComboBox) and widget.isEditable():
            return True
        widget = widget.parentWidget()
    return False
