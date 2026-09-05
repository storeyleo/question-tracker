from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QDrag, QUndoStack
from PySide6.QtWidgets import (
    QInputDialog, QLabel, QLineEdit, QPlainTextEdit, QSpinBox,
)

from question_tracker.application.library_service import LibraryService
from question_tracker.application.tracker_view import build_tracker_snapshot
from question_tracker.application.tracking_service import TrackingService, ValidationError
from question_tracker.domain.tracking import AttemptStatus, QuestionSetKind, SetupState
from question_tracker.persistence.database import Database
from question_tracker.persistence import migrations
from question_tracker.ui.question_editor import QuestionEditorDialog
from question_tracker.ui.quick_builder import QuickBuilderDialog
from question_tracker.ui.tracker import TrackerScreen


@pytest.fixture
def module_sheets(application_paths, library_service, tracking_service):
    for category, names in (
        ("Problem Sheets", ("A", "B", "C")), ("Past Papers", ("Exam",)),
    ):
        for name in names:
            path = application_paths.managed_library_root / "Year" / "Module" / category / f"{name}.pdf"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"%PDF-1.4\n%%EOF\n")
    library_service.scan_library()
    sheets = library_service.question_sets_for_module("Year", "Module")
    leaves = []
    for sheet, count in zip(sheets, (3, 1, 2, 2)):
        leaves.append(tuple(tracking_service.create_question(sheet.id, str(i + 1))
                            for i in range(count)))
        tracking_service.finish_question_set_setup(sheet.id)
    return sheets, leaves


@pytest.fixture
def screen(qtbot, module_sheets, tracking_service, library_service):
    tracker = TrackerScreen(
        tracking_service, library_service, QUndoStack(),
        initial_question_set_id=module_sheets[0][0].id,
    )
    qtbot.addWidget(tracker)
    tracker.resize(1000, 700)
    tracker.show()
    tracker.activateWindow()
    tracker.setFocus()
    qtbot.wait(20)
    return tracker


def test_topic_checkbox_click_and_titles_persist_after_reopening(
    qtbot, monkeypatch, tracking_service, application_paths,
):
    sheet = tracking_service.create_question_set(QuestionSetKind.PROBLEM_SHEET, "Sheet")
    first = tracking_service.create_question(sheet.id, "1")
    second = tracking_service.create_question(sheet.id, "2")
    topic = tracking_service.create_topic(sheet.id, "Original")
    dialog = QuestionEditorDialog(tracking_service, sheet.id)
    qtbot.addWidget(dialog)
    dialog.show()
    # A real keyboard toggle used to select PartiallyChecked and persist removal.
    dialog.assignment_list.setCurrentRow(0)
    qtbot.keyClick(dialog.assignment_list, Qt.Key.Key_Space)
    assert tracking_service.topic_ids_for_question(first.id) == (topic.id,)
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("Renamed", True))
    qtbot.mouseClick(dialog.rename_topic_button, Qt.MouseButton.LeftButton)
    dialog.question_tree.setCurrentItem(dialog.question_tree.topLevelItem(1))
    dialog.assignment_list.setCurrentRow(0)
    qtbot.keyClick(dialog.assignment_list, Qt.Key.Key_Space)
    dialog.close()

    reopened_db = Database(application_paths.database_path)
    reopened_db.initialize()
    reopened = TrackingService(reopened_db)
    assert reopened.get_topic(topic.id).title == "Renamed"
    assert reopened.topic_ids_for_question(first.id) == (topic.id,)
    assert reopened.topic_ids_for_question(second.id) == (topic.id,)
    editor = QuestionEditorDialog(reopened, sheet.id)
    qtbot.addWidget(editor)
    editor.assignment_list.setCurrentRow(0)
    qtbot.keyClick(editor.assignment_list, Qt.Key.Key_Space)
    editor.close()
    restarted = TrackingService(Database(application_paths.database_path))
    assert restarted.topic_ids_for_question(first.id) == ()
    assert restarted.topic_ids_for_question(second.id) == (topic.id,)


def test_bulk_topic_edit_is_atomic(tracking_service):
    sheet = tracking_service.create_question_set("problem_sheet", "Sheet")
    root = tracking_service.create_question(sheet.id, "1")
    leaf = tracking_service.add_child(root.id, "a")
    topic = tracking_service.create_topic(sheet.id, "Topic")
    with pytest.raises(ValidationError):
        tracking_service.set_topic_assignments((leaf.id, root.id), topic.id, True)
    assert tracking_service.topic_ids_for_question(leaf.id) == ()


def test_order_migration_upgrades_v1_without_losing_tracking_data(tmp_path):
    database = Database(tmp_path / "old.db")
    with database.connect() as connection:
        original = Path(migrations.__file__).with_name("sql_migrations") / "001_initial.sql"
        connection.executescript(original.read_text(encoding="utf-8"))
        connection.executescript("""
            CREATE TABLE schema_migrations (
                version INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL,
                applied_at_utc TEXT NOT NULL
            );
            INSERT INTO schema_migrations VALUES (1, '001_initial.sql', '2026-01-01');
            INSERT INTO question_sets VALUES (
                'set', 'problem_sheet', 'Original', 'Set note', 'ready', '2026', '2026'
            );
            INSERT INTO questions VALUES (
                'question', 'set', NULL, '1', 0, 'Question note', 1, '2026', '2026'
            );
            INSERT INTO topics VALUES ('topic', 'set', 'Legacy topic', 0);
            INSERT INTO attempts VALUES (
                'attempt', 'question', 'correct', '2026-01-01T00:00:00Z',
                'Attempt note', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z'
            );
        """)
    database.initialize()
    database.initialize()
    service = TrackingService(database)
    assert service.get_question_set("set").display_order is None
    assert service.get_question_set("set").general_note == "Set note"
    assert service.get_question("question").bookmarked
    assert service.get_question("question").note == "Question note"
    assert service.get_attempt("attempt").note == "Attempt note"
    assert service.get_topic("topic").colour == "#D9EAF7"


def test_drag_rejects_other_categories_and_modules(screen, module_sheets):
    sheets, _ = module_sheets
    tree = screen.navigation_tree
    items = {item.data(0, Qt.ItemDataRole.UserRole): item for item in tree.findItems(
        "*", Qt.MatchFlag.MatchWildcard | Qt.MatchFlag.MatchRecursive
    )}
    assert not tree.move_relative(items[sheets[0].id], items[sheets[-1].id], after=False)


def test_sheet_order_persists_across_restart_and_rename(
    module_sheets, library_service, tracking_service, application_paths,
):
    sheets, _ = module_sheets
    before_paths = {f.id: f.relative_path for f in library_service.list_files()}
    library_service.reorder_question_set(sheets[2].id, 0)
    tracking_service.rename_question_set(sheets[2].id, "Z renamed")
    restarted_db = Database(application_paths.database_path)
    restarted_db.initialize()
    restarted = LibraryService(restarted_db, application_paths.managed_library_root)
    restarted.scan_library()
    assert [s.id for s in restarted.question_sets_for_module("Year", "Module")] == [
        sheets[2].id, sheets[0].id, sheets[1].id, sheets[3].id,
    ]
    assert {f.id: f.relative_path for f in restarted.list_files()} == before_paths


def test_drag_drop_handler_persists_same_category_order(
    screen, module_sheets, library_service, monkeypatch,
):
    sheets, _ = module_sheets
    tree = screen.navigation_tree
    items = {item.data(0, Qt.ItemDataRole.UserRole): item for item in tree.findItems(
        "*", Qt.MatchFlag.MatchWildcard | Qt.MatchFlag.MatchRecursive
    )}
    source, target = items[sheets[2].id], items[sheets[0].id]
    tree.setCurrentItem(source)
    target_position = QPointF(tree.visualItemRect(target).center())

    class Drop:
        accepted = False

        def source(self): return tree
        def position(self): return target_position
        def setDropAction(self, action): assert action == Qt.DropAction.MoveAction
        def accept(self): self.accepted = True
        def ignore(self): self.accepted = False

    event = Drop()

    def complete_drag(drag, action):
        assert drag.source() is tree
        tree.dropEvent(event)
        return Qt.DropAction.MoveAction

    monkeypatch.setattr(QDrag, "exec", complete_drag)
    tree.startDrag(Qt.DropAction.MoveAction)
    assert event.accepted
    assert [s.id for s in library_service.question_sets_for_module("Year", "Module")][:3] == [
        sheets[2].id, sheets[0].id, sheets[1].id,
    ]
    assert [s.question_set.id for s in screen.module_snapshot.question_sets][:3] == [
        sheets[2].id, sheets[0].id, sheets[1].id,
    ]
    surviving_ids = {item.data(0, Qt.ItemDataRole.UserRole) for item in tree.findItems(
        "*", Qt.MatchFlag.MatchWildcard | Qt.MatchFlag.MatchRecursive
    ) if item.data(0, Qt.ItemDataRole.UserRole)}
    assert surviving_ids == {sheet.id for sheet in sheets}


def test_horizontal_and_vertical_navigation_clamps(screen, module_sheets, qtbot):
    sheets, leaves = module_sheets
    screen.select_question(leaves[0][-1].id)
    qtbot.keyClick(screen, Qt.Key.Key_Right)
    assert screen.selected_question_id == leaves[0][-1].id
    qtbot.keyClick(screen, Qt.Key.Key_Left)
    assert screen.selected_question_id == leaves[0][1].id
    qtbot.keyClick(screen, Qt.Key.Key_Down)
    assert screen.selected_question_id == leaves[1][0].id
    qtbot.keyClick(screen, Qt.Key.Key_Up)
    assert screen.selected_question_id == leaves[0][0].id
    qtbot.keyClick(screen, Qt.Key.Key_Up)
    assert screen.current_question_set_id == sheets[0].id
    screen.select_question(leaves[-1][-1].id)
    qtbot.keyClick(screen, Qt.Key.Key_Down)
    assert screen.selected_question_id == leaves[-1][-1].id


@pytest.mark.parametrize("key,status", list(zip("1234", AttemptStatus)))
def test_keyboard_marking_advances_but_stops_at_last_leaf(
    screen, module_sheets, tracking_service, qtbot, key, status,
):
    _, leaves = module_sheets
    screen.select_question(leaves[0][0].id)
    qtbot.keyClicks(screen, key)
    assert screen.selected_question_id == leaves[0][1].id
    assert tracking_service.attempts_for_question(leaves[0][0].id)[0].status is status
    screen.select_question(leaves[0][-1].id)
    qtbot.keyClicks(screen, key * 2)
    assert len(tracking_service.attempts_for_question(leaves[0][-1].id)) == 2
    assert screen.selected_question_id == leaves[0][-1].id


@pytest.mark.parametrize("editor_type", (QLineEdit, QPlainTextEdit, QSpinBox))
def test_text_and_numeric_input_keeps_shortcuts(screen, qtbot, tracking_service, editor_type):
    editor = editor_type(screen)
    screen.layout().addWidget(editor)
    editor.show()
    editor.setFocus()
    qtbot.wait(10)
    selected = screen.selected_question_id
    qtbot.keyClicks(editor, "1234")
    assert tracking_service.attempts_for_question(selected) == ()
    assert screen.selected_question_id == selected
    if isinstance(editor, QLineEdit):
        assert editor.text() == "1234"
    elif isinstance(editor, QPlainTextEdit):
        assert editor.toPlainText() == "1234"
    else:
        assert editor.value() > 0


def test_module_selection_keeps_other_sheets_visible_and_default_zoom(screen, module_sheets):
    sheets, _ = module_sheets
    headings = [screen.findChild(QLabel, f"questionSetHeading-{s.id}") for s in sheets]
    assert all(headings)
    screen.load_question_set(sheets[2].id)
    assert screen.current_question_set_id == sheets[2].id
    assert all(screen.findChild(QLabel, f"questionSetHeading-{s.id}") is h
               for s, h in zip(sheets, headings))
    assert screen.zoom_percent == 60
    assert screen.zoom_label.text() == "60%"


def test_parent_ranges_and_depths_derive_from_structure(tracking_service):
    sheet = tracking_service.create_question_set("problem_sheet", "Hierarchy")
    root = tracking_service.create_question(sheet.id, "custom root")
    a = tracking_service.add_child(root.id, "custom child")
    b = tracking_service.add_sibling(a.id, "b")
    i = tracking_service.add_child(a.id, "i")
    ii = tracking_service.add_sibling(i.id, "ii")
    snapshot = build_tracker_snapshot(tracking_service, sheet.id)
    assert [(s.question.id, s.start_column, s.end_column, s.depth)
            for s in snapshot.parent_spans] == [(root.id, 0, 2, 0), (a.id, 0, 1, 1)]
    assert [(c.question.id, c.depth) for c in snapshot.columns] == [(i.id, 2), (ii.id, 2), (b.id, 1)]


def test_leaf_grid_placement_uses_one_common_baseline(
    qtbot, tracking_service, library_service
):
    sheet = tracking_service.create_question_set("problem_sheet", "Hierarchy")
    tracking_service.append_question_structure(sheet.id, ((2, 0),))
    tracking_service.finish_question_set_setup(sheet.id)
    tracker = TrackerScreen(tracking_service, library_service, QUndoStack(),
                            initial_question_set_id=sheet.id)
    qtbot.addWidget(tracker)
    columns = tracker.snapshot.columns
    grid = tracker.tracker_scroll.widget().layout()
    positions = [grid.getItemPosition(grid.indexOf(tracker.question_cells[c.question.id]))
                 for c in columns]
    assert len({position[0] for position in positions}) == 1


def test_toolbar_controls_affect_only_selected_leaf(
    screen, module_sheets, qtbot, monkeypatch, tracking_service,
):
    _, leaves = module_sheets
    selected = leaves[1][0]
    screen.select_question(selected.id)
    monkeypatch.setattr(QInputDialog, "getMultiLineText", lambda *a, **k: ("Saved note", True))
    qtbot.mouseClick(screen.bookmark_button, Qt.MouseButton.LeftButton)
    qtbot.mouseClick(screen.note_button, Qt.MouseButton.LeftButton)
    assert tracking_service.get_question(selected.id).bookmarked
    assert tracking_service.get_question(selected.id).note == "Saved note"
    assert not tracking_service.get_question(leaves[0][0].id).bookmarked
    assert screen.bookmark_button.text() == "★ Bookmarked"
    screen.select_question(leaves[0][0].id)
    assert screen.bookmark_button.text() == "☆ Bookmark"


def test_quick_builder_creates_hierarchy_and_preserves_manual_questions(qtbot, tracking_service):
    sheet = tracking_service.create_question_set("problem_sheet", "Builder")
    old = tracking_service.create_question(sheet.id, "1")
    attempt = tracking_service.add_attempt(old.id, "correct")
    builder = QuickBuilderDialog(tracking_service, sheet.id)
    qtbot.addWidget(builder)
    builder.main_count.setValue(2)
    builder.child_counts[0].setValue(2)
    builder.grandchild_counts[0][0].setValue(3)
    qtbot.mouseClick(builder.build_button, Qt.MouseButton.LeftButton)
    snapshot = build_tracker_snapshot(tracking_service, sheet.id)
    assert [c.visible_label for c in snapshot.columns] == ["1", "2.a)i", "2.a)ii", "2.a)iii", "2.b", "3"]
    assert tracking_service.get_attempt(attempt.id).question_id == old.id
    assert tracking_service.get_question_set(sheet.id).setup_state is SetupState.NEEDS_SETUP
    manual = QuestionEditorDialog(tracking_service, sheet.id)
    qtbot.addWidget(manual)
    assert manual.question_tree.topLevelItemCount() == 3
