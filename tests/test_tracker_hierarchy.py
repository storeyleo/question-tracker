import pytest
from PySide6.QtCore import QPoint
from PySide6.QtGui import QUndoStack

from question_tracker.application.tracker_appearance import DEFAULT_APPEARANCE
from question_tracker.application.tracker_view import build_tracker_snapshot
from question_tracker.domain.tracking import AttemptStatus, QuestionSetKind, SetupState
from question_tracker.ui import tracker


@pytest.fixture
def mixed_tracker(qtbot, tracking_service, library_service, monkeypatch):
    monkeypatch.setattr(tracker, "load_tracker_appearance", lambda: DEFAULT_APPEARANCE)
    question_set = tracking_service.create_question_set(
        QuestionSetKind.PROBLEM_SHEET, "Mixed", setup_state=SetupState.READY
    )
    questions = {}
    structure = {
        "1": {"a": (), "b": (), "c": ("i", "ii"), "d": ()},
        "2": {"a": (), "b": ("i", "ii")},
        "3": {},
        "4": {"a": (), "b": ()},
    }
    for root_label, children in structure.items():
        root = tracking_service.create_question(question_set.id, root_label)
        questions[root_label] = root
        for label, grandchildren in children.items():
            child = tracking_service.add_child(root.id, label)
            questions[f"{root_label}.{label}"] = child
            for grandchild_label in grandchildren:
                grandchild = tracking_service.add_child(child.id, grandchild_label)
                questions[f"{root_label}.{label}.{grandchild_label}"] = grandchild
    leaf_order = [
        "1.a", "1.b", "1.c.i", "1.c.ii", "1.d",
        "2.a", "2.b.i", "2.b.ii", "3", "4.a", "4.b",
    ]
    # Different history heights must not change the common stack origin.
    for label, count in (("1.a", 1), ("1.c.ii", 3), ("3", 2)):
        for _ in range(count):
            tracking_service.add_attempt(questions[label].id, AttemptStatus.CORRECT)
    topic = tracking_service.create_topic(question_set.id, "Topic")
    for label in ("1.a", "1.b", "1.c.ii"):
        tracking_service.assign_topic(questions[label].id, topic.id)
    screen = tracker.TrackerScreen(
        tracking_service, library_service, QUndoStack(),
        initial_question_set_id=question_set.id,
    )
    qtbot.addWidget(screen)
    screen.resize(900, 600)
    screen.show()
    return screen, questions, leaf_order, topic


def test_mixed_hierarchy_spans_order_and_topics(mixed_tracker):
    screen, questions, leaf_order, topic = mixed_tracker
    snapshot = screen.module_snapshot.question_sets[0]
    assert [column.question.id for column in snapshot.columns] == [
        questions[label].id for label in leaf_order
    ]
    expected = {
        "1": (0, 0, 4), "2": (0, 5, 7), "3": (0, 8, 8), "4": (0, 9, 10),
        "1.a": (1, 0, 0), "1.b": (1, 1, 1), "1.c": (1, 2, 3), "1.d": (1, 4, 4),
        "2.a": (1, 5, 5), "2.b": (1, 6, 7), "4.a": (1, 9, 9), "4.b": (1, 10, 10),
    }
    assert {
        span.question.id: (span.depth, span.start_column, span.end_column)
        for span in snapshot.hierarchy_headings
    } == {questions[label].id: span for label, span in expected.items()}
    assert snapshot.heading_row_count == 2
    assert [(span.topic.id, span.start_column, span.end_column) for span in snapshot.topic_spans] == [
        (topic.id, 0, 1), (topic.id, 3, 3),
    ]
    for label in ("1.a", "1.b", "1.d", "2.a", "4.a", "4.b"):
        assert screen.hierarchy_headings[questions[label].id].text() == questions[label].local_label
    for label in ("1.c.i", "1.c.ii", "2.b.i", "2.b.ii"):
        assert questions[label].id not in screen.hierarchy_headings
        assert screen.question_cells[questions[label].id].label_button.isHidden()


def test_hierarchy_geometry_tracks_columns_at_each_zoom(mixed_tracker, qapp):
    screen, questions, leaf_order, _ = mixed_tracker
    original_snapshot = screen.module_snapshot.question_sets[0]
    original_topics = original_snapshot.topic_spans
    original_headings = original_snapshot.hierarchy_headings
    for zoom in (50, 60, 100, 160):
        screen.change_zoom(zoom - screen.zoom_percent)
        qapp.processEvents()
        content = screen.tracker_scroll.widget()
        grid = content.layout()
        cells = [screen.question_cells[questions[label].id] for label in leaf_order]
        snapshot = screen.module_snapshot.question_sets[0]
        assert snapshot.hierarchy_headings == original_headings
        assert set(screen.hierarchy_headings) == {span.question.id for span in original_headings}
        assert [column.question.id for column in snapshot.columns] == [
            questions[label].id for label in leaf_order
        ]
        for index, cell in enumerate(cells):
            assert grid.getItemPosition(grid.indexOf(cell))[1:] == (index, 1, 1)
        assert len({cell.attempt_stack.mapTo(content, QPoint()).y() for cell in cells}) == 1
        attempt_rows = {grid.getItemPosition(grid.indexOf(cell))[0] for cell in cells}
        assert len(attempt_rows) == 1  # Includes the root-level leaf, question 3.
        heading_rows = {0: set(), 1: set()}
        for span in snapshot.hierarchy_headings:
            heading = screen.hierarchy_headings[span.question.id]
            row, column, row_span, column_span = grid.getItemPosition(grid.indexOf(heading))
            heading_rows[span.depth].add(row)
            count = span.end_column - span.start_column + 1
            assert (column, row_span, column_span) == (span.start_column, 1, count)
            # Grid placement is the contract: spanning and fixed-width widgets
            # can receive different integer-pixel allocations.
            assert heading.visible_span_rect().width() > 0
        assert len(heading_rows[0]) == len(heading_rows[1]) == 1
        root_row = min(heading_rows[0])
        assert heading_rows[1] == {root_row + 1}
        assert attempt_rows == {root_row + snapshot.heading_row_count}
        assert snapshot.topic_spans == original_topics
        assert build_tracker_snapshot(screen.service, screen.current_question_set_id).topic_spans == original_topics

    # A long local label must be elided, not widen its descendant columns.
    heading = screen.hierarchy_headings[questions["3"].id]
    width = heading.width()
    heading.setText("A long local question label " * 10)
    qapp.processEvents()
    assert heading.width() == width
