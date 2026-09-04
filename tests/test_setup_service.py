from __future__ import annotations

from pathlib import Path

import pytest

from question_tracker.application.library_service import LibraryService
from question_tracker.application.tracking_service import TrackingService, ValidationError
from question_tracker.domain.library import DocumentType, FileRole
from question_tracker.domain.questions import visible_question_label
from question_tracker.domain.tracking import AttemptStatus, QuestionSetKind, SetupState
from question_tracker.paths import ApplicationPaths


def _write_pdf(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"%PDF-1.4\n%%EOF\n")


@pytest.mark.parametrize(
    "kind", (QuestionSetKind.PROBLEM_SHEET, QuestionSetKind.PAST_PAPER)
)
def test_new_trackable_question_sets_need_setup(
    tracking_service: TrackingService, kind: QuestionSetKind
) -> None:
    question_set = tracking_service.create_question_set(kind, "Unconfigured")

    assert question_set.setup_state is SetupState.NEEDS_SETUP
    assert tracking_service.get_question_set(question_set.id).setup_state is SetupState.NEEDS_SETUP


def test_solution_file_does_not_create_a_question_set(
    library_service: LibraryService, application_paths: ApplicationPaths
) -> None:
    solution = (
        application_paths.managed_library_root
        / "Year 2"
        / "Algebra"
        / "Problem Sheet Solutions"
        / "Sheet 1 solutions.pdf"
    )
    _write_pdf(solution)

    result = library_service.scan_library()

    assert len(result.new_files) == 1
    assert result.new_files[0].document_type is DocumentType.SOLUTION
    assert library_service.question_sets_for_file(result.new_files[0].id) == ()


def test_empty_setup_cannot_be_saved_ready(
    tracking_service: TrackingService,
) -> None:
    question_set = tracking_service.create_question_set(
        QuestionSetKind.PROBLEM_SHEET, "Empty"
    )

    with pytest.raises(ValidationError, match="at least one markable leaf"):
        tracking_service.finish_question_set_setup(question_set.id)

    assert tracking_service.get_question_set(question_set.id).setup_state is SetupState.NEEDS_SETUP


def test_finishing_valid_setup_only_updates_state(
    tracking_service: TrackingService,
) -> None:
    question_set = tracking_service.create_question_set(
        QuestionSetKind.PAST_PAPER, "May paper"
    )
    tracking_service.create_question(question_set.id, "1")

    tracking_service.rename_question_set(question_set.id, "May 2025 paper")
    tracking_service.set_question_set_note(question_set.id, "Timed attempt")

    saved = tracking_service.finish_question_set_setup(question_set.id)

    assert saved.setup_state is SetupState.READY
    assert saved.display_name == "May 2025 paper"
    assert saved.general_note == "Timed attempt"


def test_not_finishing_setup_leaves_state_unchanged(
    tracking_service: TrackingService,
) -> None:
    question_set = tracking_service.create_question_set(
        QuestionSetKind.PROBLEM_SHEET, "Sheet 1"
    )
    tracking_service.create_question(question_set.id, "1")

    assert tracking_service.get_question_set(question_set.id).setup_state is SetupState.NEEDS_SETUP


def test_editing_ready_set_preserves_state_and_attempts(
    tracking_service: TrackingService,
) -> None:
    question_set = tracking_service.create_question_set(
        QuestionSetKind.PROBLEM_SHEET,
        "Sheet 1",
        setup_state=SetupState.READY,
    )
    question = tracking_service.create_question(question_set.id, "1")
    attempt = tracking_service.add_attempt(question.id, AttemptStatus.PARTIAL)

    tracking_service.rename_question_set(question_set.id, "Renamed sheet")
    saved = tracking_service.set_question_set_note(
        question_set.id, "Keep practising"
    )

    assert saved.setup_state is SetupState.READY
    assert tracking_service.get_attempt(attempt.id).question_id == question.id


def test_visible_labels_are_derived_from_local_hierarchy_components(
    tracking_service: TrackingService,
) -> None:
    question_set = tracking_service.create_question_set(
        QuestionSetKind.PROBLEM_SHEET, "Sheet"
    )
    root = tracking_service.create_question(question_set.id, "1")
    child = tracking_service.add_child(root.id, "a")
    grandchild = tracking_service.add_child(child.id, "i")
    questions = tracking_service.list_questions(question_set.id)

    assert visible_question_label(root.id, questions) == "1"
    assert visible_question_label(child.id, questions) == "1.a"
    assert visible_question_label(grandchild.id, questions) == "1.a)i"


def test_delete_topic_removes_assignments_and_compacts_order(
    tracking_service: TrackingService,
) -> None:
    question_set = tracking_service.create_question_set(
        QuestionSetKind.PROBLEM_SHEET, "Sheet"
    )
    first_leaf = tracking_service.create_question(question_set.id, "1")
    second_leaf = tracking_service.create_question(question_set.id, "2")
    first = tracking_service.create_topic(question_set.id, "Vectors")
    middle = tracking_service.create_topic(question_set.id, "Matrices")
    last = tracking_service.create_topic(question_set.id, "Eigenvalues")
    tracking_service.assign_topic(first_leaf.id, middle.id)
    tracking_service.assign_topic(second_leaf.id, middle.id)

    tracking_service.delete_topic(middle.id)

    topics = tracking_service.list_topics(question_set.id)
    assert [(topic.id, topic.sort_order) for topic in topics] == [
        (first.id, 0),
        (last.id, 1),
    ]
    assert tracking_service.topic_ids_for_question(first_leaf.id) == ()
    assert tracking_service.topic_ids_for_question(second_leaf.id) == ()


def test_display_name_change_does_not_change_managed_file_path(
    library_service: LibraryService,
    tracking_service: TrackingService,
    application_paths: ApplicationPaths,
) -> None:
    pdf = (
        application_paths.managed_library_root
        / "Year 1"
        / "Analysis"
        / "Problem Sheets"
        / "Sheet 1.pdf"
    )
    _write_pdf(pdf)
    managed_file = library_service.scan_library().new_files[0]
    question_set = library_service.question_sets_for_file(
        managed_file.id, FileRole.MAIN
    )[0]
    tracking_service.create_question(question_set.id, "1")
    original_path = managed_file.relative_path

    tracking_service.rename_question_set(question_set.id, "Limits practice")
    tracking_service.set_question_set_note(
        question_set.id, "Renamed in the app only"
    )

    assert library_service.get_file(managed_file.id).relative_path == original_path
    assert pdf.exists()
