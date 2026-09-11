import pytest
from PySide6.QtCore import QPoint
from PySide6.QtGui import QColor, QPalette, QUndoStack
from PySide6.QtWidgets import QSizePolicy

from question_tracker.application.tracker_appearance import DEFAULT_APPEARANCE
from question_tracker.application.tracker_view import build_tracker_snapshot
from question_tracker.domain.tracking import QuestionSetKind, SetupState
from question_tracker.ui import tracker
from question_tracker.ui.colours import contrasting_text_colour


def test_topic_rows_and_spans_stay_aligned_across_zoom(
    qtbot, qapp, monkeypatch, tracking_service, library_service
):
    monkeypatch.setattr(tracker, "load_tracker_appearance", lambda: DEFAULT_APPEARANCE)
    question_set = tracking_service.create_question_set(
        QuestionSetKind.PROBLEM_SHEET, "Topics", setup_state=SetupState.READY
    )
    leaves = []
    for root_label, labels in (("1", ("a", "b", "c")), ("2", ("a", "b"))):
        root = tracking_service.create_question(question_set.id, root_label)
        leaves.extend(tracking_service.add_child(root.id, label) for label in labels)
    leaves.append(tracking_service.create_question(question_set.id, "3"))
    first = tracking_service.create_topic(question_set.id, "Discontinuous topic", colour="#123456")
    second = tracking_service.create_topic(question_set.id, "Replacement label", colour="#fedcba")
    for index in (0, 1, 4, 5):
        tracking_service.assign_topic(leaves[index].id, first.id)
    for index in (1, 2, 3):
        tracking_service.assign_topic(leaves[index].id, second.id)
    before = build_tracker_snapshot(tracking_service, question_set.id)
    saved_topics = tracking_service.list_topics(question_set.id)
    assert [(span.topic.id, span.start_column, span.end_column) for span in before.topic_spans] == [
        (first.id, 0, 0), (first.id, 4, 5), (second.id, 1, 3),
    ]
    assert before.columns[1].topic_ids == frozenset((second.id,))
    screen = tracker.TrackerScreen(
        tracking_service, library_service, QUndoStack(), initial_question_set_id=question_set.id
    )
    qtbot.addWidget(screen)
    screen.resize(900, 600)
    screen.show()
    for zoom in (50, 60, 100, 160):
        screen.change_zoom(zoom - screen.zoom_percent)
        qapp.processEvents()
        content = screen.tracker_scroll.widget()
        grid = content.layout()
        assert len(content.findChildren(tracker.TopicBandLabel)) == 3
        label_band_row = 1
        for span in before.topic_spans:
            band = content.findChild(
                tracker.TopicBandLabel,
                f"topicSpan-{span.topic.id}-{span.start_column}-{span.end_column}",
            )
            assert band.parentWidget() is content  # Shares the question scroll container.
            assert grid.getItemPosition(grid.indexOf(band)) == (
                label_band_row, span.start_column, 1,
                span.end_column - span.start_column + 1,
            )
            assert band.topic_colour.name() == span.topic.colour.lower()
            assert band.toolTip() == band.text() == span.topic.title
            assert band.height() == round(tracker.TRACKER_METRICS.topic_row_height * zoom / 100)
            assert band.font().pixelSize() == round(tracker.TRACKER_METRICS.topic_text_size * zoom / 100)
            assert band.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Ignored
        for span in before.hierarchy_headings:
            heading = screen.hierarchy_headings[span.question.id]
            assert grid.getItemPosition(grid.indexOf(heading)) == (
                2 + span.depth, span.start_column, 1, span.end_column - span.start_column + 1,
            )
        cells = [screen.question_cells[leaf.id] for leaf in leaves]
        for index, cell in enumerate(cells):
            assert grid.getItemPosition(grid.indexOf(cell)) == (4, index, 1, 1)
        assert len({cell.attempt_stack.mapTo(content, QPoint()).y() for cell in cells}) == 1
        assert build_tracker_snapshot(tracking_service, question_set.id) == before
        assert tracking_service.list_topics(question_set.id) == saved_topics


@pytest.mark.parametrize("colour", ["#ffffff", "#000000", "#123456", "#fedcba"])
def test_topic_colour_keeps_a_contrasting_outline(qtbot, qapp, colour):
    band = tracker.TopicBandLabel("Topic", colour, 1.0)
    qtbot.addWidget(band)
    # Worst case: the background matches the user's saved topic colour.
    palette = band.palette()
    palette.setColor(QPalette.ColorRole.Window, QColor(colour))
    band.setPalette(palette)
    band.resize(160, band.height())
    band.show()
    qapp.processEvents()
    assert band.topic_colour.name() == colour
    assert band.contrast_colour.name() == contrasting_text_colour(colour).lower()
    assert band.contrast_colour != band.topic_colour
    assert not band.autoFillBackground()
    assert band.styleSheet() == ""


@pytest.mark.parametrize("compact", [False, True])
def test_single_label_band_refreshes_replacement_and_clearing(qtbot, tracking_service, library_service, compact):
    from PySide6.QtWidgets import QLabel
    from question_tracker.ui.marking import MarkingWorkspace
    service = tracking_service
    question_set = service.create_question_set(QuestionSetKind.PROBLEM_SHEET, "Band", setup_state=SetupState.READY)
    leaves = [service.create_question(question_set.id, str(i)) for i in range(4)]
    labels = [service.create_topic(question_set.id, name) for name in ("Bases", "Vectors", "Eigenvectors")]
    for leaf, label in zip(leaves, (labels[0], labels[0], labels[1], labels[2])):
        service.set_question_label(leaf.id, label.id)
    cls = MarkingWorkspace if compact else tracker.TrackerScreen
    screen = cls(service, library_service, QUndoStack(), initial_question_set_id=question_set.id)
    qtbot.addWidget(screen)

    def verify(expected):
        if compact:
            screen.compact_tracker.refresh()
            content = screen.compact_tracker.strip._scroll.widget()
            prefix = "compactTopicSpan-"
            bands = [w for w in content.findChildren(QLabel) if w.objectName().startswith(prefix)]
        else:
            screen.refresh_current()
            content = screen.tracker_scroll.widget()
            bands = content.findChildren(tracker.TopicBandLabel)
        assert len(bands) == expected
        positions = [content.layout().getItemPosition(content.layout().indexOf(band)) for band in bands]
        assert len({pos[0] for pos in positions}) <= 1
        covered = [column for _, start, _, count in positions for column in range(start, start + count)]
        assert len(covered) == len(set(covered))

    verify(3)
    service.set_question_label(leaves[2].id, labels[0].id)
    verify(2)
    service.set_question_label(leaves[3].id, None)
    verify(1)
    for leaf in leaves:
        service.set_question_label(leaf.id, None)
    verify(0)
