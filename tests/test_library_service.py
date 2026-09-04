from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest

from question_tracker.application.library_service import (
    AssociationError,
    DestinationConflictError,
    InvalidFolderNameError,
    InvalidSourceError,
    InvalidTargetError,
    LibraryService,
)
from question_tracker.application.tracking_service import TrackingService
from question_tracker.domain.library import (
    CATEGORY_FOR_DOCUMENT_TYPE,
    DocumentType,
    FileAvailability,
    FileRole,
    LibraryModule,
    STANDARD_CATEGORY_FOLDERS,
)
from question_tracker.domain.tracking import AttemptStatus, SetupState
from question_tracker.paths import ApplicationPaths


def write_pdf(path: Path, marker: bytes = b"original") -> bytes:
    content = b"%PDF-1.4\n" + marker + b"\n%%EOF\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return content


def question_set_for_main(library_service: LibraryService, file_id: str):
    question_sets = library_service.question_sets_for_file(file_id, FileRole.MAIN)
    assert len(question_sets) == 1
    return question_sets[0]


def test_create_year_and_module_with_standard_categories_and_spaces(
    library_service: LibraryService,
    application_paths: ApplicationPaths,
) -> None:
    year_path = library_service.create_year("Year 2")
    module_path = library_service.create_module("Year 2", "Linear Algebra")

    assert year_path == application_paths.managed_library_root / "Year 2"
    assert module_path == year_path / "Linear Algebra"
    assert {
        path.name for path in module_path.iterdir() if path.is_dir()
    } == set(STANDARD_CATEGORY_FOLDERS)


@pytest.mark.parametrize("name", ("../outside", "..", "bad/name", "CON"))
def test_folder_creation_rejects_unsafe_names(
    library_service: LibraryService, name: str
) -> None:
    with pytest.raises(InvalidFolderNameError):
        library_service.create_year(name)


def test_scan_discovers_manual_layout_and_infers_all_document_types(
    library_service: LibraryService,
    application_paths: ApplicationPaths,
) -> None:
    module = application_paths.managed_library_root / "Year 1" / "Analysis"
    paths = {
        DocumentType.PROBLEM_SHEET: module / "Problem Sheets" / "Sheet 1.pdf",
        DocumentType.SOLUTION: module
        / "Problem Sheet Solutions"
        / "Sheet 1 Solutions.pdf",
        DocumentType.PAST_PAPER: module / "Past Papers" / "2025.pdf",
    }
    for path in paths.values():
        write_pdf(path)

    result = library_service.scan_library()

    assert result.years == ("Year 1",)
    assert result.modules == (LibraryModule("Year 1", "Analysis"),)
    assert len(result.new_files) == 3
    by_name = {file.relative_path.name: file for file in result.present_files}
    assert by_name["Sheet 1.pdf"].document_type is DocumentType.PROBLEM_SHEET
    assert (
        by_name["Sheet 1 Solutions.pdf"].document_type is DocumentType.SOLUTION
    )
    assert by_name["2025.pdf"].document_type is DocumentType.PAST_PAPER

    for filename in ("Sheet 1.pdf", "2025.pdf"):
        question_set = question_set_for_main(
            library_service, by_name[filename].id
        )
        assert question_set.setup_state is SetupState.NEEDS_SETUP
        assert question_set.display_name == Path(filename).stem
    assert library_service.question_sets_for_file(
        by_name["Sheet 1 Solutions.pdf"].id
    ) == ()


@pytest.mark.parametrize(
    ("document_type", "nested_parts", "filename"),
    (
        (DocumentType.PROBLEM_SHEET, ("Week 1",), "Sheet 1.pdf"),
        (
            DocumentType.SOLUTION,
            ("Semester 1", "Week 1"),
            "Sheet 1 Solutions.pdf",
        ),
        (DocumentType.PAST_PAPER, ("2025", "May"), "May Exam.pdf"),
    ),
)
def test_scan_discovers_nested_pdfs_and_infers_type_from_category(
    library_service: LibraryService,
    application_paths: ApplicationPaths,
    document_type: DocumentType,
    nested_parts: tuple[str, ...],
    filename: str,
) -> None:
    category = CATEGORY_FOR_DOCUMENT_TYPE[document_type]
    path = application_paths.managed_library_root.joinpath(
        "Year 2", "Linear Algebra", category, *nested_parts, filename
    )
    write_pdf(path)

    result = library_service.scan_library()

    assert len(result.new_files) == 1
    discovered = result.new_files[0]
    assert discovered.document_type is document_type
    assert discovered.relative_path.as_posix().endswith(
        "/".join((category, *nested_parts, filename))
    )


def test_scan_ignores_pdfs_outside_standard_category_trees(
    library_service: LibraryService,
    application_paths: ApplicationPaths,
) -> None:
    module = application_paths.managed_library_root / "Year 1" / "Analysis"
    write_pdf(module / "Loose Paper.pdf")
    write_pdf(module / "Notes" / "Reference.pdf")

    result = library_service.scan_library()

    assert result.new_files == ()
    assert result.present_files == ()
    assert result.modules == (LibraryModule("Year 1", "Analysis"),)


def test_repeated_recursive_scan_is_idempotent(
    library_service: LibraryService,
    application_paths: ApplicationPaths,
) -> None:
    path = (
        application_paths.managed_library_root
        / "Year 1"
        / "Analysis"
        / "Problem Sheets"
        / "Semester 1"
        / "Week 3"
        / "Sheet.pdf"
    )
    write_pdf(path)

    first = library_service.scan_library()
    second = library_service.refresh()

    assert len(first.new_files) == 1
    assert second.new_files == ()
    assert [file.id for file in second.present_files] == [first.new_files[0].id]


def test_nested_pdf_missing_and_reappearing_retains_uuid(
    library_service: LibraryService,
    application_paths: ApplicationPaths,
) -> None:
    path = (
        application_paths.managed_library_root
        / "Year 3"
        / "Topology"
        / "Past Papers"
        / "2025"
        / "Semester 2"
        / "Exam.pdf"
    )
    write_pdf(path)
    original = library_service.scan_library().new_files[0]

    path.unlink()
    missing = library_service.refresh()
    assert [file.id for file in missing.missing_files] == [original.id]

    write_pdf(path, b"restored")
    reappeared = library_service.refresh()

    assert [file.id for file in reappeared.reappeared_files] == [original.id]
    assert reappeared.reappeared_files[0].relative_path == original.relative_path


def test_repeated_scan_is_idempotent(library_service: LibraryService) -> None:
    library_service.create_year("Year 1")
    module = library_service.create_module("Year 1", "Algebra")
    write_pdf(module / "Problem Sheets" / "Sheet.pdf")

    first = library_service.scan_library()
    second = library_service.refresh()

    assert len(first.new_files) == 1
    assert second.new_files == ()
    assert [file.id for file in second.present_files] == [
        file.id for file in first.present_files
    ]
    assert len(library_service.list_files()) == 1
    assert len(
        library_service.question_sets_for_file(first.present_files[0].id)
    ) == 1


def test_missing_file_preserves_uuid_and_reappears_at_known_path(
    library_service: LibraryService,
) -> None:
    library_service.create_year("Year 1")
    module = library_service.create_module("Year 1", "Algebra")
    path = module / "Past Papers" / "2024.pdf"
    write_pdf(path)
    discovered = library_service.scan_library().new_files[0]
    question_set_id = question_set_for_main(library_service, discovered.id).id

    path.unlink()
    missing_result = library_service.refresh()

    assert [file.id for file in missing_result.missing_files] == [discovered.id]
    assert library_service.get_file(discovered.id).availability is FileAvailability.MISSING
    assert question_set_for_main(library_service, discovered.id).id == question_set_id

    write_pdf(path, b"reappeared")
    reappeared_result = library_service.refresh()

    assert [file.id for file in reappeared_result.reappeared_files] == [
        discovered.id
    ]
    assert library_service.get_file(discovered.id).availability is FileAvailability.PRESENT
    assert question_set_for_main(library_service, discovered.id).id == question_set_id


@pytest.mark.parametrize(
    "document_type",
    (
        DocumentType.PROBLEM_SHEET,
        DocumentType.SOLUTION,
        DocumentType.PAST_PAPER,
    ),
)
def test_import_copies_each_pdf_type_without_altering_source(
    library_service: LibraryService,
    application_paths: ApplicationPaths,
    document_type: DocumentType,
) -> None:
    library_service.create_year("Year 3")
    library_service.create_module("Year 3", "Topology")
    source = application_paths.data_dir / f"{document_type.value}.pdf"
    original = write_pdf(source, document_type.value.encode())

    managed_file = library_service.import_pdf(
        source, "Year 3", "Topology", document_type
    )

    destination = (
        application_paths.managed_library_root
        / "Year 3"
        / "Topology"
        / CATEGORY_FOR_DOCUMENT_TYPE[document_type]
        / source.name
    )
    assert source.exists()
    assert source.read_bytes() == original
    assert destination.read_bytes() == original
    assert managed_file.availability is FileAvailability.PRESENT
    assert str(UUID(managed_file.id)) == managed_file.id
    question_sets = library_service.question_sets_for_file(managed_file.id)
    if document_type is DocumentType.SOLUTION:
        assert question_sets == ()
    else:
        assert len(question_sets) == 1
        assert question_sets[0].setup_state is SetupState.NEEDS_SETUP


def test_import_rejects_destination_conflict_and_non_pdf_sources(
    library_service: LibraryService,
    application_paths: ApplicationPaths,
) -> None:
    library_service.create_year("Year 1")
    library_service.create_module("Year 1", "Geometry")
    source = application_paths.data_dir / "Sheet.pdf"
    write_pdf(source)
    library_service.import_pdf(
        source, "Year 1", "Geometry", DocumentType.PROBLEM_SHEET
    )

    with pytest.raises(DestinationConflictError):
        library_service.import_pdf(
            source, "Year 1", "Geometry", DocumentType.PROBLEM_SHEET
        )

    text_file = application_paths.data_dir / "notes.txt"
    text_file.write_text("not a PDF", encoding="utf-8")
    with pytest.raises(InvalidSourceError, match=".pdf extension"):
        library_service.import_pdf(
            text_file, "Year 1", "Geometry", DocumentType.SOLUTION
        )

    fake_pdf = application_paths.data_dir / "fake.pdf"
    fake_pdf.write_text("not a PDF", encoding="utf-8")
    with pytest.raises(InvalidSourceError, match="PDF header"):
        library_service.import_pdf(
            fake_pdf, "Year 1", "Geometry", DocumentType.SOLUTION
        )


def test_import_rejects_invalid_target(library_service: LibraryService, tmp_path: Path) -> None:
    source = tmp_path / "Sheet.pdf"
    write_pdf(source)

    with pytest.raises(InvalidTargetError, match="year folder does not exist"):
        library_service.import_pdf(
            source, "Missing Year", "Missing Module", DocumentType.PROBLEM_SHEET
        )


def test_manual_solution_associations_support_multiple_and_shared_solutions(
    library_service: LibraryService,
    application_paths: ApplicationPaths,
) -> None:
    library_service.create_year("Year 2")
    library_service.create_module("Year 2", "Mechanics")
    sources = {}
    for name in ("Sheet A.pdf", "Sheet B.pdf", "Solution 1.pdf", "Solution 2.pdf"):
        sources[name] = application_paths.data_dir / name
        write_pdf(sources[name], name.encode())

    first_main = library_service.import_pdf(
        sources["Sheet A.pdf"], "Year 2", "Mechanics", DocumentType.PROBLEM_SHEET
    )
    second_main = library_service.import_pdf(
        sources["Sheet B.pdf"], "Year 2", "Mechanics", DocumentType.PROBLEM_SHEET
    )
    first_solution = library_service.import_pdf(
        sources["Solution 1.pdf"], "Year 2", "Mechanics", DocumentType.SOLUTION
    )
    second_solution = library_service.import_pdf(
        sources["Solution 2.pdf"], "Year 2", "Mechanics", DocumentType.SOLUTION
    )
    first_set = question_set_for_main(library_service, first_main.id)
    second_set = question_set_for_main(library_service, second_main.id)

    assert library_service.files_for_question_set(
        first_set.id, FileRole.REFERENCE
    ) == ()
    library_service.associate_reference(first_set.id, first_solution.id)
    library_service.associate_reference(first_set.id, second_solution.id)
    library_service.associate_reference(second_set.id, first_solution.id)

    assert [
        file.id
        for file in library_service.files_for_question_set(
            first_set.id, FileRole.REFERENCE
        )
    ] == [first_solution.id, second_solution.id]
    assert [
        question_set.id
        for question_set in library_service.question_sets_for_file(
            first_solution.id, FileRole.REFERENCE
        )
    ] == sorted((first_set.id, second_set.id))

    with pytest.raises(AssociationError, match="Only solution PDFs"):
        library_service.associate_reference(first_set.id, second_main.id)


def test_replace_present_pdf_preserves_uuid_association_and_tracking_history(
    library_service: LibraryService,
    tracking_service: TrackingService,
    application_paths: ApplicationPaths,
) -> None:
    library_service.create_year("Year 1")
    library_service.create_module("Year 1", "Calculus")
    original_source = application_paths.data_dir / "Sheet.pdf"
    write_pdf(original_source, b"old content")
    managed_file = library_service.import_pdf(
        original_source, "Year 1", "Calculus", DocumentType.PROBLEM_SHEET
    )
    question_set = question_set_for_main(library_service, managed_file.id)
    question = tracking_service.create_question(question_set.id, "1")
    tracking_service.set_question_note(question.id, "Use substitution")
    tracking_service.toggle_question_bookmark(question.id)
    topic = tracking_service.create_topic(question_set.id, "Integration")
    tracking_service.assign_topic(question.id, topic.id)
    attempt = tracking_service.add_attempt(question.id, AttemptStatus.PARTIAL)
    replacement_source = application_paths.data_dir / "Replacement.pdf"
    replacement_content = write_pdf(replacement_source, b"new content")

    replaced = library_service.replace_file(managed_file.id, replacement_source)

    destination = application_paths.managed_library_root.joinpath(
        *managed_file.relative_path.parts
    )
    assert replaced.id == managed_file.id
    assert destination.read_bytes() == replacement_content
    assert question_set_for_main(library_service, replaced.id).id == question_set.id
    preserved_question = tracking_service.get_question(question.id)
    assert preserved_question.note == "Use substitution"
    assert preserved_question.bookmarked is True
    assert tracking_service.topic_ids_for_question(question.id) == (topic.id,)
    assert tracking_service.get_attempt(attempt.id).question_id == question.id


def test_replace_missing_pdf_preserves_uuid(
    library_service: LibraryService,
    application_paths: ApplicationPaths,
) -> None:
    library_service.create_year("Year 1")
    library_service.create_module("Year 1", "Statistics")
    source = application_paths.data_dir / "Paper.pdf"
    write_pdf(source, b"old")
    managed_file = library_service.import_pdf(
        source, "Year 1", "Statistics", DocumentType.PAST_PAPER
    )
    destination = application_paths.managed_library_root.joinpath(
        *managed_file.relative_path.parts
    )
    destination.unlink()
    library_service.refresh()
    replacement_source = application_paths.data_dir / "Replacement.pdf"
    replacement_content = write_pdf(replacement_source, b"replacement")

    replaced = library_service.replace_file(managed_file.id, replacement_source)

    assert replaced.id == managed_file.id
    assert replaced.availability is FileAvailability.PRESENT
    assert destination.read_bytes() == replacement_content
