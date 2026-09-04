from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from question_tracker.application.tracking_service import (
    NotFoundError,
    TrackingService,
    ValidationError,
)
from question_tracker.domain.questions import QuestionStructureError
from question_tracker.domain.tracking import (
    AttemptStatus,
    NoteConflictPolicy,
    QuestionSetKind,
    SetupState,
)


BASE_TIME = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def create_set(
    service: TrackingService,
    *,
    kind: QuestionSetKind = QuestionSetKind.PROBLEM_SHEET,
    state: SetupState = SetupState.READY,
):
    return service.create_question_set(kind, "Sheet 1", setup_state=state)


def test_question_sets_share_model_and_support_editable_fields(
    tracking_service: TrackingService,
) -> None:
    problem_sheet = tracking_service.create_question_set(
        QuestionSetKind.PROBLEM_SHEET, "Sheet 1"
    )
    past_paper = tracking_service.create_question_set(
        QuestionSetKind.PAST_PAPER,
        "2025 Paper",
        setup_state=SetupState.READY,
        general_note="Use closed book",
    )

    problem_sheet = tracking_service.rename_question_set(
        problem_sheet.id, "Week 1 Questions"
    )
    problem_sheet = tracking_service.set_question_set_setup_state(
        problem_sheet.id, SetupState.READY
    )
    problem_sheet = tracking_service.set_question_set_note(
        problem_sheet.id, "Review after the lecture"
    )

    assert problem_sheet.kind is QuestionSetKind.PROBLEM_SHEET
    assert problem_sheet.display_name == "Week 1 Questions"
    assert problem_sheet.setup_state is SetupState.READY
    assert problem_sheet.general_note == "Review after the lecture"
    assert past_paper.kind is QuestionSetKind.PAST_PAPER
    assert past_paper.general_note == "Use closed book"


def test_three_level_hierarchy_and_invalid_fourth_level(
    tracking_service: TrackingService,
) -> None:
    question_set = create_set(tracking_service)
    level_one = tracking_service.create_question(question_set.id, "1")
    level_two = tracking_service.add_child(level_one.id, "a")
    level_three = tracking_service.add_child(level_two.id, "i")

    assert [question.id for question in tracking_service.leaf_questions(question_set.id)] == [
        level_three.id
    ]

    with pytest.raises(QuestionStructureError, match="cannot exceed 3 levels"):
        tracking_service.add_child(level_three.id, "A")
    assert len(tracking_service.list_questions(question_set.id)) == 3


def test_leaf_traversal_follows_visible_depth_first_order(
    tracking_service: TrackingService,
) -> None:
    question_set = create_set(tracking_service)
    first = tracking_service.create_question(question_set.id, "1")
    second = tracking_service.create_question(question_set.id, "2")
    first_a = tracking_service.add_child(first.id, "a")
    first_b = tracking_service.add_sibling(first_a.id, "b")
    first_ai = tracking_service.add_child(first_a.id, "i")

    assert [question.id for question in tracking_service.leaf_questions(question_set.id)] == [
        first_ai.id,
        first_b.id,
        second.id,
    ]


def test_rename_and_reorder_preserve_identity_and_attempt_history(
    tracking_service: TrackingService,
) -> None:
    question_set = create_set(tracking_service)
    first = tracking_service.create_question(question_set.id, "1")
    second = tracking_service.create_question(question_set.id, "2")
    attempt = tracking_service.add_attempt(first.id, AttemptStatus.PARTIAL)

    renamed = tracking_service.rename_question(first.id, "Q1")
    reordered = tracking_service.reorder_question(first.id, 1)

    assert renamed.id == first.id == reordered.id
    assert renamed.local_label == "Q1"
    assert [question.id for question in tracking_service.leaf_questions(question_set.id)] == [
        second.id,
        first.id,
    ]
    assert tracking_service.get_attempt(attempt.id).question_id == first.id


def test_valid_reparent_and_cycle_prevention(
    tracking_service: TrackingService,
) -> None:
    question_set = create_set(tracking_service)
    first = tracking_service.create_question(question_set.id, "1")
    second = tracking_service.create_question(question_set.id, "2")
    child = tracking_service.add_child(first.id, "a")

    moved = tracking_service.reparent_question(child.id, second.id)
    assert moved.id == child.id
    assert tracking_service.get_question(child.id).parent_id == second.id

    with pytest.raises(QuestionStructureError, match="contains a cycle"):
        tracking_service.reparent_question(second.id, child.id)


def test_cross_question_set_parenting_is_prevented(
    tracking_service: TrackingService,
) -> None:
    first_set = create_set(tracking_service)
    second_set = create_set(tracking_service)
    first = tracking_service.create_question(first_set.id, "1")
    second = tracking_service.create_question(second_set.id, "1")

    with pytest.raises(ValidationError, match="same question set"):
        tracking_service.reparent_question(first.id, second.id)
    assert tracking_service.get_question(first.id).parent_id is None


def test_attempts_only_belong_to_leaves_and_each_marking_is_separate(
    tracking_service: TrackingService,
) -> None:
    question_set = create_set(tracking_service)
    parent = tracking_service.create_question(question_set.id, "1")
    child = tracking_service.add_child(parent.id, "a")

    with pytest.raises(ValidationError, match="leaf questions"):
        tracking_service.add_attempt(parent.id, AttemptStatus.CORRECT)

    first = tracking_service.add_attempt(child.id, AttemptStatus.INCORRECT)
    second = tracking_service.add_attempt(child.id, AttemptStatus.CORRECT)
    attempts = tracking_service.attempts_for_question(child.id)

    assert first.id != second.id
    assert [attempt.id for attempt in attempts] == [first.id, second.id]


def test_edit_attempt_does_not_create_attempt_and_delete_removes_it(
    tracking_service: TrackingService,
) -> None:
    question_set = create_set(tracking_service)
    question = tracking_service.create_question(question_set.id, "1")
    attempt = tracking_service.add_attempt(
        question.id, AttemptStatus.INCORRECT, note="Misread sign"
    )

    edited = tracking_service.edit_attempt(
        attempt.id, status=AttemptStatus.PARTIAL, note="Only arithmetic error"
    )

    assert edited.id == attempt.id
    assert edited.status is AttemptStatus.PARTIAL
    assert edited.note == "Only arithmetic error"
    assert len(tracking_service.attempts_for_question(question.id)) == 1

    tracking_service.delete_attempt(attempt.id)
    assert tracking_service.attempts_for_question(question.id) == ()


def test_latest_attempt_is_deterministic_by_timestamp(
    tracking_service: TrackingService,
) -> None:
    question_set = create_set(tracking_service)
    question = tracking_service.create_question(question_set.id, "1")
    latest = tracking_service.add_attempt(
        question.id, AttemptStatus.CORRECT, timestamp=BASE_TIME + timedelta(days=1)
    )
    tracking_service.add_attempt(
        question.id, AttemptStatus.INCORRECT, timestamp=BASE_TIME
    )

    assert tracking_service.latest_attempt_for_question(question.id).id == latest.id


def test_split_leaf_transfers_attempt_note_bookmark_and_topics(
    tracking_service: TrackingService,
) -> None:
    question_set = create_set(tracking_service)
    parent = tracking_service.create_question(question_set.id, "1")
    parent = tracking_service.set_question_note(parent.id, "Important method")
    parent = tracking_service.toggle_question_bookmark(parent.id)
    first_topic = tracking_service.create_topic(question_set.id, "Bases")
    second_topic = tracking_service.create_topic(question_set.id, "Proofs")
    tracking_service.assign_topic(parent.id, first_topic.id)
    tracking_service.assign_topic(parent.id, second_topic.id)
    attempt = tracking_service.add_attempt(
        parent.id, AttemptStatus.PARTIAL, note="Retry elimination"
    )

    child = tracking_service.add_child(parent.id, "a")

    updated_parent = tracking_service.get_question(parent.id)
    updated_child = tracking_service.get_question(child.id)
    assert updated_parent.note == ""
    assert updated_parent.bookmarked is False
    assert updated_child.note == "Important method"
    assert updated_child.bookmarked is True
    assert set(tracking_service.topic_ids_for_question(child.id)) == {
        first_topic.id,
        second_topic.id,
    }
    assert tracking_service.topic_ids_for_question(parent.id) == ()
    assert tracking_service.get_attempt(attempt.id).question_id == child.id


def test_permanent_question_deletion_removes_subtree_history(
    tracking_service: TrackingService,
) -> None:
    question_set = create_set(tracking_service)
    parent = tracking_service.create_question(question_set.id, "1")
    child = tracking_service.add_child(parent.id, "a")
    survivor = tracking_service.create_question(question_set.id, "2")
    attempt = tracking_service.add_attempt(child.id, AttemptStatus.CORRECT)

    tracking_service.delete_question_permanently(parent.id)

    assert [question.id for question in tracking_service.list_questions(question_set.id)] == [
        survivor.id
    ]
    assert tracking_service.get_question(survivor.id).sort_order == 0
    with pytest.raises(NotFoundError):
        tracking_service.get_attempt(attempt.id)


def test_delete_and_move_history_preserves_data_on_target_leaf(
    tracking_service: TrackingService,
) -> None:
    question_set = create_set(tracking_service)
    source = tracking_service.create_question(question_set.id, "1")
    target = tracking_service.create_question(question_set.id, "2")
    tracking_service.set_question_note(source.id, "Source note")
    tracking_service.toggle_question_bookmark(source.id)
    topic = tracking_service.create_topic(question_set.id, "Eigenvalues")
    tracking_service.assign_topic(source.id, topic.id)
    attempt = tracking_service.add_attempt(
        source.id, AttemptStatus.NO_START, note="Read chapter 4"
    )

    tracking_service.delete_question_and_move_history(source.id, target.id)

    with pytest.raises(NotFoundError):
        tracking_service.get_question(source.id)
    updated_target = tracking_service.get_question(target.id)
    assert updated_target.note == "Source note"
    assert updated_target.bookmarked is True
    assert tracking_service.topic_ids_for_question(target.id) == (topic.id,)
    moved_attempt = tracking_service.get_attempt(attempt.id)
    assert moved_attempt.question_id == target.id
    assert moved_attempt.note == "Read chapter 4"


def test_move_history_preserves_target_note_when_source_has_no_note(
    tracking_service: TrackingService,
) -> None:
    question_set = create_set(tracking_service)
    source = tracking_service.create_question(question_set.id, "1")
    target = tracking_service.create_question(question_set.id, "2")
    tracking_service.set_question_note(target.id, "Target note")

    tracking_service.delete_question_and_move_history(source.id, target.id)

    assert tracking_service.get_question(target.id).note == "Target note"


@pytest.mark.parametrize(
    ("policy", "expected_note"),
    (
        (NoteConflictPolicy.KEEP_TARGET, "Target note"),
        (NoteConflictPolicy.USE_SOURCE, "Source note"),
        (NoteConflictPolicy.COMBINE, "Target note\n\nSource note"),
    ),
)
def test_move_history_resolves_note_conflict_with_explicit_policy(
    tracking_service: TrackingService,
    policy: NoteConflictPolicy,
    expected_note: str,
) -> None:
    question_set = create_set(tracking_service)
    source = tracking_service.create_question(question_set.id, "1")
    target = tracking_service.create_question(question_set.id, "2")
    tracking_service.set_question_note(source.id, "Source note")
    tracking_service.set_question_note(target.id, "Target note")

    tracking_service.delete_question_and_move_history(
        source.id, target.id, note_conflict_policy=policy
    )

    assert tracking_service.get_question(target.id).note == expected_note


def test_move_history_requires_policy_when_both_questions_have_notes(
    tracking_service: TrackingService,
) -> None:
    question_set = create_set(tracking_service)
    source = tracking_service.create_question(question_set.id, "1")
    target = tracking_service.create_question(question_set.id, "2")
    tracking_service.set_question_note(source.id, "Source note")
    tracking_service.set_question_note(target.id, "Target note")

    with pytest.raises(ValidationError, match="conflict policy is required"):
        tracking_service.delete_question_and_move_history(source.id, target.id)

    assert tracking_service.get_question(source.id).note == "Source note"
    assert tracking_service.get_question(target.id).note == "Target note"


def test_topics_can_be_renamed_reordered_assigned_multiple_and_removed(
    tracking_service: TrackingService,
) -> None:
    question_set = create_set(tracking_service)
    question = tracking_service.create_question(question_set.id, "1")
    first = tracking_service.create_topic(question_set.id, "Bases")
    second = tracking_service.create_topic(question_set.id, "Spans")

    tracking_service.assign_topic(question.id, first.id)
    tracking_service.assign_topic(question.id, second.id)
    tracking_service.assign_topic(question.id, second.id)
    renamed = tracking_service.rename_topic(second.id, "Linear spans")
    reordered = tracking_service.reorder_topic(second.id, 0)

    assert renamed.id == second.id == reordered.id
    assert renamed.title == "Linear spans"
    assert [topic.id for topic in tracking_service.list_topics(question_set.id)] == [
        second.id,
        first.id,
    ]
    assert set(tracking_service.topic_ids_for_question(question.id)) == {
        first.id,
        second.id,
    }

    tracking_service.remove_topic_assignment(question.id, first.id)
    assert tracking_service.topic_ids_for_question(question.id) == (second.id,)


def test_completion_mastery_and_bookmark_independence(
    tracking_service: TrackingService,
) -> None:
    question_set = create_set(tracking_service)
    first = tracking_service.create_question(question_set.id, "1")
    second = tracking_service.create_question(question_set.id, "2")

    assert tracking_service.question_set_progress(question_set.id).complete is False
    tracking_service.add_attempt(first.id, AttemptStatus.CORRECT, timestamp=BASE_TIME)
    assert tracking_service.question_set_progress(question_set.id).complete is False
    tracking_service.add_attempt(second.id, AttemptStatus.PARTIAL, timestamp=BASE_TIME)

    progress = tracking_service.question_set_progress(question_set.id)
    assert progress.complete is True
    assert progress.mastered is False

    tracking_service.toggle_question_bookmark(second.id)
    assert tracking_service.latest_attempt_for_question(second.id).status is AttemptStatus.PARTIAL
    assert tracking_service.question_set_progress(question_set.id) == progress

    tracking_service.add_attempt(
        second.id, AttemptStatus.CORRECT, timestamp=BASE_TIME + timedelta(minutes=1)
    )
    assert tracking_service.question_set_progress(question_set.id).mastered is True


def test_needs_setup_and_empty_ready_sets_are_never_complete_or_mastered(
    tracking_service: TrackingService,
) -> None:
    needs_setup = create_set(tracking_service, state=SetupState.NEEDS_SETUP)
    question = tracking_service.create_question(needs_setup.id, "1")
    tracking_service.add_attempt(question.id, AttemptStatus.CORRECT)
    empty_ready = create_set(tracking_service, state=SetupState.READY)

    needs_setup_progress = tracking_service.question_set_progress(needs_setup.id)
    empty_progress = tracking_service.question_set_progress(empty_ready.id)
    assert needs_setup_progress.complete is False
    assert needs_setup_progress.mastered is False
    assert empty_progress.leaf_count == 0
    assert empty_progress.complete is False
    assert empty_progress.mastered is False


def test_ids_are_stable_uuids(tracking_service: TrackingService) -> None:
    question_set = create_set(tracking_service)
    question = tracking_service.create_question(question_set.id, "1")
    topic = tracking_service.create_topic(question_set.id, "Matrices")
    attempt = tracking_service.add_attempt(question.id, AttemptStatus.CORRECT)

    assert str(UUID(question_set.id)) == question_set.id
    assert str(UUID(question.id)) == question.id
    assert str(UUID(topic.id)) == topic.id
    assert str(UUID(attempt.id)) == attempt.id
