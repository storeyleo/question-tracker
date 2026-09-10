from PySide6.QtCore import Qt
from PySide6.QtGui import QUndoStack
from PySide6.QtWidgets import QLabel, QPushButton

from question_tracker.application.tracker_appearance import DEFAULT_APPEARANCE
from question_tracker.application.tracker_view import ModuleTrackerSnapshot, build_tracker_snapshot
from question_tracker.domain.tracking import AttemptStatus, QuestionSetKind, SetupState
from question_tracker.ui import tracker


def test_section_progress_and_actions_use_their_own_question_set(
    qtbot, monkeypatch, tracking_service, library_service
):
    monkeypatch.setattr(tracker, "load_tracker_appearance", lambda: DEFAULT_APPEARANCE)
    sets = [tracking_service.create_question_set(
        QuestionSetKind.PROBLEM_SHEET, name, setup_state=SetupState.READY
    ) for name in ("Sheet One", "Sheet Two")]
    leaves = [tracking_service.create_question(item.id, "1") for item in sets]
    opened = []
    edited = []
    screen = tracker.TrackerScreen(
        tracking_service, library_service, QUndoStack(), initial_question_set_id=sets[0].id,
        on_open_marking=lambda *args: opened.append(args),
    )
    qtbot.addWidget(screen)
    monkeypatch.setattr(screen, "_edit_question_set", edited.append)

    def render():
        screen.module_snapshot = ModuleTrackerSnapshot(
            None, tuple(build_tracker_snapshot(tracking_service, item.id) for item in sets)
        )
        screen._render_snapshot(screen.module_snapshot)

    render()
    for item in sets:
        assert screen.findChild(QLabel, f"questionSetHeading-{item.id}").text() == item.display_name
        assert screen.findChild(QLabel, f"questionSetProgress-{item.id}").text() == "Attempted 0/1   Incomplete   Not Mastered"
    tracking_service.add_attempt(leaves[1].id, AttemptStatus.INCORRECT)
    render()
    assert screen.findChild(QLabel, f"questionSetProgress-{sets[1].id}").text() == "Attempted 1/1   Complete   Not Mastered"
    tracking_service.add_attempt(leaves[1].id, AttemptStatus.CORRECT)
    render()
    assert screen.findChild(QLabel, f"questionSetProgress-{sets[1].id}").text() == "Attempted 1/1   Complete   Mastered"
    qtbot.mouseClick(screen.findChild(QPushButton, f"sectionEditQuestions-{sets[1].id}"), Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: edited == [sets[1].id])
    qtbot.mouseClick(screen.findChild(QPushButton, f"sectionOpenMarking-{sets[1].id}"), Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: opened == [(sets[1].id, leaves[1].id)])
    assert screen.selected_question_id == leaves[0].id
    # These are the original controls; shortcut and selected-question behaviour
    # continue to be exercised by the existing tracker UI tests.
    for name in ("undo_button", "redo_button", "bookmark_button", "note_button",
                 "zoom_out_button", "zoom_in_button", "appearance_button"):
        assert getattr(screen, name).parentWidget() is not None
    assert set(screen.status_buttons) == set(AttemptStatus)
