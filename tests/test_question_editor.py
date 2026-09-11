from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QColorDialog,
    QDialog,
    QInputDialog,
    QLabel,
    QMessageBox,
)

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


def test_compact_editor_actions_remain_accessible(
    qtbot, monkeypatch, tracking_service: TrackingService
) -> None:
    question_set = _new_set(tracking_service)
    tracking_service.create_question(question_set.id, "1")
    dialog = QuestionEditorDialog(tracking_service, question_set.id)
    qtbot.addWidget(dialog)
    dialog.show()

    for button in (
        dialog.add_root_button, dialog.add_sibling_button, dialog.add_child_button,
        dialog.rename_button, dialog.move_up_button, dialog.move_down_button,
        dialog.reparent_button, dialog.delete_button, dialog.quick_builder_button,
        dialog.add_topic_button, dialog.rename_topic_button, dialog.delete_topic_button,
        dialog.topic_up_button, dialog.topic_down_button, dialog.topic_colour_button,
        dialog.reset_button, dialog.close_button, dialog.finish_setup_button,
    ):
        assert button.isVisibleTo(dialog)
    assert dialog.reset_button.menu().actions() == [
        dialog.reset_attempts_action, dialog.reset_questions_action,
    ]

    monkeypatch.setattr(QInputDialog, "getText", lambda *args, **kwargs: ("Algebra", True))
    qtbot.mouseClick(dialog.add_topic_button, Qt.MouseButton.LeftButton)
    assert dialog.topic_list.count() == 1
    assert dialog.topic_list.item(0).text() == "Algebra"


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


def test_question_details_and_replacement_label_are_saved(
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
        second_topic.id,
    }


def test_multiple_leaf_questions_can_receive_one_topic_assignment(
    qtbot, tracking_service: TrackingService
) -> None:
    question_set = _new_set(tracking_service)
    first = tracking_service.create_question(question_set.id, "1")
    second = tracking_service.create_question(question_set.id, "2")
    topic = tracking_service.create_topic(question_set.id, "Vectors")
    tracking_service.assign_topic(first.id, topic.id)
    dialog = QuestionEditorDialog(tracking_service, question_set.id)
    qtbot.addWidget(dialog)
    first_item = _tree_item_for_id(dialog, first.id)
    second_item = _tree_item_for_id(dialog, second.id)

    dialog.question_tree.clearSelection()
    first_item.setSelected(True)
    second_item.setSelected(True)

    assert dialog.question_tree.selectionMode() is QAbstractItemView.SelectionMode.ExtendedSelection
    assert dialog.assignment_list.isEnabled()
    assignment = dialog.assignment_list.item(0)
    assert assignment.checkState() is Qt.CheckState.PartiallyChecked
    assignment.setCheckState(Qt.CheckState.Checked)
    assert tracking_service.topic_ids_for_question(first.id) == (topic.id,)
    assert tracking_service.topic_ids_for_question(second.id) == (topic.id,)


def test_selected_parent_and_child_expand_to_deduplicated_leaf_assignments(
    qtbot, tracking_service: TrackingService
) -> None:
    question_set = _new_set(tracking_service)
    root = tracking_service.create_question(question_set.id, "1")
    child = tracking_service.add_child(root.id, "a")
    first = tracking_service.add_child(child.id, "i")
    second = tracking_service.add_sibling(first.id, "ii")
    sibling = tracking_service.add_sibling(child.id, "b")
    topic = tracking_service.create_topic(question_set.id, "Vectors")
    dialog = QuestionEditorDialog(tracking_service, question_set.id)
    qtbot.addWidget(dialog)

    dialog.question_tree.clearSelection()
    _tree_item_for_id(dialog, root.id).setSelected(True)
    _tree_item_for_id(dialog, child.id).setSelected(True)
    dialog.assignment_list.item(0).setCheckState(Qt.CheckState.Checked)

    assert dialog.assignment_list.isEnabled()
    assert tracking_service.effective_leaf_question_ids(
        question_set.id, (root.id, child.id)
    ) == (first.id, second.id, sibling.id)
    for leaf in (first, second, sibling):
        assert tracking_service.topic_ids_for_question(leaf.id) == (topic.id,)


def test_shift_range_containing_parents_can_receive_topic(
    qtbot, tracking_service: TrackingService
) -> None:
    question_set = _new_set(tracking_service)
    first_root = tracking_service.create_question(question_set.id, "1")
    first_leaf = tracking_service.add_child(first_root.id, "a")
    tracking_service.add_sibling(first_leaf.id, "b")
    second_root = tracking_service.create_question(question_set.id, "2")
    second_leaf = tracking_service.add_child(second_root.id, "a")
    final_leaf = tracking_service.add_sibling(second_leaf.id, "b")
    topic = tracking_service.create_topic(question_set.id, "Range topic")
    dialog = QuestionEditorDialog(tracking_service, question_set.id)
    qtbot.addWidget(dialog)
    dialog.show()

    first_item = _tree_item_for_id(dialog, first_root.id)
    final_item = _tree_item_for_id(dialog, final_leaf.id)
    qtbot.mouseClick(
        dialog.question_tree.viewport(),
        Qt.MouseButton.LeftButton,
        pos=dialog.question_tree.visualItemRect(first_item).center(),
    )
    qtbot.mouseClick(
        dialog.question_tree.viewport(),
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.ShiftModifier,
        dialog.question_tree.visualItemRect(final_item).center(),
    )
    dialog.assignment_list.item(0).setCheckState(Qt.CheckState.Checked)

    for leaf in tracking_service.leaf_questions(question_set.id):
        assert tracking_service.topic_ids_for_question(leaf.id) == (topic.id,)


def test_single_label_badge_replacement_colour_and_clearing_refresh_immediately(
    qtbot, monkeypatch, tracking_service: TrackingService
) -> None:
    question_set = _new_set(tracking_service)
    question = tracking_service.create_question(question_set.id, "1")
    first = tracking_service.create_topic(question_set.id, "Bases")
    second = tracking_service.create_topic(question_set.id, "Vectors")
    dialog = QuestionEditorDialog(tracking_service, question_set.id)
    qtbot.addWidget(dialog)

    assert tracking_service.get_question_label(question.id) is None
    assert dialog.findChild(QLabel, f"topicBadge-{question.id}-{first.id}") is None
    assert dialog.findChild(QLabel, f"topicBadge-{question.id}-{second.id}") is None
    # Use the editor's assignment action, which calls set_question_labels and
    # refreshes badges synchronously; no manual refresh hides a UI regression.
    dialog.assignment_list.item(0).setCheckState(Qt.CheckState.Checked)
    assert tracking_service.get_question_label(question.id).id == first.id
    assert dialog.findChild(QLabel, f"topicBadge-{question.id}-{first.id}")
    dialog.assignment_list.item(1).setCheckState(Qt.CheckState.Checked)
    assert tracking_service.get_question_label(question.id).id == second.id
    assert dialog.findChild(QLabel, f"topicBadge-{question.id}-{first.id}") is None
    assert dialog.findChild(QLabel, f"topicBadge-{question.id}-{second.id}")
    monkeypatch.setattr(
        QColorDialog, "getColor", lambda *args, **kwargs: QColor("#112233")
    )
    dialog.topic_list.setCurrentRow(1)
    qtbot.mouseClick(dialog.topic_colour_button, Qt.MouseButton.LeftButton)

    assert tracking_service.get_topic(second.id).colour == "#112233"
    badge = dialog.findChild(QLabel, f"topicBadge-{question.id}-{second.id}")
    assert "background: #112233" in badge.styleSheet()
    assert "color: #FFFFFF" in badge.styleSheet()

    dialog.assignment_list.item(1).setCheckState(Qt.CheckState.Unchecked)
    assert tracking_service.get_question_label(question.id) is None
    assert dialog.findChild(QLabel, f"topicBadge-{question.id}-{first.id}") is None
    assert dialog.findChild(QLabel, f"topicBadge-{question.id}-{second.id}") is None


def test_editor_reset_actions_use_confirmed_service_operations(
    qtbot, monkeypatch, tracking_service: TrackingService
) -> None:
    question_set = tracking_service.create_question_set(
        QuestionSetKind.PROBLEM_SHEET,
        "Ready",
        setup_state=SetupState.READY,
    )
    question = tracking_service.create_question(question_set.id, "1")
    tracking_service.add_attempt(question.id, "incorrect", note="Remove me")
    dialog = QuestionEditorDialog(tracking_service, question_set.id)
    qtbot.addWidget(dialog)
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )

    dialog.reset_attempts_action.trigger()

    assert tracking_service.attempts_for_question(question.id) == ()
    tracking_service.add_attempt(question.id, "partial")
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )
    dialog.reset_questions_action.trigger()

    assert tracking_service.list_questions(question_set.id) == ()
    assert dialog.question_tree.topLevelItemCount() == 0
    assert not dialog.finish_setup_button.isHidden()


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


def test_home_refresh_rescans_manually_added_pdfs(
    qtbot,
    tracking_service: TrackingService,
    application_paths: ApplicationPaths,
) -> None:
    home = HomeScreen(tracking_service, application_paths)
    qtbot.addWidget(home)
    pdf = (
        application_paths.managed_library_root
        / "Year 2"
        / "Linear Algebra"
        / "Problem Sheets"
        / "New sheet.pdf"
    )
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")

    qtbot.mouseClick(home.refresh_button, Qt.MouseButton.LeftButton)

    assert home.question_set_list.count() == 1
    assert "New sheet" in home.question_set_list.item(0).text()
    assert "Needs setup" in home.question_set_list.item(0).text()
