from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QInputDialog, QMessageBox

from question_tracker.application.tracking_service import TrackingService
from question_tracker.domain.tracking import QuestionSetKind, SetupState
from question_tracker.paths import ApplicationPaths
from question_tracker.ui.home import HomeScreen
from question_tracker.ui.question_editor import QuestionEditorDialog


def _new_set(service: TrackingService):
    return service.create_question_set(QuestionSetKind.PROBLEM_SHEET, "Sheet 1")


def _tree_item_for_id(dialog: QuestionEditorDialog, question_id: str):
    matches = dialog.question_tree.findItems(
        "*", Qt.MatchFlag.MatchWildcard | Qt.MatchFlag.MatchRecursive
    )
    return next(
        item
        for item in matches
        if item.data(0, Qt.ItemDataRole.UserRole) == question_id
    )


def test_editor_opens_for_needs_setup_and_displays_hierarchy(
    qtbot, tracking_service: TrackingService
) -> None:
    question_set = _new_set(tracking_service)
    root = tracking_service.create_question(question_set.id, "1")
    tracking_service.add_child(root.id, "a")

    dialog = QuestionEditorDialog(tracking_service, question_set.id)
    qtbot.addWidget(dialog)

    assert dialog.display_name_edit.text() == "Sheet 1"
    assert dialog.question_tree.topLevelItemCount() == 1
    assert dialog.question_tree.topLevelItem(0).text(0) == "1"
    assert dialog.question_tree.topLevelItem(0).child(0).text(0) == "1.a"


def test_basic_add_question_interaction(
    qtbot, monkeypatch, tracking_service: TrackingService
) -> None:
    question_set = _new_set(tracking_service)
    dialog = QuestionEditorDialog(tracking_service, question_set.id)
    qtbot.addWidget(dialog)
    monkeypatch.setattr(
        QInputDialog, "getText", lambda *args, **kwargs: ("1", True)
    )

    qtbot.mouseClick(dialog.add_root_button, Qt.MouseButton.LeftButton)

    questions = tracking_service.list_questions(question_set.id)
    assert len(questions) == 1
    assert questions[0].local_label == "1"
    assert dialog.question_tree.topLevelItemCount() == 1


def test_invalid_fourth_level_is_reported_without_crashing(
    qtbot, monkeypatch, tracking_service: TrackingService
) -> None:
    question_set = _new_set(tracking_service)
    root = tracking_service.create_question(question_set.id, "1")
    child = tracking_service.add_child(root.id, "a")
    grandchild = tracking_service.add_child(child.id, "i")
    dialog = QuestionEditorDialog(tracking_service, question_set.id)
    qtbot.addWidget(dialog)
    dialog.question_tree.setCurrentItem(_tree_item_for_id(dialog, grandchild.id))
    monkeypatch.setattr(
        QInputDialog, "getText", lambda *args, **kwargs: ("A", True)
    )
    messages: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda parent, title, message: messages.append(message),
    )

    qtbot.mouseClick(dialog.add_child_button, Qt.MouseButton.LeftButton)

    assert len(tracking_service.list_questions(question_set.id)) == 3
    assert messages and "cannot exceed 3 levels" in messages[0]


def test_valid_setup_can_be_finished_from_editor(
    qtbot, tracking_service: TrackingService
) -> None:
    question_set = _new_set(tracking_service)
    tracking_service.create_question(question_set.id, "1")
    dialog = QuestionEditorDialog(tracking_service, question_set.id)
    qtbot.addWidget(dialog)
    dialog.display_name_edit.setText("Week 1")
    dialog.display_name_edit.editingFinished.emit()
    dialog.general_note_edit.setPlainText("Review on Friday")

    qtbot.mouseClick(dialog.finish_setup_button, Qt.MouseButton.LeftButton)

    saved = tracking_service.get_question_set(question_set.id)
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert saved.setup_state is SetupState.READY
    assert saved.display_name == "Week 1"
    assert saved.general_note == "Review on Friday"


def test_question_details_and_multiple_topic_assignments_are_saved(
    qtbot, tracking_service: TrackingService
) -> None:
    question_set = _new_set(tracking_service)
    question = tracking_service.create_question(question_set.id, "1")
    first_topic = tracking_service.create_topic(question_set.id, "Vectors")
    second_topic = tracking_service.create_topic(question_set.id, "Proofs")
    dialog = QuestionEditorDialog(tracking_service, question_set.id)
    qtbot.addWidget(dialog)
    dialog.question_note_edit.setPlainText("Useful substitution")
    dialog.bookmark_check.setChecked(True)

    for index in range(dialog.assignment_list.count()):
        dialog.assignment_list.item(index).setCheckState(Qt.CheckState.Checked)

    saved_question = tracking_service.get_question(question.id)
    assert saved_question.note == "Useful substitution"
    assert saved_question.bookmarked is True
    assert set(tracking_service.topic_ids_for_question(question.id)) == {
        first_topic.id,
        second_topic.id,
    }


def test_close_preserves_auto_saved_edits_without_finishing_setup(
    qtbot, tracking_service: TrackingService
) -> None:
    question_set = _new_set(tracking_service)
    question = tracking_service.create_question(question_set.id, "1")
    dialog = QuestionEditorDialog(tracking_service, question_set.id)
    qtbot.addWidget(dialog)
    dialog.display_name_edit.setText("Renamed before closing")
    dialog.display_name_edit.editingFinished.emit()
    dialog.general_note_edit.setPlainText("Still being configured")
    dialog.question_note_edit.setPlainText("Saved question note")
    dialog.bookmark_check.setChecked(True)

    qtbot.mouseClick(dialog.close_button, Qt.MouseButton.LeftButton)

    saved_set = tracking_service.get_question_set(question_set.id)
    saved_question = tracking_service.get_question(question.id)
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert saved_set.setup_state is SetupState.NEEDS_SETUP
    assert saved_set.display_name == "Renamed before closing"
    assert saved_set.general_note == "Still being configured"
    assert saved_question.note == "Saved question note"
    assert saved_question.bookmarked is True


def test_ready_editor_auto_saves_without_finish_action(
    qtbot, tracking_service: TrackingService
) -> None:
    question_set = tracking_service.create_question_set(
        QuestionSetKind.PROBLEM_SHEET,
        "Ready sheet",
        setup_state=SetupState.READY,
    )
    tracking_service.create_question(question_set.id, "1")
    dialog = QuestionEditorDialog(tracking_service, question_set.id)
    qtbot.addWidget(dialog)

    assert dialog.finish_setup_button.isHidden()
    dialog.general_note_edit.setPlainText("Saved without a final action")
    qtbot.mouseClick(dialog.close_button, Qt.MouseButton.LeftButton)

    saved = tracking_service.get_question_set(question_set.id)
    assert saved.setup_state is SetupState.READY
    assert saved.general_note == "Saved without a final action"


def test_home_lists_needs_setup_and_ready_question_sets(
    qtbot,
    tracking_service: TrackingService,
    application_paths: ApplicationPaths,
) -> None:
    needs_setup = _new_set(tracking_service)
    ready = tracking_service.create_question_set(
        QuestionSetKind.PAST_PAPER,
        "May 2025",
        setup_state=SetupState.READY,
    )

    home = HomeScreen(tracking_service, application_paths)
    qtbot.addWidget(home)

    ids = {
        home.question_set_list.item(index).data(Qt.ItemDataRole.UserRole)
        for index in range(home.question_set_list.count())
    }
    assert ids == {needs_setup.id, ready.id}


def test_home_action_opens_selected_question_set_editor(
    qtbot,
    monkeypatch,
    tracking_service: TrackingService,
    application_paths: ApplicationPaths,
) -> None:
    question_set = _new_set(tracking_service)
    opened: list[str] = []
    monkeypatch.setattr(
        QuestionEditorDialog,
        "exec",
        lambda dialog: opened.append(dialog.question_set_id)
        or QDialog.DialogCode.Rejected,
    )
    home = HomeScreen(tracking_service, application_paths)
    qtbot.addWidget(home)

    qtbot.mouseClick(home.open_button, Qt.MouseButton.LeftButton)

    assert opened == [question_set.id]
