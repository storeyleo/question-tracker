from __future__ import annotations

from PySide6.QtGui import QUndoStack

from question_tracker.application.tracking_service import TrackingService
from question_tracker.domain.tracking import AttemptStatus, QuestionSetKind, SetupState
from question_tracker.ui.tracker_commands import (
    AddAttemptCommand,
    DeleteAttemptCommand,
    DeleteAttemptsCommand,
    EditAttemptCommand,
    EditQuestionNoteCommand,
    SetBookmarkCommand,
)


def _leaf(service: TrackingService):
    question_set = service.create_question_set(
        QuestionSetKind.PROBLEM_SHEET,
        "Sheet",
        setup_state=SetupState.READY,
    )
    return service.create_question(question_set.id, "1")


def test_add_attempt_undo_redo_restores_same_uuid(
    qtbot, tracking_service: TrackingService
) -> None:
    del qtbot
    leaf = _leaf(tracking_service)
    stack = QUndoStack()
    command = AddAttemptCommand(
        tracking_service, leaf.id, AttemptStatus.INCORRECT
    )

    stack.push(command)
    attempt_id = command.attempt.id
    stack.undo()
    assert tracking_service.attempts_for_question(leaf.id) == ()

    stack.redo()
    restored = tracking_service.attempts_for_question(leaf.id)
    assert len(restored) == 1
    assert restored[0].id == attempt_id
    assert restored[0].status is AttemptStatus.INCORRECT


def test_edit_attempt_undo_restores_original_without_creating_row(
    qtbot, tracking_service: TrackingService
) -> None:
    del qtbot
    leaf = _leaf(tracking_service)
    original = tracking_service.add_attempt(
        leaf.id, AttemptStatus.INCORRECT, note="Original"
    )
    stack = QUndoStack()

    stack.push(
        EditAttemptCommand(
            tracking_service, original, AttemptStatus.PARTIAL, "Corrected note"
        )
    )
    edited = tracking_service.get_attempt(original.id)
    assert edited.status is AttemptStatus.PARTIAL
    assert edited.note == "Corrected note"
    assert len(tracking_service.attempts_for_question(leaf.id)) == 1

    stack.undo()
    restored = tracking_service.get_attempt(original.id)
    assert restored.status is AttemptStatus.INCORRECT
    assert restored.note == "Original"


def test_delete_attempt_undo_restores_exact_attempt_and_preserves_others(
    qtbot, tracking_service: TrackingService
) -> None:
    del qtbot
    leaf = _leaf(tracking_service)
    first = tracking_service.add_attempt(leaf.id, AttemptStatus.INCORRECT)
    second = tracking_service.add_attempt(leaf.id, AttemptStatus.CORRECT)
    stack = QUndoStack()

    stack.push(DeleteAttemptCommand(tracking_service, second))
    assert [item.id for item in tracking_service.attempts_for_question(leaf.id)] == [
        first.id
    ]

    stack.undo()
    restored = tracking_service.get_attempt(second.id)
    assert restored == second
    assert {item.id for item in tracking_service.attempts_for_question(leaf.id)} == {
        first.id,
        second.id,
    }

    stack.redo()
    assert [item.id for item in tracking_service.attempts_for_question(leaf.id)] == [
        first.id
    ]


def test_bulk_delete_attempts_is_one_undoable_action(
    qtbot, tracking_service: TrackingService
) -> None:
    del qtbot
    leaf = _leaf(tracking_service)
    attempts = (
        tracking_service.add_attempt(leaf.id, AttemptStatus.INCORRECT),
        tracking_service.add_attempt(leaf.id, AttemptStatus.PARTIAL),
        tracking_service.add_attempt(leaf.id, AttemptStatus.CORRECT),
    )
    stack = QUndoStack()

    stack.push(DeleteAttemptsCommand(tracking_service, attempts[:2]))

    assert tracking_service.attempts_for_question(leaf.id) == (attempts[2],)
    assert stack.undoText() == "Delete 2 attempts"
    stack.undo()
    assert {
        item.id for item in tracking_service.attempts_for_question(leaf.id)
    } == {item.id for item in attempts}


def test_bookmark_and_question_note_commands_undo_and_redo(
    qtbot, tracking_service: TrackingService
) -> None:
    del qtbot
    leaf = _leaf(tracking_service)
    stack = QUndoStack()

    stack.push(SetBookmarkCommand(tracking_service, leaf.id, False, True))
    stack.push(
        EditQuestionNoteCommand(
            tracking_service, leaf.id, "", "Review this proof"
        )
    )
    assert tracking_service.get_question(leaf.id).bookmarked is True
    assert tracking_service.get_question(leaf.id).note == "Review this proof"

    stack.undo()
    stack.undo()
    assert tracking_service.get_question(leaf.id).bookmarked is False
    assert tracking_service.get_question(leaf.id).note == ""

    stack.redo()
    stack.redo()
    assert tracking_service.get_question(leaf.id).bookmarked is True
    assert tracking_service.get_question(leaf.id).note == "Review this proof"


def test_undo_history_is_not_persistent(
    qtbot, tracking_service: TrackingService
) -> None:
    del qtbot
    leaf = _leaf(tracking_service)
    first_session = QUndoStack()
    first_session.push(
        AddAttemptCommand(tracking_service, leaf.id, AttemptStatus.CORRECT)
    )

    second_session = QUndoStack()

    assert first_session.canUndo() is True
    assert second_session.canUndo() is False
