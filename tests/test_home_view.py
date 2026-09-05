from __future__ import annotations

from question_tracker.application.home_view import build_home_snapshot
from question_tracker.application.library_service import LibraryService
from question_tracker.application.tracking_service import TrackingService
from question_tracker.domain.library import DocumentType, FileRole
from question_tracker.domain.tracking import AttemptStatus, SetupState
from question_tracker.paths import ApplicationPaths


def _import_fixture(
    library_service: LibraryService,
    application_paths: ApplicationPaths,
    pdf_factory,
    filename: str,
    document_type: DocumentType,
):
    source = pdf_factory(application_paths.data_dir / "sources" / filename)
    return library_service.import_pdf(
        source, "Year 2", "Linear Algebra", document_type
    )


def test_home_snapshot_derives_module_counts_progress_and_needs_setup(
    tracking_service: TrackingService,
    library_service: LibraryService,
    application_paths: ApplicationPaths,
    pdf_factory,
) -> None:
    library_service.create_year("Year 2")
    library_service.create_module("Year 2", "Linear Algebra")
    mastered_file = _import_fixture(
        library_service,
        application_paths,
        pdf_factory,
        "Sheet 1.pdf",
        DocumentType.PROBLEM_SHEET,
    )
    needs_setup_file = _import_fixture(
        library_service,
        application_paths,
        pdf_factory,
        "Sheet 2.pdf",
        DocumentType.PROBLEM_SHEET,
    )
    paper_file = _import_fixture(
        library_service,
        application_paths,
        pdf_factory,
        "2025.pdf",
        DocumentType.PAST_PAPER,
    )
    _import_fixture(
        library_service,
        application_paths,
        pdf_factory,
        "Sheet 1 Solutions.pdf",
        DocumentType.SOLUTION,
    )

    mastered_set = library_service.question_sets_for_file(
        mastered_file.id, FileRole.MAIN
    )[0]
    mastered_question = tracking_service.create_question(mastered_set.id, "1")
    tracking_service.finish_question_set_setup(mastered_set.id)
    tracking_service.add_attempt(mastered_question.id, AttemptStatus.CORRECT)

    needs_setup_set = library_service.question_sets_for_file(
        needs_setup_file.id, FileRole.MAIN
    )[0]
    incomplete_paper = library_service.question_sets_for_file(
        paper_file.id, FileRole.MAIN
    )[0]
    tracking_service.create_question(incomplete_paper.id, "1")
    tracking_service.finish_question_set_setup(incomplete_paper.id)

    snapshot = build_home_snapshot(
        tracking_service, library_service, library_service.refresh()
    )

    assert snapshot.years == ("Year 2",)
    assert len(snapshot.modules) == 1
    summary = snapshot.modules[0]
    assert summary.module.year_name == "Year 2"
    assert summary.module.module_name == "Linear Algebra"
    assert summary.problem_sheets.total == 2
    assert summary.problem_sheets.complete == 1
    assert summary.problem_sheets.mastered == 1
    assert summary.problem_sheets.needs_setup == 1
    assert summary.past_papers.total == 1
    assert summary.past_papers.complete == 0
    assert summary.past_papers.mastered == 0
    assert summary.first_ready_question_set_id == mastered_set.id
    assert [item.question_set.id for item in snapshot.needs_setup] == [
        needs_setup_set.id
    ]
    assert snapshot.needs_setup[0].question_set.setup_state is SetupState.NEEDS_SETUP


def test_home_snapshot_includes_empty_and_missing_managed_modules(
    tracking_service: TrackingService,
    library_service: LibraryService,
    application_paths: ApplicationPaths,
    pdf_factory,
) -> None:
    library_service.create_year("Year 1")
    library_service.create_module("Year 1", "Empty Module")
    library_service.create_module("Year 1", "Missing Module")
    source = pdf_factory(application_paths.data_dir / "Missing.pdf")
    managed_file = library_service.import_pdf(
        source,
        "Year 1",
        "Missing Module",
        DocumentType.PROBLEM_SHEET,
    )
    library_service.path_for_file(managed_file.id).unlink()

    snapshot = build_home_snapshot(
        tracking_service, library_service, library_service.refresh()
    )

    modules = {
        (summary.module.year_name, summary.module.module_name): summary
        for summary in snapshot.modules
    }
    assert ("Year 1", "Empty Module") in modules
    assert ("Year 1", "Missing Module") in modules
    assert modules[("Year 1", "Empty Module")].problem_sheets.total == 0
    assert snapshot.needs_setup[0].is_missing is True
