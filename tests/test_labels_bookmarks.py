import sqlite3

import pytest
from PySide6.QtGui import QUndoStack
from PySide6.QtWidgets import QInputDialog, QMessageBox, QToolButton

from question_tracker.application.tracking_service import TrackingService, TrackingError
from question_tracker.application.review_service import ReviewFilter, ReviewService
from question_tracker.domain.tracking import AttemptStatus, QuestionSetKind, SetupState
from question_tracker.persistence.database import Database
from question_tracker.ui.bookmark_controls import BookmarkTypesDialog
from question_tracker.ui.tracker_commands import SetBookmarkCommand


def seed(service):
    question_set = service.create_question_set(QuestionSetKind.PROBLEM_SHEET, "Labels", setup_state=SetupState.READY)
    question = service.create_question(question_set.id, "1")
    return question_set, question


def test_single_label_atomic_replacement_and_integrity(tracking_service, database):
    service = tracking_service
    question_set, question = seed(service)
    first = service.create_topic(question_set.id, "First")
    second = service.create_topic(question_set.id, "Second")
    assert service.get_question_label(question.id) is None
    service.set_question_note(question.id, "Keep this note")
    service.set_question_bookmark(question.id, True)
    attempt = service.add_attempt(question.id, AttemptStatus.PARTIAL)
    before = service.get_question(question.id)
    service.set_question_label(question.id, first.id)
    assert service.get_question_label(question.id) == first
    service.set_question_label(question.id, second.id)
    assert service.topic_ids_for_question(question.id) == (second.id,)
    with pytest.raises(sqlite3.IntegrityError), database.transaction() as connection:
        connection.execute("INSERT INTO question_topics VALUES (?,?,?)", (question.id, first.id, question_set.id))
    other_set, _ = seed(service)
    foreign = service.create_topic(other_set.id, "Foreign")
    for bad_id in (foreign.id, "missing"):
        with pytest.raises(TrackingError):
            service.set_question_label(question.id, bad_id)
        assert service.get_question_label(question.id) == second
    with pytest.raises(TrackingError):
        service.set_question_labels((question.id, "missing"), first.id)
    assert service.get_question_label(question.id) == second
    reopened = TrackingService(Database(database.path))
    assert reopened.get_question_label(question.id) == second
    assert reopened.get_question(question.id) == before
    assert reopened.get_attempt(attempt.id) == attempt
    service.set_question_label(question.id, None)
    assert service.get_question_label(question.id) is None
    service.set_question_label(question.id, first.id)
    service.delete_topic(first.id)
    assert service.get_question_label(question.id) is None


def test_v5_label_and_boolean_bookmark_migration(tracking_service, database):
    service = tracking_service
    question_set, zero = seed(service)
    one = service.create_question(question_set.id, "2")
    many = service.create_question(question_set.id, "3")
    parent = service.create_question(question_set.id, "4")
    child = service.add_child(parent.id, "a")
    first = service.create_topic(question_set.id, "First")
    second = service.create_topic(question_set.id, "Second")
    service.set_question_note(many.id, "Migration note")
    attempt = service.add_attempt(many.id, AttemptStatus.CORRECT, note="Attempt note")
    # Reconstruct precisely the v5 assignment/boolean model, without calling
    # new bookmark/label APIs against an old schema.
    with database.transaction() as connection:
        connection.execute("DROP TABLE question_bookmarks")
        connection.execute("DROP TABLE bookmark_types")
        connection.execute("DROP INDEX uq_question_single_label")
        connection.execute("DELETE FROM schema_migrations WHERE version=6")
        connection.executemany("INSERT INTO question_topics VALUES (?,?,?)", [
            (one.id, second.id, question_set.id),
            (many.id, second.id, question_set.id),
            (many.id, first.id, question_set.id),
        ])
        connection.execute("UPDATE questions SET bookmarked=1 WHERE id IN (?,?)", (many.id, parent.id))
    before = service.list_questions(question_set.id)
    database.initialize()
    database.initialize()
    assert service.get_question_label(zero.id) is None
    assert service.get_question_label(one.id) == second
    assert service.get_question_label(many.id) == first
    assert service.list_topics(question_set.id) == (first, second)
    assert service.list_questions(question_set.id) == before
    assert service.get_attempt(attempt.id) == attempt
    assert service.get_question(child.id).parent_id == parent.id
    assert [t.name for t in service.get_question_bookmarks(many.id)] == ["Bookmark"]
    assert [t.name for t in service.get_question_bookmarks(parent.id)] == ["Bookmark"]
    assert service.get_question_bookmarks(zero.id) == ()
    with database.read() as connection:
        assert not connection.execute("PRAGMA foreign_key_check").fetchall()


def test_bookmark_types_lifecycle_queries_and_persistence(tracking_service, database):
    service = tracking_service
    question_set, question = seed(service)
    other = service.create_question(question_set.id, "2")
    defaults = service.list_bookmark_types()
    assert [t.name for t in defaults] == ["Bookmark", "Revisit", "Important"]
    custom = service.create_bookmark_type("  Useful  ")
    renamed = service.rename_bookmark_type(custom.id, "Reference")
    assert renamed.id == custom.id
    for name in ("", "   ", "BOOKMARK", " reference "):
        with pytest.raises(TrackingError):
            service.create_bookmark_type(name)
    with pytest.raises(TrackingError):
        service.rename_bookmark_type(custom.id, "BOOKMARK")
    for item in (defaults[0], defaults[1], custom):
        service.set_question_bookmark(question.id, True, bookmark_type_id=item.id)
        service.set_question_bookmark(question.id, True, bookmark_type_id=item.id)
    service.set_question_bookmark(other.id, True, bookmark_type_id=defaults[1].id)
    assert len(service.get_question_bookmarks(question.id)) == 3
    assert {q.id for q in service.list_bookmarked_questions()} == {question.id, other.id}
    assert {q.id for q in service.list_bookmarked_questions(custom.id)} == {question.id}
    reopened = TrackingService(Database(database.path))
    assert reopened.get_question_bookmarks(question.id) == service.get_question_bookmarks(question.id)
    service.delete_bookmark_type(defaults[2].id)  # Unused.
    service.delete_bookmark_type(custom.id)  # Used; other markers survive.
    assert len(service.get_question_bookmarks(question.id)) == 2
    service.set_question_bookmark(question.id, False)
    assert service.get_question(question.id).bookmarked
    service.set_question_bookmark(question.id, False, bookmark_type_id=defaults[1].id)
    assert not service.get_question(question.id).bookmarked
    assert service.get_question(other.id).bookmarked
    with pytest.raises(TrackingError):
        service.set_question_bookmark(other.id, True, bookmark_type_id="missing")
    service.delete_question_permanently(other.id)
    assert not service.list_bookmarked_questions()
    assert service.get_question(question.id).id == question.id


def test_split_preserves_every_bookmark_type(tracking_service):
    service = tracking_service
    question_set, parent = seed(service)
    for item in service.list_bookmark_types():
        service.set_question_bookmark(parent.id, True, bookmark_type_id=item.id)
    label = service.create_topic(question_set.id, "Label")
    service.set_question_label(parent.id, label.id)
    child = service.add_child(parent.id, "a")
    assert service.get_question_bookmarks(child.id) == service.list_bookmark_types()
    assert service.get_question_bookmarks(parent.id) == ()
    assert service.get_question_label(child.id) == label


def test_bookmark_undo_redo_affects_only_requested_type(qtbot, tracking_service):
    service = tracking_service
    _, question = seed(service)
    first, second, _ = service.list_bookmark_types()
    service.set_question_bookmark(question.id, True, bookmark_type_id=first.id)
    stack = QUndoStack()
    stack.push(SetBookmarkCommand(service, question.id, False, True, bookmark_type_id=second.id))
    assert len(service.get_question_bookmarks(question.id)) == 2
    stack.undo()
    assert service.get_question_bookmarks(question.id) == (first,)
    stack.redo()
    stack.push(SetBookmarkCommand(service, question.id, True, False, bookmark_type_id=second.id))
    assert service.get_question_bookmarks(question.id) == (first,)
    stack.undo()
    assert len(service.get_question_bookmarks(question.id)) == 2
    stack.redo()
    assert service.get_question_bookmarks(question.id) == (first,)


def test_review_any_and_individual_bookmark_filters(tracking_service, database):
    service = tracking_service
    question_set, first = seed(service)
    second = service.create_question(question_set.id, "2")
    types = service.list_bookmark_types()
    service.set_question_bookmark(first.id, True, bookmark_type_id=types[1].id)
    service.set_question_bookmark(second.id, True, bookmark_type_id=types[2].id)
    summary = ReviewService(database).snapshot()[0]
    assert {i.question.id for i in summary.review_items(ReviewFilter.BOOKMARKED)} == {first.id, second.id}
    assert [i.question.id for i in summary.review_items(f"bookmark:{types[1].id}")] == [first.id]
    assert summary.counts.bookmarked == 2


@pytest.mark.parametrize("workspace", ["tracker", "marking"])
def test_bookmark_menu_and_quick_toggle_preserve_other_types(qtbot, tracking_service, library_service, workspace):
    from question_tracker.ui.tracker import TrackerScreen
    from question_tracker.ui.marking import MarkingWorkspace
    service = tracking_service
    question_set, question = seed(service)
    stack = QUndoStack()
    cls = TrackerScreen if workspace == "tracker" else MarkingWorkspace
    screen = cls(service, library_service, stack, initial_question_set_id=question_set.id)
    qtbot.addWidget(screen)
    target = screen if workspace == "tracker" else screen.compact_tracker
    target.select_question(question.id)
    assert target.bookmark_button.popupMode() == QToolButton.ToolButtonPopupMode.MenuButtonPopup
    target.bookmark_button.click()
    assert [t.name for t in service.get_question_bookmarks(question.id)] == ["Bookmark"]
    target.bookmark_button.click()
    assert service.get_question_bookmarks(question.id) == ()
    assert "☆" in target.bookmark_button.text()

    def toggle_important(expected_checked):
        menu = target.bookmark_button.menu()
        menu.aboutToShow.emit()  # Populate the actual dropdown without a native popup.
        action = next(action for action in menu.actions() if action.text() == "Important")
        assert action.isCheckable()
        assert action.isChecked() == expected_checked
        assert not menu.isVisible()
        action.trigger()

    toggle_important(False)
    assert [t.name for t in service.get_question_bookmarks(question.id)] == ["Important"]
    assert "Other bookmarks" in target.bookmark_button.text()
    assert "Active: Important" in target.bookmark_button.toolTip()
    target.bookmark_button.click()
    assert [t.name for t in service.get_question_bookmarks(question.id)] == ["Bookmark", "Important"]
    target.bookmark_button.click()
    assert [t.name for t in service.get_question_bookmarks(question.id)] == ["Important"]
    stack.undo()
    assert [t.name for t in service.get_question_bookmarks(question.id)] == ["Bookmark", "Important"]
    stack.redo()
    assert [t.name for t in service.get_question_bookmarks(question.id)] == ["Important"]
    target.bookmark_button.click()
    assert [t.name for t in service.get_question_bookmarks(question.id)] == ["Bookmark", "Important"]
    toggle_important(True)
    assert [t.name for t in service.get_question_bookmarks(question.id)] == ["Bookmark"]
    stack.undo()
    assert [t.name for t in service.get_question_bookmarks(question.id)] == ["Bookmark", "Important"]
    menu = target.bookmark_button.menu()
    menu.aboutToShow.emit()
    assert {a.text() for a in menu.actions() if a.isChecked()} == {"Bookmark", "Important"}
    other = service.create_question(question_set.id, "2")
    if workspace == "tracker":
        target.refresh_current()
    else:
        target.refresh()
    target.select_question(other.id)
    menu.aboutToShow.emit()
    assert not any(a.isChecked() for a in menu.actions())
    assert "Active: None" in target.bookmark_button.toolTip()
    target.select_question(question.id)
    menu.aboutToShow.emit()
    assert {a.text() for a in menu.actions() if a.isChecked()} == {"Bookmark", "Important"}


def test_type_management_dialog(qtbot, monkeypatch, tracking_service):
    _, question = seed(tracking_service)
    dialog = BookmarkTypesDialog(tracking_service, QUndoStack())
    qtbot.addWidget(dialog)
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **kw: ("Custom", True))
    dialog.create()
    dialog.list.setCurrentRow(dialog.list.count() - 1)
    type_id = dialog.selected().id
    tracking_service.set_question_bookmark(question.id, True, bookmark_type_id=type_id)
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **kw: ("Renamed", True))
    dialog.rename()
    assert tracking_service.get_question_bookmarks(question.id)[0].name == "Renamed"
    dialog.list.setCurrentRow(dialog.list.count() - 1)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **kw: QMessageBox.StandardButton.Yes)
    dialog.delete()
    assert tracking_service.get_question_bookmarks(question.id) == ()
    assert tracking_service.get_question(question.id).id == question.id


def test_editor_default_checkbox_does_not_clear_other_types(qtbot, tracking_service):
    from question_tracker.ui.question_editor import QuestionEditorDialog
    question_set, question = seed(tracking_service)
    important = tracking_service.list_bookmark_types()[2]
    tracking_service.set_question_bookmark(question.id, True, bookmark_type_id=important.id)
    editor = QuestionEditorDialog(tracking_service, question_set.id)
    qtbot.addWidget(editor)
    assert not editor.bookmark_check.isChecked()
    editor.bookmark_check.setChecked(True)
    assert len(tracking_service.get_question_bookmarks(question.id)) == 2
    editor.bookmark_check.setChecked(False)
    assert tracking_service.get_question_bookmarks(question.id) == (important,)
