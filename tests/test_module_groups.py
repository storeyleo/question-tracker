import pytest

from question_tracker.application.module_group_service import ModuleGroupError, ModuleGroupService
from question_tracker.domain.library import LibraryModule, FileRole, DocumentType
from question_tracker.domain.tracking import AttemptStatus
from question_tracker.persistence.database import Database


def test_group_lifecycle_and_assignment_reopen(database):
    service = ModuleGroupService(database)
    module = LibraryModule("Year 2", "Analysis")
    first = service.create("Year 2", "  Maths  ")
    second = service.create("Year 2", "Physics")
    assert first.name == "Maths"
    service.assign(module, first.id)
    renamed = service.rename(first.id, "Pure Maths")
    assert renamed.id == first.id
    assert service.snapshot()[1][("year 2", "analysis")] == first.id
    service.assign(module, second.id)
    reopened = ModuleGroupService(Database(database.path))
    assert reopened.snapshot()[1][("year 2", "analysis")] == second.id
    service.assign(module, None)
    assert not service.snapshot()[1]
    service.delete(first.id)  # Empty group.
    service.assign(module, second.id)
    service.delete(second.id)  # Cascades only organisation assignments.
    assert service.snapshot() == ((), {})


def test_group_validation(database):
    service = ModuleGroupService(database)
    first = service.create("Year 2", "Maths")
    second = service.create("Year 2", "Physics")
    service.create("Year 3", "Maths")
    for name in ("", "   ", "MATHS", " maths "):
        with pytest.raises(ModuleGroupError):
            service.create("Year 2", name)
        with pytest.raises(ModuleGroupError):
            service.rename(second.id, name)
    with pytest.raises(ModuleGroupError):
        service.assign(LibraryModule("Year 3", "Analysis"), first.id)
    with pytest.raises(ModuleGroupError):
        service.assign(LibraryModule("Year 2", "Analysis"), "missing")
    assert not service.snapshot()[1]


def test_regroup_scan_and_import_preserve_tracking(database, library_service, tracking_service, pdf_factory, tmp_path):
    library_service.create_year("Year 2")
    library_service.create_module("Year 2", "Analysis")
    source = pdf_factory(tmp_path / "Source.pdf")
    managed = library_service.import_pdf(source, "Year 2", "Analysis", DocumentType.PROBLEM_SHEET)
    question_set = library_service.question_sets_for_file(managed.id, FileRole.MAIN)[0]
    question = tracking_service.create_question(question_set.id, "1")
    tracking_service.finish_question_set_setup(question_set.id)
    attempt = tracking_service.add_attempt(question.id, AttemptStatus.CORRECT)
    before_set = tracking_service.get_question_set(question_set.id)
    before_question = tracking_service.get_question(question.id)
    before_bytes = (library_service.root / managed.relative_path).read_bytes()
    service = ModuleGroupService(database)
    group = service.create("Year 2", "Maths")
    service.assign(LibraryModule("Year 2", "Analysis"), group.id)
    library_service.refresh()
    library_service.import_pdf(pdf_factory(tmp_path / "Other.pdf", ("Other",)), "Year 2", "Analysis", DocumentType.PROBLEM_SHEET)
    assert service.snapshot()[1][("year 2", "analysis")] == group.id
    service.delete(group.id)
    assert tracking_service.get_question_set(question_set.id) == before_set
    assert tracking_service.get_question(question.id) == before_question
    assert tracking_service.get_attempt(attempt.id) == attempt
    assert library_service.module_for_question_set(question_set.id) == LibraryModule("Year 2", "Analysis")
    assert library_service.get_file(managed.id).relative_path == managed.relative_path
    assert (library_service.root / managed.relative_path).read_bytes() == before_bytes
    assert not (library_service.root / "Year 2" / "Maths").exists()


def test_v4_database_upgrade_preserves_existing_rows(database, tracking_service):
    from question_tracker.domain.tracking import QuestionSetKind
    question_set = tracking_service.create_question_set(QuestionSetKind.PROBLEM_SHEET, "Existing")
    with database.transaction() as connection:
        connection.execute("DROP TABLE module_group_assignments")
        connection.execute("DROP TABLE module_groups")
        connection.execute("DELETE FROM schema_migrations WHERE version=5")
    database.initialize()
    database.initialize()
    assert tracking_service.get_question_set(question_set.id) == question_set
    assert ModuleGroupService(database).snapshot() == ((), {})
    with database.read() as connection:
        assert not connection.execute("PRAGMA foreign_key_check").fetchall()
