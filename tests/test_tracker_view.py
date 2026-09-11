from __future__ import annotations

from datetime import UTC, datetime, timedelta

from question_tracker.application.tracker_view import build_tracker_snapshot
from question_tracker.application.tracking_service import TrackingService
from question_tracker.domain.tracking import AttemptStatus, QuestionSetKind, SetupState


BASE_TIME = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def _ready_set(service: TrackingService):
    return service.create_question_set(
        QuestionSetKind.PROBLEM_SHEET,
        "Sheet 1",
        setup_state=SetupState.READY,
    )


def test_snapshot_flattens_only_leaves_in_visible_order_with_derived_labels(
    tracking_service: TrackingService,
) -> None:
    question_set = _ready_set(tracking_service)
    first = tracking_service.create_question(question_set.id, "1")
    second = tracking_service.create_question(question_set.id, "2")
    third = tracking_service.create_question(question_set.id, "3")
    first_a = tracking_service.add_child(first.id, "a")
    first_b = tracking_service.add_sibling(first_a.id, "b")
    second_a = tracking_service.add_child(second.id, "a")
    second_ai = tracking_service.add_child(second_a.id, "i")
    second_aii = tracking_service.add_sibling(second_ai.id, "ii")

    snapshot = build_tracker_snapshot(tracking_service, question_set.id)

    assert [column.question.id for column in snapshot.columns] == [
        first_a.id,
        first_b.id,
        second_ai.id,
        second_aii.id,
        third.id,
    ]
    assert [column.visible_label for column in snapshot.columns] == [
        "1.a",
        "1.b",
        "2.a)i",
        "2.a)ii",
        "3",
    ]


def test_snapshot_builds_visual_parent_spans_over_descendant_leaves(
    tracking_service: TrackingService,
) -> None:
    question_set = _ready_set(tracking_service)
    first = tracking_service.create_question(question_set.id, "1")
    first_a = tracking_service.add_child(first.id, "a")
    tracking_service.add_sibling(first_a.id, "b")
    second = tracking_service.create_question(question_set.id, "2")
    second_a = tracking_service.add_child(second.id, "a")
    second_ai = tracking_service.add_child(second_a.id, "i")
    tracking_service.add_sibling(second_ai.id, "ii")

    snapshot = build_tracker_snapshot(tracking_service, question_set.id)

    assert [
        (span.visible_label, span.depth, span.start_column, span.end_column)
        for span in snapshot.parent_spans
    ] == [
        ("1", 0, 0, 1),
        ("2", 0, 2, 3),
        ("2.a", 1, 2, 3),
    ]
    assert snapshot.parent_row_count == 2


def test_snapshot_uses_fixed_root_and_second_level_heading_rows(
    tracking_service: TrackingService,
) -> None:
    question_set = _ready_set(tracking_service)
    first = tracking_service.create_question(question_set.id, "1")
    first_a = tracking_service.add_child(first.id, "a")
    first_b = tracking_service.add_sibling(first_a.id, "b")
    first_bi = tracking_service.add_child(first_b.id, "i")
    first_bii = tracking_service.add_sibling(first_bi.id, "ii")
    second = tracking_service.create_question(question_set.id, "2")

    snapshot = build_tracker_snapshot(tracking_service, question_set.id)

    assert snapshot.heading_row_count == 2
    assert [
        (
            heading.question.id,
            heading.depth,
            heading.start_column,
            heading.end_column,
            heading.is_leaf,
        )
        for heading in snapshot.hierarchy_headings
    ] == [
        (first.id, 0, 0, 2, False),
        (second.id, 0, 3, 3, True),
        (first_a.id, 1, 0, 0, True),
        (first_b.id, 1, 1, 2, False),
    ]
    assert first_bi.id not in {
        heading.question.id for heading in snapshot.hierarchy_headings
    }
    assert first_bii.id not in {
        heading.question.id for heading in snapshot.hierarchy_headings
    }
    flat_set = _ready_set(tracking_service)
    tracking_service.create_question(flat_set.id, "1")
    assert build_tracker_snapshot(
        tracking_service, flat_set.id
    ).heading_row_count == snapshot.heading_row_count


def test_topic_assignments_form_contiguous_and_discontinuous_spans(
    tracking_service: TrackingService,
) -> None:
    question_set = _ready_set(tracking_service)
    leaves = [
        tracking_service.create_question(question_set.id, str(index))
        for index in range(1, 6)
    ]
    bases = tracking_service.create_topic(
        question_set.id, "Bases", colour="#123456"
    )
    proofs = tracking_service.create_topic(question_set.id, "Proofs")
    for index in (0, 1, 3, 4):
        tracking_service.assign_topic(leaves[index].id, bases.id)
    for index in (1, 2):
        tracking_service.assign_topic(leaves[index].id, proofs.id)

    snapshot = build_tracker_snapshot(tracking_service, question_set.id)

    assert [
        (span.topic.id, span.start_column, span.end_column)
        for span in snapshot.topic_spans
    ] == [
        (bases.id, 0, 0),
        (bases.id, 3, 4),
        (proofs.id, 1, 2),
    ]
    assert snapshot.columns[1].topic_ids == frozenset({proofs.id})
    assert snapshot.topic_spans[0].topic.colour == "#123456"


def test_attempt_view_orders_history_and_identifies_latest_and_unattempted(
    tracking_service: TrackingService,
) -> None:
    question_set = _ready_set(tracking_service)
    attempted = tracking_service.create_question(question_set.id, "1")
    unattempted = tracking_service.create_question(question_set.id, "2")
    oldest = tracking_service.add_attempt(
        attempted.id, AttemptStatus.INCORRECT, timestamp=BASE_TIME
    )
    newest = tracking_service.add_attempt(
        attempted.id,
        AttemptStatus.CORRECT,
        timestamp=BASE_TIME + timedelta(minutes=2),
    )
    middle = tracking_service.add_attempt(
        attempted.id,
        AttemptStatus.PARTIAL,
        timestamp=BASE_TIME + timedelta(minutes=1),
    )

    snapshot = build_tracker_snapshot(tracking_service, question_set.id)

    assert [view.attempt.id for view in snapshot.columns[0].attempts] == [
        oldest.id,
        middle.id,
        newest.id,
    ]
    assert [view.attempt.id for view in snapshot.columns[0].attempts if view.is_latest] == [
        newest.id
    ]
    assert snapshot.columns[0].is_unattempted is False
    assert snapshot.columns[1].question.id == unattempted.id
    assert snapshot.columns[1].is_unattempted is True


def test_deleting_latest_attempt_exposes_previous_latest(
    tracking_service: TrackingService,
) -> None:
    question_set = _ready_set(tracking_service)
    question = tracking_service.create_question(question_set.id, "1")
    previous = tracking_service.add_attempt(
        question.id, AttemptStatus.PARTIAL, timestamp=BASE_TIME
    )
    latest = tracking_service.add_attempt(
        question.id,
        AttemptStatus.CORRECT,
        timestamp=BASE_TIME + timedelta(minutes=1),
    )

    tracking_service.delete_attempt(latest.id)
    snapshot = build_tracker_snapshot(tracking_service, question_set.id)

    assert [view.attempt.id for view in snapshot.columns[0].attempts] == [
        previous.id
    ]
    assert snapshot.columns[0].attempts[0].is_latest is True


def test_snapshot_progress_updates_and_latest_status_controls_mastery(
    tracking_service: TrackingService,
) -> None:
    question_set = _ready_set(tracking_service)
    first = tracking_service.create_question(question_set.id, "1")
    second = tracking_service.create_question(question_set.id, "2")

    initial = build_tracker_snapshot(tracking_service, question_set.id)
    tracking_service.add_attempt(first.id, AttemptStatus.CORRECT, timestamp=BASE_TIME)
    partial = build_tracker_snapshot(tracking_service, question_set.id)
    tracking_service.add_attempt(second.id, AttemptStatus.CORRECT, timestamp=BASE_TIME)
    mastered = build_tracker_snapshot(tracking_service, question_set.id)
    tracking_service.add_attempt(
        first.id,
        AttemptStatus.PARTIAL,
        timestamp=BASE_TIME + timedelta(minutes=1),
    )
    no_longer_mastered = build_tracker_snapshot(tracking_service, question_set.id)

    assert initial.attempted_count == 0
    assert partial.attempted_count == 1
    assert partial.progress.complete is False
    assert mastered.attempted_count == 2
    assert mastered.progress.complete is True
    assert mastered.progress.mastered is True
    assert no_longer_mastered.progress.complete is True
    assert no_longer_mastered.progress.mastered is False


def test_rapid_generated_attempts_remain_in_marking_order(
    monkeypatch, tracking_service: TrackingService
) -> None:
    question_set = _ready_set(tracking_service)
    question = tracking_service.create_question(question_set.id, "1")
    monkeypatch.setattr(
        "question_tracker.application.tracking_service._utc_now",
        lambda: BASE_TIME,
    )

    first = tracking_service.add_attempt(question.id, AttemptStatus.INCORRECT)
    second = tracking_service.add_attempt(question.id, AttemptStatus.PARTIAL)
    third = tracking_service.add_attempt(question.id, AttemptStatus.CORRECT)

    assert [
        attempt.id for attempt in tracking_service.attempts_for_question(question.id)
    ] == [first.id, second.id, third.id]
