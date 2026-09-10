from dataclasses import asdict, fields

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtGui import QUndoStack
from PySide6.QtWidgets import QPushButton

from question_tracker.application import tracker_appearance
from question_tracker.application.tracker_appearance import (
    DEFAULT_APPEARANCE,
    TrackerAppearance,
    load_tracker_appearance,
    reset_tracker_appearance,
    save_tracker_appearance,
)
from question_tracker.domain.tracking import AttemptStatus, QuestionSetKind, SetupState
from question_tracker.application.tracker_view import build_tracker_snapshot
from question_tracker.ui.tracker import (
    DEFAULT_ZOOM_PERCENT,
    TRACKER_METRICS,
    QuestionCell,
    TrackerScreen,
    ATTEMPT_STYLES,
)


@pytest.fixture
def settings(tmp_path):
    return QSettings(str(tmp_path / "appearance.ini"), QSettings.Format.IniFormat)


def test_defaults_match_milestone_7(settings):
    assert asdict(DEFAULT_APPEARANCE) == {
        "question_box_size": 50,
        "horizontal_question_spacing": 4,
        "attempt_stack_offset": 4,
        "hierarchy_heading_text_size": 10,
        "hierarchy_line_thickness": 1,
    }
    assert load_tracker_appearance(settings) == DEFAULT_APPEARANCE
    assert TRACKER_METRICS.stack_width == 70
    assert TRACKER_METRICS.cell_width == 78
    assert DEFAULT_ZOOM_PERCENT == 60


def test_all_five_settings_round_trip(settings):
    appearance = TrackerAppearance(64, 8, 6, 12, 2)
    save_tracker_appearance(appearance, settings)
    reopened = QSettings(settings.fileName(), QSettings.Format.IniFormat)
    assert load_tracker_appearance(reopened) == appearance
    assert len(reopened.allKeys()) == 5


def test_reset_restores_only_appearance_defaults(settings):
    settings.setValue("unrelated/value", "preserved")
    save_tracker_appearance(TrackerAppearance(64, 8, 6, 12, 2), settings)
    assert reset_tracker_appearance(settings) == DEFAULT_APPEARANCE
    reopened = QSettings(settings.fileName(), QSettings.Format.IniFormat)
    assert load_tracker_appearance(reopened) == DEFAULT_APPEARANCE
    assert reopened.value("unrelated/value") == "preserved"


@pytest.mark.parametrize("malformed", ["bad", "", "2.5", None, True, [], 2.5])
def test_malformed_values_fall_back(settings, malformed):
    for field in fields(TrackerAppearance):
        settings.setValue("trackerAppearance/" + field.name, malformed)
    assert load_tracker_appearance(settings) == DEFAULT_APPEARANCE


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("-100", TrackerAppearance(1, 0, 0, 1, 1)),
        ("999999999999999999999999", TrackerAppearance(256, 128, 64, 96, 16)),
    ],
)
def test_out_of_range_values_are_clamped(settings, raw, expected):
    for field in fields(TrackerAppearance):
        settings.setValue("trackerAppearance/" + field.name, raw)
    assert load_tracker_appearance(settings) == expected


def test_save_validates_values_and_missing_values_keep_defaults(settings):
    settings.setValue("trackerAppearance/question_box_size", "64")
    assert load_tracker_appearance(settings) == TrackerAppearance(question_box_size=64)
    save_tracker_appearance(TrackerAppearance(question_box_size="bad"), settings)
    assert load_tracker_appearance(settings) == DEFAULT_APPEARANCE


@pytest.mark.parametrize("appearance", [
    DEFAULT_APPEARANCE,
    TrackerAppearance(64, 8, 6, 12, 3),
    TrackerAppearance(64, 0, 0, 12, 3),
])
def test_tracker_consumes_settings_and_shared_cells_keep_defaults(
    qtbot, tracking_service, library_service, settings, monkeypatch, appearance
):
    save_tracker_appearance(appearance, settings)
    monkeypatch.setattr(tracker_appearance, "_settings", lambda supplied: settings)
    question_set = tracking_service.create_question_set(
        QuestionSetKind.PROBLEM_SHEET, "Appearance", setup_state=SetupState.READY
    )
    question = tracking_service.create_question(question_set.id, "1")
    attempts = [
        tracking_service.add_attempt(question.id, status)
        for status in (AttemptStatus.INCORRECT, AttemptStatus.CORRECT)
    ]
    screen = TrackerScreen(
        tracking_service, library_service, QUndoStack(),
        initial_question_set_id=question_set.id,
    )
    qtbot.addWidget(screen)
    assert screen.appearance == appearance
    assert screen.zoom_percent == 60
    scale = 0.6
    scaled = lambda value: max(1, round(value * scale))
    grid = screen.tracker_scroll.widget().layout()
    assert grid.horizontalSpacing() == round(appearance.horizontal_question_spacing * scale)
    cell = screen.question_cells[question.id]
    buttons = [cell.findChild(QPushButton, f"attemptButton-{item.id}") for item in attempts]
    assert buttons[0].width() == buttons[0].height() == scaled(appearance.question_box_size)
    assert buttons[0].x() == buttons[0].y() == scaled(4)
    assert buttons[1].x() == buttons[1].y() == scaled(4 + appearance.attempt_stack_offset)
    heading = screen.hierarchy_headings[question.id]
    assert heading.font().pixelSize() == scaled(appearance.hierarchy_heading_text_size)
    assert heading.line_thickness == scaled(appearance.hierarchy_line_thickness)

    # Marking constructs these shared cells without an appearance argument.
    compact_cell = QuestionCell(cell.column, 0.65)
    qtbot.addWidget(compact_cell)
    compact_button = compact_cell.findChild(QPushButton, f"attemptButton-{attempts[1].id}")
    assert compact_cell.width() == round(78 * 0.65)
    assert compact_button.width() == round(50 * 0.65)
    assert compact_button.x() == compact_button.y() == round(8 * 0.65)


@pytest.mark.parametrize("status", [None, *AttemptStatus])
def test_main_selection_is_independent_of_status(qtbot, tracking_service, status):
    question_set = tracking_service.create_question_set(QuestionSetKind.PROBLEM_SHEET, "Selection")
    question = tracking_service.create_question(question_set.id, "1")
    if status is not None:
        tracking_service.add_attempt(question.id, status)
    column = build_tracker_snapshot(tracking_service, question_set.id).columns[0]
    cell = QuestionCell(column, refined=True)
    qtbot.addWidget(cell)
    buttons = cell.attempt_stack.findChildren(QPushButton)
    assert len(buttons) == 1
    button = buttons[0]
    assert button.text() == ""
    assert button.width() == button.height()
    if status is None:
        assert button.objectName() == f"unattemptedButton-{question.id}"
        assert "palette(base)" in button.styleSheet()
        assert "palette(text)" in button.styleSheet()
        assert button.toolTip() == "Unattempted"
    else:
        assert ATTEMPT_STYLES[status].colour in button.styleSheet()
    original_style = button.styleSheet()
    original_geometry = button.geometry()
    original_size = cell.size()
    for selected in (True, False, True):
        cell.set_selected(selected)
        assert cell.is_selected is selected
        assert "border: 2px solid transparent" in cell.styleSheet()
        assert "background: transparent" in cell.styleSheet()
        assert button.styleSheet() == original_style
        assert button.geometry() == original_geometry
        assert cell.size() == original_size


@pytest.mark.parametrize("scale", [0.5, 1.0, 1.6])
def test_main_history_is_bounded_scaled_and_preserved(qtbot, qapp, tracking_service, scale):
    question_set = tracking_service.create_question_set(QuestionSetKind.PROBLEM_SHEET, "History")
    question = tracking_service.create_question(question_set.id, "1")
    for index in range(12):
        tracking_service.add_attempt(question.id, AttemptStatus.CORRECT, note=f"Attempt {index}")
    before = tracking_service.attempts_for_question(question.id)
    column = build_tracker_snapshot(tracking_service, question_set.id).columns[0]
    appearance = TrackerAppearance(attempt_stack_offset=6)
    main = QuestionCell(column, scale, appearance=appearance, refined=True)
    compact = QuestionCell(column, scale, appearance=appearance)
    for cell in (main, compact):
        qtbot.addWidget(cell)
        cell.show()
    qapp.processEvents()
    buttons = [
        main.attempt_stack.findChild(QPushButton, f"attemptButton-{item.attempt.id}")
        for item in column.attempts
    ]
    assert len(main.attempt_stack.findChildren(QPushButton)) == len(before)
    assert all(button is not None for button in buttons)
    assert main.attempt_stack.height() == round((50 + 8 + 4 * 6) * scale)
    assert compact.attempt_stack.height() == round((50 + 8 + 11 * 6) * scale)
    assert all(button.width() == button.height() == round(50 * scale) for button in buttons)
    assert buttons[0].x() == buttons[0].y() == round(4 * scale)
    assert buttons[-1].x() == buttons[-1].y() == round((4 + 4 * 6) * scale)
    assert all(first.x() <= second.x() for first, second in zip(buttons, buttons[1:]))
    overlap = buttons[0].geometry().intersected(buttons[-1].geometry())
    assert not overlap.isEmpty()
    assert main.attempt_stack.childAt(overlap.center()) is buttons[-1]
    assert all("Attempt" in button.toolTip() for button in buttons)
    main.set_selected(True)
    compact.set_selected(True)
    assert main.is_selected and compact.is_selected
    assert "#1267c4" in compact.styleSheet()  # Existing compact selection stays intact.
    assert tracking_service.attempts_for_question(question.id) == before
