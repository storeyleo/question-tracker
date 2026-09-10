from __future__ import annotations

from pathlib import Path
from datetime import UTC, datetime, timedelta

import pytest
from PySide6.QtCore import QItemSelectionModel, QPoint, Qt
from PySide6.QtGui import QUndoStack
from PySide6.QtWidgets import (
    QDialog,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
)

from question_tracker.application.library_service import LibraryService
from question_tracker.application.tracking_service import TrackingService
from question_tracker.app import MainWindow
from question_tracker.domain.tracking import AttemptStatus, QuestionSetKind, SetupState
from question_tracker.paths import ApplicationPaths
from question_tracker.ui.home import HomeScreen
from question_tracker.ui.question_editor import QuestionEditorDialog
from question_tracker.ui.colours import contrasting_text_colour
from question_tracker.ui.tracker import (
    AttemptEditorDialog,
    QuestionAttemptsDialog,
    TrackerScreen,
)


def _ready_set(service: TrackingService, question_count: int = 3):
    question_set = service.create_question_set(
        QuestionSetKind.PROBLEM_SHEET,
        "Ready sheet",
        setup_state=SetupState.READY,
    )
    questions = tuple(
        service.create_question(question_set.id, str(index + 1))
        for index in range(question_count)
    )
    return question_set, questions


def _screen(
    qtbot,
    service: TrackingService,
    library_service: LibraryService,
    question_set_id: str,
) -> TrackerScreen:
    screen = TrackerScreen(
        service,
        library_service,
        QUndoStack(),
        initial_question_set_id=question_set_id,
    )
    qtbot.addWidget(screen)
    screen.resize(900, 600)
    screen.show()
    screen.activateWindow()
    screen.setFocus()
    return screen


def _write_pdf(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"%PDF-1.4\n%%EOF\n")


def test_ready_tracker_loads_and_selects_first_leaf(
    qtbot, tracking_service: TrackingService, library_service: LibraryService
) -> None:
    question_set, questions = _ready_set(tracking_service)

    screen = _screen(qtbot, tracking_service, library_service, question_set.id)

    assert screen.current_question_set_id == question_set.id
    assert screen.selected_question_id == questions[0].id
    assert list(screen.question_cells) == [question.id for question in questions]
    assert screen.question_cells[questions[0].id].is_selected


def test_keyboard_and_click_navigation_stays_within_leaf_boundaries(
    qtbot, monkeypatch, tracking_service: TrackingService, library_service: LibraryService
) -> None:
    monkeypatch.setattr(QuestionAttemptsDialog, "exec", lambda dialog: QDialog.DialogCode.Rejected)
    question_set, questions = _ready_set(tracking_service)
    screen = _screen(qtbot, tracking_service, library_service, question_set.id)

    qtbot.keyClick(screen, Qt.Key.Key_Left)
    assert screen.selected_question_id == questions[0].id
    qtbot.keyClick(screen, Qt.Key.Key_Right)
    assert screen.selected_question_id == questions[1].id
    qtbot.keyClick(screen, Qt.Key.Key_Right)
    qtbot.keyClick(screen, Qt.Key.Key_Right)
    assert screen.selected_question_id == questions[2].id

    qtbot.mouseClick(
        screen.question_cells[questions[0].id], Qt.MouseButton.LeftButton
    )
    assert screen.selected_question_id == questions[0].id


def test_keyboard_marking_creates_each_status_and_updates_display(
    qtbot, tracking_service: TrackingService, library_service: LibraryService
) -> None:
    question_set, questions = _ready_set(tracking_service, 1)
    screen = _screen(qtbot, tracking_service, library_service, question_set.id)

    for key in (Qt.Key.Key_1, Qt.Key.Key_2, Qt.Key.Key_3, Qt.Key.Key_4):
        qtbot.keyClick(screen, key)

    attempts = tracking_service.attempts_for_question(questions[0].id)
    assert [attempt.status for attempt in attempts] == [
        AttemptStatus.CORRECT,
        AttemptStatus.PARTIAL,
        AttemptStatus.INCORRECT,
        AttemptStatus.NO_START,
    ]
    assert len(screen.snapshot.columns[0].attempts) == 4
    assert screen.selected_question_id == questions[0].id


def test_marking_shortcuts_are_suppressed_during_text_entry(
    qtbot, tracking_service: TrackingService, library_service: LibraryService
) -> None:
    question_set, questions = _ready_set(tracking_service, 1)
    screen = _screen(qtbot, tracking_service, library_service, question_set.id)
    editor = QLineEdit(screen)
    editor.setObjectName("shortcutSuppressionEditor")
    screen.layout().addWidget(editor)
    editor.setFocus()

    qtbot.keyClick(editor, Qt.Key.Key_1)

    assert tracking_service.attempts_for_question(questions[0].id) == ()


def test_status_button_marks_selected_question(
    qtbot, tracking_service: TrackingService, library_service: LibraryService
) -> None:
    question_set, questions = _ready_set(tracking_service, 1)
    screen = _screen(qtbot, tracking_service, library_service, question_set.id)

    qtbot.mouseClick(
        screen.status_buttons[AttemptStatus.PARTIAL], Qt.MouseButton.LeftButton
    )

    attempts = tracking_service.attempts_for_question(questions[0].id)
    assert len(attempts) == 1
    assert attempts[0].status is AttemptStatus.PARTIAL


def test_tracker_keyboard_undo_redo_restores_same_attempt(
    qtbot, tracking_service: TrackingService, library_service: LibraryService
) -> None:
    question_set, questions = _ready_set(tracking_service, 1)
    screen = _screen(qtbot, tracking_service, library_service, question_set.id)
    qtbot.keyClick(screen, Qt.Key.Key_3)
    attempt_id = tracking_service.attempts_for_question(questions[0].id)[0].id

    qtbot.keyClick(
        screen, Qt.Key.Key_Z, modifier=Qt.KeyboardModifier.ControlModifier
    )
    assert tracking_service.attempts_for_question(questions[0].id) == ()

    qtbot.keyClick(
        screen, Qt.Key.Key_Y, modifier=Qt.KeyboardModifier.ControlModifier
    )
    restored = tracking_service.attempts_for_question(questions[0].id)
    assert len(restored) == 1
    assert restored[0].id == attempt_id


def test_attempt_box_opens_editor(
    qtbot,
    monkeypatch,
    tracking_service: TrackingService,
    library_service: LibraryService,
) -> None:
    question_set, questions = _ready_set(tracking_service, 1)
    attempt = tracking_service.add_attempt(
        questions[0].id, AttemptStatus.INCORRECT
    )
    opened: list[str] = []
    monkeypatch.setattr(
        AttemptEditorDialog,
        "exec",
        lambda dialog: opened.append(dialog.attempt.id)
        or QDialog.DialogCode.Rejected,
    )
    screen = _screen(qtbot, tracking_service, library_service, question_set.id)
    button = screen.findChild(QPushButton, f"attemptButton-{attempt.id}")

    qtbot.mouseClick(button, Qt.MouseButton.LeftButton)

    assert opened == [attempt.id]


def test_attempt_hover_shows_date_status_and_note(
    qtbot, tracking_service: TrackingService, library_service: LibraryService
) -> None:
    question_set, questions = _ready_set(tracking_service, 1)
    attempt = tracking_service.add_attempt(
        questions[0].id, AttemptStatus.PARTIAL, note="Try elimination"
    )
    screen = _screen(qtbot, tracking_service, library_service, question_set.id)
    button = screen.findChild(QPushButton, f"attemptButton-{attempt.id}")

    assert "Partial" in button.toolTip()
    assert "Date:" in button.toolTip()
    assert "Try elimination" in button.toolTip()


def test_unattempted_question_opens_history_from_attempt_area(
    qtbot,
    monkeypatch,
    tracking_service: TrackingService,
    library_service: LibraryService,
) -> None:
    question_set, questions = _ready_set(tracking_service, 1)
    opened: list[str] = []
    monkeypatch.setattr(
        QuestionAttemptsDialog,
        "exec",
        lambda dialog: opened.append(dialog.question_id)
        or QDialog.DialogCode.Rejected,
    )
    screen = _screen(qtbot, tracking_service, library_service, question_set.id)
    empty_button = screen.findChild(
        QPushButton, f"unattemptedButton-{questions[0].id}"
    )

    assert screen.hierarchy_headings[questions[0].id].text() == "1"
    qtbot.mouseClick(empty_button, Qt.MouseButton.LeftButton)

    assert opened == [questions[0].id]


def test_attempt_history_can_bulk_delete_and_undo(
    qtbot,
    monkeypatch,
    tracking_service: TrackingService,
) -> None:
    question_set, questions = _ready_set(tracking_service, 1)
    attempts = tuple(
        tracking_service.add_attempt(questions[0].id, status)
        for status in (
            AttemptStatus.INCORRECT,
            AttemptStatus.PARTIAL,
            AttemptStatus.CORRECT,
        )
    )
    undo_stack = QUndoStack()
    dialog = QuestionAttemptsDialog(
        tracking_service, undo_stack, questions[0].id, "1"
    )
    qtbot.addWidget(dialog)
    selection = dialog.attempts_table.selectionModel()
    flags = (
        QItemSelectionModel.SelectionFlag.Select
        | QItemSelectionModel.SelectionFlag.Rows
    )
    selection.select(dialog.attempts_table.model().index(0, 0), flags)
    selection.select(dialog.attempts_table.model().index(1, 0), flags)
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )

    qtbot.mouseClick(dialog.delete_button, Qt.MouseButton.LeftButton)

    assert tracking_service.attempts_for_question(questions[0].id) == (
        attempts[0],
    )
    undo_stack.undo()
    assert {
        item.id
        for item in tracking_service.attempts_for_question(questions[0].id)
    } == {item.id for item in attempts}


@pytest.mark.parametrize("access", ["question", "stack_margin", "context_action"])
def test_attempted_question_opens_complete_history(
    qtbot, monkeypatch, tracking_service, library_service, access
):
    question_set, questions = _ready_set(tracking_service, 2)
    question = questions[1]
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    for index in range(8):
        tracking_service.add_attempt(
            question.id, AttemptStatus.PARTIAL,
            timestamp=timestamp + timedelta(days=index // 2), note=f"Note {index}",
        )
    expected = tuple(reversed(tracking_service.attempts_for_question(question.id)))
    opened = []

    def inspect(dialog):
        opened.append(dialog.question_id)
        assert question.local_label in dialog.windowTitle()
        assert dialog.attempts_table.rowCount() == len(expected)
        assert [
            dialog.attempts_table.item(row, 0).data(dialog.ATTEMPT_ID_ROLE)
            for row in range(len(expected))
        ] == [attempt.id for attempt in expected]
        assert [dialog.attempts_table.item(row, 2).text() for row in range(len(expected))] == [
            attempt.note for attempt in expected
        ]
        assert not dialog.delete_button.isEnabled()
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(QuestionAttemptsDialog, "exec", inspect)
    screen = _screen(qtbot, tracking_service, library_service, question_set.id)
    cell = screen.question_cells[question.id]
    if access == "context_action":
        next(action for action in cell.actions() if action.objectName() == "viewQuestionAttemptsAction").trigger()
    else:
        target = cell if access == "question" else cell.attempt_stack
        qtbot.mouseClick(target, Qt.MouseButton.LeftButton, pos=QPoint(1, 1))
    assert opened == [question.id]
    assert screen.selected_question_id == question.id


def test_history_deletion_refreshes_tracker_and_is_one_undo_action(
    qtbot, qapp, monkeypatch, tracking_service, library_service
):
    question_set, questions = _ready_set(tracking_service, 25)
    question = questions[20]
    for other in questions:
        if other.id != question.id:
            tracking_service.add_attempt(other.id, AttemptStatus.CORRECT)
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    before = tuple(
        tracking_service.add_attempt(
            question.id, status, timestamp=timestamp + timedelta(days=index), note=f"Saved {index}"
        )
        for index, status in enumerate((AttemptStatus.INCORRECT, AttemptStatus.PARTIAL, AttemptStatus.CORRECT))
    )
    screen = _screen(qtbot, tracking_service, library_service, question_set.id)
    screen.select_question(question.id)
    qapp.processEvents()
    horizontal = screen.tracker_scroll.horizontalScrollBar()
    vertical = screen.tracker_scroll.verticalScrollBar()
    assert horizontal.maximum() > 0
    horizontal.setValue(horizontal.maximum() // 2)
    scroll_position = (horizontal.value(), vertical.value())
    dialog = QuestionAttemptsDialog(tracking_service, screen.undo_stack, question.id, "21", screen)
    qtbot.addWidget(dialog)
    flags = QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows
    for row in (0, 1):
        dialog.attempts_table.selectionModel().select(dialog.attempts_table.model().index(row, 0), flags)
    prompts = []

    def cancel(parent, title, message):
        prompts.append(message)
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(QMessageBox, "question", cancel)
    qtbot.mouseClick(dialog.delete_button, Qt.MouseButton.LeftButton)
    assert "2 selected attempts" in prompts[0]
    assert tracking_service.attempts_for_question(question.id) == before
    assert screen.undo_stack.count() == 0
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    qtbot.mouseClick(dialog.delete_button, Qt.MouseButton.LeftButton)
    qapp.processEvents()
    assert tracking_service.attempts_for_question(question.id) == (before[0],)
    assert screen.undo_stack.count() == screen.undo_stack.index() == 1
    assert screen.snapshot.progress.complete and not screen.snapshot.progress.mastered
    assert screen.question_cells[question.id].column.attempts[-1].attempt == before[0]
    assert screen.selected_question_id == question.id
    assert (horizontal.value(), vertical.value()) == scroll_position
    screen.undo_stack.undo()
    assert tracking_service.attempts_for_question(question.id) == before
    assert screen.snapshot.progress.mastered
    screen.undo_stack.redo()
    assert tracking_service.attempts_for_question(question.id) == (before[0],)
    qtbot.mouseClick(dialog.select_all_button, Qt.MouseButton.LeftButton)
    qtbot.mouseClick(dialog.delete_button, Qt.MouseButton.LeftButton)
    qapp.processEvents()
    assert tracking_service.attempts_for_question(question.id) == ()
    assert not screen.snapshot.progress.complete and not screen.snapshot.progress.mastered
    assert screen.question_cells[question.id].column.is_unattempted
    assert screen.findChild(QPushButton, f"unattemptedButton-{question.id}") is not None
    assert screen.selected_question_id == question.id
    assert (horizontal.value(), vertical.value()) == scroll_position
    assert not dialog.delete_button.isEnabled()


def test_attempt_editor_changes_existing_attempt_in_place(
    qtbot,
    monkeypatch,
    tracking_service: TrackingService,
    library_service: LibraryService,
) -> None:
    question_set, questions = _ready_set(tracking_service, 1)
    attempt = tracking_service.add_attempt(
        questions[0].id, AttemptStatus.INCORRECT, note="Before"
    )

    def edit_and_accept(dialog: AttemptEditorDialog):
        dialog.status_combo.setCurrentIndex(
            dialog.status_combo.findData(AttemptStatus.PARTIAL.value)
        )
        dialog.note_edit.setPlainText("Corrected note")
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(AttemptEditorDialog, "exec", edit_and_accept)
    screen = _screen(qtbot, tracking_service, library_service, question_set.id)
    button = screen.findChild(QPushButton, f"attemptButton-{attempt.id}")

    qtbot.mouseClick(button, Qt.MouseButton.LeftButton)

    edited = tracking_service.get_attempt(attempt.id)
    assert edited.status is AttemptStatus.PARTIAL
    assert edited.note == "Corrected note"
    assert len(tracking_service.attempts_for_question(questions[0].id)) == 1


def test_bookmark_and_question_note_controls_persist(
    qtbot,
    monkeypatch,
    tracking_service: TrackingService,
    library_service: LibraryService,
) -> None:
    question_set, questions = _ready_set(tracking_service, 1)
    monkeypatch.setattr(
        QInputDialog,
        "getMultiLineText",
        lambda *args, **kwargs: ("Remember this method", True),
    )
    screen = _screen(qtbot, tracking_service, library_service, question_set.id)
    qtbot.mouseClick(screen.bookmark_button, Qt.MouseButton.LeftButton)
    qtbot.mouseClick(
        screen.note_button,
        Qt.MouseButton.LeftButton,
    )

    question = tracking_service.get_question(questions[0].id)
    assert question.bookmarked is True
    assert question.note == "Remember this method"
    assert tracking_service.attempts_for_question(questions[0].id) == ()


def test_needs_setup_routes_to_existing_editor(
    qtbot,
    monkeypatch,
    tracking_service: TrackingService,
    library_service: LibraryService,
) -> None:
    question_set = tracking_service.create_question_set(
        QuestionSetKind.PAST_PAPER, "Unconfigured paper"
    )
    opened: list[str] = []
    monkeypatch.setattr(
        QuestionEditorDialog,
        "exec",
        lambda editor: opened.append(editor.question_set_id)
        or QDialog.DialogCode.Accepted,
    )

    screen = _screen(qtbot, tracking_service, library_service, question_set.id)

    assert opened == [question_set.id]
    assert screen.current_question_set_id is None
    assert tracking_service.get_question_set(question_set.id).setup_state is SetupState.NEEDS_SETUP


def test_navigation_hides_solutions_and_marks_missing_and_needs_setup(
    qtbot,
    tracking_service: TrackingService,
    library_service: LibraryService,
    application_paths: ApplicationPaths,
) -> None:
    module = application_paths.managed_library_root / "Year 2" / "Algebra"
    sheet = module / "Problem Sheets" / "Sheet 1.pdf"
    solution = module / "Problem Sheet Solutions" / "Sheet 1 solution.pdf"
    paper = module / "Past Papers" / "2025.pdf"
    _write_pdf(sheet)
    _write_pdf(solution)
    _write_pdf(paper)
    library_service.scan_library()
    sheet.unlink()
    library_service.scan_library()

    screen = TrackerScreen(tracking_service, library_service, QUndoStack())
    qtbot.addWidget(screen)
    texts = [
        item.text(0)
        for item in screen.navigation_tree.findItems(
            "*", Qt.MatchFlag.MatchWildcard | Qt.MatchFlag.MatchRecursive
        )
    ]

    assert "Problem Sheet Solutions" not in texts
    assert not any("solution" in text.casefold() for text in texts)
    assert any("Sheet 1" in text and "Missing" in text for text in texts)
    assert any("2025" in text and "Needs setup" in text for text in texts)


def test_many_questions_use_horizontal_scrolling(
    qtbot, tracking_service: TrackingService, library_service: LibraryService
) -> None:
    question_set, _ = _ready_set(tracking_service, 30)
    screen = _screen(qtbot, tracking_service, library_service, question_set.id)
    screen.resize(600, 450)
    qtbot.wait(20)

    assert screen.tracker_scroll.horizontalScrollBar().maximum() > 0


def test_marking_an_offscreen_question_preserves_horizontal_scroll(
    qtbot, tracking_service: TrackingService, library_service: LibraryService
) -> None:
    question_set, questions = _ready_set(tracking_service, 30)
    screen = _screen(qtbot, tracking_service, library_service, question_set.id)
    screen.resize(600, 450)
    qtbot.wait(20)
    scrollbar = screen.tracker_scroll.horizontalScrollBar()
    scrollbar.setValue(scrollbar.maximum())
    original_position = scrollbar.value()
    screen.select_question(questions[-1].id)

    qtbot.mouseClick(
        screen.status_buttons[AttemptStatus.CORRECT], Qt.MouseButton.LeftButton
    )
    qtbot.wait(20)

    assert original_position > 0
    assert scrollbar.value() == min(original_position, scrollbar.maximum())


def test_zoom_controls_scale_question_cells(
    qtbot, tracking_service: TrackingService, library_service: LibraryService
) -> None:
    question_set, questions = _ready_set(tracking_service, 2)
    screen = _screen(qtbot, tracking_service, library_service, question_set.id)
    original_width = screen.question_cells[questions[0].id].width()

    qtbot.mouseClick(screen.zoom_out_button, Qt.MouseButton.LeftButton)
    qtbot.wait(10)

    assert screen.zoom_percent == 50
    assert screen.zoom_label.text() == "50%"
    assert screen.question_cells[questions[0].id].width() < original_width


def test_parent_question_heading_spans_its_child_cells(
    qtbot, tracking_service: TrackingService, library_service: LibraryService
) -> None:
    question_set = tracking_service.create_question_set(
        QuestionSetKind.PROBLEM_SHEET,
        "Structured sheet",
        setup_state=SetupState.READY,
    )
    parent = tracking_service.create_question(question_set.id, "1")
    first = tracking_service.add_child(parent.id, "a")
    tracking_service.add_sibling(first.id, "b")

    screen = _screen(qtbot, tracking_service, library_service, question_set.id)
    heading = screen.findChild(QLabel, f"parentSpan-{parent.id}")

    assert heading is not None
    assert heading.text() == "1"
    assert heading.width() > screen.question_cells[first.id].width()


def test_hierarchy_headings_use_fixed_rows_and_attempts_share_baseline(
    qtbot, tracking_service: TrackingService, library_service: LibraryService
) -> None:
    question_set = tracking_service.create_question_set(
        QuestionSetKind.PROBLEM_SHEET,
        "Structured sheet",
        setup_state=SetupState.READY,
    )
    first = tracking_service.create_question(question_set.id, "1")
    first_a = tracking_service.add_child(first.id, "a")
    first_b = tracking_service.add_sibling(first_a.id, "b")
    first_c = tracking_service.add_sibling(first_b.id, "c")
    first_ci = tracking_service.add_child(first_c.id, "i")
    first_cii = tracking_service.add_sibling(first_ci.id, "ii")
    first_d = tracking_service.add_sibling(first_c.id, "d")
    second = tracking_service.create_question(question_set.id, "2")
    screen = _screen(qtbot, tracking_service, library_service, question_set.id)
    grid = screen.tracker_scroll.widget().layout()

    def position(widget):
        return grid.getItemPosition(grid.indexOf(widget))

    root_rows = {
        position(screen.hierarchy_headings[question.id])[0]
        for question in (first, second)
    }
    second_rows = {
        position(screen.hierarchy_headings[question.id])[0]
        for question in (first_a, first_b, first_c, first_d)
    }
    attempt_rows = {
        position(screen.question_cells[question.id])[0]
        for question in (first_a, first_b, first_ci, first_cii, first_d, second)
    }

    assert len(root_rows) == 1
    assert len(second_rows) == 1
    assert len(attempt_rows) == 1
    assert next(iter(root_rows)) < next(iter(second_rows)) < next(iter(attempt_rows))
    assert first_ci.id not in screen.hierarchy_headings
    assert first_cii.id not in screen.hierarchy_headings
    assert position(screen.hierarchy_headings[first.id])[3] == 5
    assert position(screen.hierarchy_headings[second.id])[3] == 1
    assert position(screen.hierarchy_headings[first_c.id])[3] == 2
    assert screen.question_cells[first_ci.id].label_button.isHidden()
    assert "background: transparent" in screen.question_cells[first_ci.id].styleSheet()


def test_topic_band_uses_persisted_colour_and_readable_foreground(
    qtbot, tracking_service: TrackingService, library_service: LibraryService
) -> None:
    question_set, questions = _ready_set(tracking_service, 2)
    topic = tracking_service.create_topic(
        question_set.id, "Bases", colour="#112233"
    )
    for question in questions:
        tracking_service.assign_topic(question.id, topic.id)

    screen = _screen(qtbot, tracking_service, library_service, question_set.id)
    band = screen.findChild(QLabel, f"topicSpan-{topic.id}-0-1")

    assert band is not None
    assert "background: #112233" in band.styleSheet()
    assert "color: #FFFFFF" in band.styleSheet()
    assert contrasting_text_colour("#000000") == "#FFFFFF"
    assert contrasting_text_colour("#FFFFFF") == "#000000"


def test_returning_from_question_editor_refreshes_topic_band_colour(
    qtbot,
    monkeypatch,
    tracking_service: TrackingService,
    library_service: LibraryService,
) -> None:
    question_set, questions = _ready_set(tracking_service, 1)
    topic = tracking_service.create_topic(question_set.id, "Bases")
    tracking_service.assign_topic(questions[0].id, topic.id)
    screen = _screen(qtbot, tracking_service, library_service, question_set.id)

    def change_colour_and_close(dialog):
        dialog.service.set_topic_colour(topic.id, "#654321")
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(QuestionEditorDialog, "exec", change_colour_and_close)
    qtbot.mouseClick(screen.edit_questions_button, Qt.MouseButton.LeftButton)

    band = screen.findChild(QLabel, f"topicSpan-{topic.id}-0-0")
    assert band is not None
    assert "background: #654321" in band.styleSheet()


def test_module_view_shows_all_ready_question_sets_together(
    qtbot,
    tracking_service: TrackingService,
    library_service: LibraryService,
    application_paths: ApplicationPaths,
) -> None:
    module = application_paths.managed_library_root / "Year 2" / "Linear Algebra"
    _write_pdf(module / "Problem Sheets" / "Sheet 1.pdf")
    _write_pdf(module / "Problem Sheets" / "Sheet 2.pdf")
    library_service.scan_library()
    question_sets = library_service.question_sets_for_module(
        "Year 2", "Linear Algebra"
    )
    question_ids: list[str] = []
    for question_set in question_sets:
        question = tracking_service.create_question(question_set.id, "1")
        question_ids.append(question.id)
        tracking_service.finish_question_set_setup(question_set.id)

    screen = _screen(
        qtbot, tracking_service, library_service, question_sets[0].id
    )

    assert screen.title_label.text() == "Linear Algebra — All question sets"
    assert len(screen.module_snapshot.question_sets) == 2
    assert set(screen.question_cells) == set(question_ids)


def test_home_routes_ready_set_to_tracker_callback(
    qtbot,
    tracking_service: TrackingService,
    application_paths: ApplicationPaths,
) -> None:
    question_set, _ = _ready_set(tracking_service, 1)
    opened: list[str] = []
    home = HomeScreen(
        tracking_service,
        application_paths,
        open_tracker=opened.append,
    )
    qtbot.addWidget(home)

    qtbot.mouseClick(home.open_button, Qt.MouseButton.LeftButton)

    assert opened == [question_set.id]


def test_main_window_routes_tracker_back_to_home_with_shared_undo_stack(
    qtbot,
    tracking_service: TrackingService,
    library_service: LibraryService,
    application_paths: ApplicationPaths,
) -> None:
    question_set, _ = _ready_set(tracking_service, 1)
    window = MainWindow(application_paths, tracking_service, library_service)
    qtbot.addWidget(window)

    window.show_tracker(question_set.id)
    tracker = window.centralWidget()
    assert isinstance(tracker, TrackerScreen)
    assert tracker.undo_stack is window.undo_stack

    back_button = tracker.findChild(QPushButton, "backToHomeButton")
    qtbot.mouseClick(back_button, Qt.MouseButton.LeftButton)

    assert isinstance(window.centralWidget(), HomeScreen)
