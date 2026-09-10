from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QInputDialog,
    QMessageBox,
    QPushButton,
)

from question_tracker.application.library_service import LibraryService
from question_tracker.application.tracking_service import TrackingService
from question_tracker.app import MainWindow
from question_tracker.domain.library import (
    CATEGORY_FOR_DOCUMENT_TYPE,
    DocumentType,
    FileAvailability,
    FileRole,
    STANDARD_CATEGORY_FOLDERS,
)
from question_tracker.domain.tracking import AttemptStatus, SetupState
from question_tracker.paths import ApplicationPaths
from question_tracker.ui.home import HomeScreen
from question_tracker.ui.import_dialog import ImportPdfDialog
from question_tracker.ui.marking import MarkingWorkspace
from question_tracker.ui.question_editor import QuestionEditorDialog
from question_tracker.ui.reference_dialog import ReferenceDialog


def _create_module(library_service: LibraryService) -> None:
    library_service.create_year("Year 2")
    library_service.create_module("Year 2", "Analysis")


def _question_set_for_main(library_service: LibraryService, file_id: str):
    return library_service.question_sets_for_file(file_id, FileRole.MAIN)[0]


def _import_main(
    library_service: LibraryService,
    application_paths: ApplicationPaths,
    pdf_factory,
    filename: str,
    document_type: DocumentType = DocumentType.PROBLEM_SHEET,
):
    source = pdf_factory(application_paths.data_dir / "sources" / filename)
    return library_service.import_pdf(
        source, "Year 2", "Analysis", document_type
    )


@pytest.mark.parametrize(
    "document_type",
    (
        DocumentType.PROBLEM_SHEET,
        DocumentType.PAST_PAPER,
        DocumentType.SOLUTION,
    ),
)
def test_import_dialog_copies_each_document_type_and_preserves_source(
    qtbot,
    library_service: LibraryService,
    application_paths: ApplicationPaths,
    pdf_factory,
    document_type: DocumentType,
) -> None:
    _create_module(library_service)
    source = pdf_factory(
        application_paths.data_dir / "incoming" / f"{document_type.value}.pdf"
    )
    original = source.read_bytes()
    dialog = ImportPdfDialog(library_service, document_type)
    qtbot.addWidget(dialog)
    dialog.set_source_path(source)
    dialog.show()

    qtbot.mouseClick(dialog.import_button, Qt.MouseButton.LeftButton)

    assert dialog.result() == QDialog.DialogCode.Accepted
    assert source.read_bytes() == original
    destination = (
        application_paths.managed_library_root
        / "Year 2"
        / "Analysis"
        / CATEGORY_FOR_DOCUMENT_TYPE[document_type]
        / source.name
    )
    assert destination.read_bytes() == original
    assert dialog.imported_file is not None
    question_sets = library_service.question_sets_for_file(dialog.imported_file.id)
    if document_type is DocumentType.SOLUTION:
        assert question_sets == ()
    else:
        assert len(question_sets) == 1
        assert question_sets[0].setup_state is SetupState.NEEDS_SETUP


def test_import_conflict_is_visible_and_alternate_filename_succeeds(
    qtbot,
    monkeypatch,
    library_service: LibraryService,
    application_paths: ApplicationPaths,
    pdf_factory,
) -> None:
    _create_module(library_service)
    source = pdf_factory(application_paths.data_dir / "incoming" / "Sheet.pdf")
    library_service.import_pdf(
        source, "Year 2", "Analysis", DocumentType.PROBLEM_SHEET
    )
    warnings: list[tuple[str, str]] = []
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda parent, title, message, *args: (
            warnings.append((title, message)) or QMessageBox.StandardButton.Cancel
        ),
    )
    dialog = ImportPdfDialog(library_service, DocumentType.PROBLEM_SHEET)
    qtbot.addWidget(dialog)
    dialog.set_source_path(source)
    dialog.show()

    qtbot.mouseClick(dialog.import_button, Qt.MouseButton.LeftButton)

    assert dialog.result() != QDialog.DialogCode.Accepted
    assert warnings and warnings[-1][0] == "Replace existing PDF?"
    assert "keep the existing questions and tracking" in warnings[-1][1]

    dialog.destination_name_edit.setText("Sheet copy.pdf")
    qtbot.mouseClick(dialog.import_button, Qt.MouseButton.LeftButton)
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert (
        application_paths.managed_library_root
        / "Year 2"
        / "Analysis"
        / "Problem Sheets"
        / "Sheet copy.pdf"
    ).is_file()


def test_import_dialog_creates_year_and_module_without_leaving_workflow(
    qtbot,
    monkeypatch,
    library_service: LibraryService,
) -> None:
    answers = iter((("Masters", True), ("Functional Analysis", True)))
    monkeypatch.setattr(QInputDialog, "getText", lambda *args, **kwargs: next(answers))
    dialog = ImportPdfDialog(library_service, DocumentType.PAST_PAPER)
    qtbot.addWidget(dialog)

    qtbot.mouseClick(
        dialog.findChild(QPushButton, "createImportYearButton"),
        Qt.MouseButton.LeftButton,
    )
    qtbot.mouseClick(
        dialog.findChild(QPushButton, "createImportModuleButton"),
        Qt.MouseButton.LeftButton,
    )

    assert dialog.year_combo.currentText() == "Masters"
    assert dialog.module_combo.currentText() == "Functional Analysis"
    module = library_service.root / "Masters" / "Functional Analysis"
    assert {path.name for path in module.iterdir()} == set(STANDARD_CATEGORY_FOLDERS)


def test_home_module_cards_show_multiple_modules_and_open_exact_tracker(
    qtbot,
    tracking_service: TrackingService,
    library_service: LibraryService,
    application_paths: ApplicationPaths,
    pdf_factory,
) -> None:
    library_service.create_year("Year 1")
    for module_name in ("Algebra", "Analysis"):
        library_service.create_module("Year 1", module_name)
        source = pdf_factory(
            application_paths.data_dir / "sources" / f"{module_name}.pdf"
        )
        main = library_service.import_pdf(
            source, "Year 1", module_name, DocumentType.PROBLEM_SHEET
        )
        question_set = _question_set_for_main(library_service, main.id)
        tracking_service.create_question(question_set.id, "1")
        tracking_service.finish_question_set_setup(question_set.id)
    opened: list[str] = []
    home = HomeScreen(
        tracking_service,
        application_paths,
        open_tracker=opened.append,
        library_service=library_service,
    )
    qtbot.addWidget(home)

    assert set(home.module_cards) == {
        ("Year 1", "Algebra"),
        ("Year 1", "Analysis"),
    }
    card = home.module_cards[("Year 1", "Analysis")]
    assert "Analysis" in card.text()
    assert "Problem Sheets: 0 / 1 attempted" in card.text()
    qtbot.mouseClick(card, Qt.MouseButton.LeftButton)

    expected = library_service.question_sets_for_module("Year 1", "Analysis")[0]
    assert opened == [expected.id]


def test_home_needs_setup_action_opens_editor_and_refreshes_summary(
    qtbot,
    monkeypatch,
    tracking_service: TrackingService,
    library_service: LibraryService,
    application_paths: ApplicationPaths,
    pdf_factory,
) -> None:
    _create_module(library_service)
    main = _import_main(
        library_service, application_paths, pdf_factory, "Needs setup.pdf"
    )
    question_set = _question_set_for_main(library_service, main.id)
    opened: list[str] = []

    def finish_setup(dialog: QuestionEditorDialog):
        opened.append(dialog.question_set_id)
        tracking_service.create_question(dialog.question_set_id, "1")
        tracking_service.finish_question_set_setup(dialog.question_set_id)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(QuestionEditorDialog, "exec", finish_setup)
    home = HomeScreen(
        tracking_service,
        application_paths,
        library_service=library_service,
    )
    qtbot.addWidget(home)
    item = home.needs_setup_tree.topLevelItem(0)
    setup_button = home.needs_setup_tree.itemWidget(item, 4)

    qtbot.mouseClick(setup_button, Qt.MouseButton.LeftButton)

    assert opened == [question_set.id]
    assert home.needs_setup_tree.topLevelItemCount() == 0
    assert "Problem Sheets: 0 / 1 attempted" in home.module_cards[
        ("Year 2", "Analysis")
    ].text()


def test_home_quick_browser_routes_main_and_solution_files(
    qtbot,
    tracking_service: TrackingService,
    library_service: LibraryService,
    application_paths: ApplicationPaths,
    pdf_factory,
) -> None:
    _create_module(library_service)
    main = _import_main(library_service, application_paths, pdf_factory, "Sheet.pdf")
    solution = _import_main(
        library_service,
        application_paths,
        pdf_factory,
        "Solution.pdf",
        DocumentType.SOLUTION,
    )
    question_set = _question_set_for_main(library_service, main.id)
    tracking_service.create_question(question_set.id, "1")
    tracking_service.finish_question_set_setup(question_set.id)
    tracker_calls: list[str] = []
    marking_calls: list[str | None] = []
    file_calls: list[str] = []
    home = HomeScreen(
        tracking_service,
        application_paths,
        open_tracker=tracker_calls.append,
        open_marking=marking_calls.append,
        open_file_marking=file_calls.append,
        library_service=library_service,
    )
    qtbot.addWidget(home)

    home.library_browser.setCurrentItem(home.library_browser.file_item(main.id))
    qtbot.mouseClick(home.quick_tracker_button, Qt.MouseButton.LeftButton)
    qtbot.mouseClick(home.quick_marking_button, Qt.MouseButton.LeftButton)
    assert tracker_calls == [question_set.id]
    assert marking_calls == [question_set.id]

    home.library_browser.setCurrentItem(home.library_browser.file_item(solution.id))
    qtbot.mouseClick(home.quick_marking_button, Qt.MouseButton.LeftButton)
    assert file_calls == [solution.id]


def test_home_generic_marking_and_import_buttons_open_expected_workflows(
    qtbot,
    monkeypatch,
    tracking_service: TrackingService,
    library_service: LibraryService,
    application_paths: ApplicationPaths,
) -> None:
    marking_calls: list[str | None] = []
    imported_types: list[DocumentType] = []
    monkeypatch.setattr(
        ImportPdfDialog,
        "exec",
        lambda dialog: imported_types.append(dialog.document_type)
        or QDialog.DialogCode.Rejected,
    )
    home = HomeScreen(
        tracking_service,
        application_paths,
        open_marking=marking_calls.append,
        library_service=library_service,
    )
    qtbot.addWidget(home)

    qtbot.mouseClick(home.generic_marking_button, Qt.MouseButton.LeftButton)
    qtbot.mouseClick(home.add_problem_sheet_button, Qt.MouseButton.LeftButton)
    qtbot.mouseClick(home.add_solution_button, Qt.MouseButton.LeftButton)
    qtbot.mouseClick(home.add_past_paper_button, Qt.MouseButton.LeftButton)

    assert marking_calls == [None]
    assert imported_types == [
        DocumentType.PROBLEM_SHEET,
        DocumentType.SOLUTION,
        DocumentType.PAST_PAPER,
    ]


def test_home_import_actions_add_all_types_and_refresh_counts(
    qtbot,
    monkeypatch,
    tracking_service: TrackingService,
    library_service: LibraryService,
    application_paths: ApplicationPaths,
    pdf_factory,
) -> None:
    _create_module(library_service)
    sources = {
        DocumentType.PROBLEM_SHEET: pdf_factory(
            application_paths.data_dir / "incoming" / "Sheet.pdf"
        ),
        DocumentType.SOLUTION: pdf_factory(
            application_paths.data_dir / "incoming" / "Solution.pdf"
        ),
        DocumentType.PAST_PAPER: pdf_factory(
            application_paths.data_dir / "incoming" / "Paper.pdf"
        ),
    }

    def import_selected(dialog: ImportPdfDialog):
        dialog.set_source_path(sources[dialog.document_type])
        dialog._import()
        return dialog.result()

    monkeypatch.setattr(ImportPdfDialog, "exec", import_selected)
    home = HomeScreen(
        tracking_service,
        application_paths,
        library_service=library_service,
    )
    qtbot.addWidget(home)

    qtbot.mouseClick(home.add_problem_sheet_button, Qt.MouseButton.LeftButton)
    qtbot.mouseClick(home.add_solution_button, Qt.MouseButton.LeftButton)
    qtbot.mouseClick(home.add_past_paper_button, Qt.MouseButton.LeftButton)

    summary = home.module_cards[("Year 2", "Analysis")].summary
    assert summary.problem_sheets.total == 1
    assert summary.past_papers.total == 1
    assert summary.needs_setup_count == 2
    assert len(library_service.list_files()) == 3
    assert len(tracking_service.list_question_sets()) == 2


def test_home_create_year_module_validation_and_immediate_refresh(
    qtbot,
    monkeypatch,
    tracking_service: TrackingService,
    library_service: LibraryService,
    application_paths: ApplicationPaths,
) -> None:
    home = HomeScreen(
        tracking_service,
        application_paths,
        library_service=library_service,
    )
    qtbot.addWidget(home)
    monkeypatch.setattr(
        QInputDialog, "getText", lambda *args, **kwargs: ("Revision", True)
    )
    qtbot.mouseClick(home.create_year_button, Qt.MouseButton.LeftButton)
    assert home.snapshot.years == ("Revision",)

    monkeypatch.setattr(
        QInputDialog, "getItem", lambda *args, **kwargs: ("Revision", True)
    )
    monkeypatch.setattr(
        QInputDialog, "getText", lambda *args, **kwargs: ("Methods", True)
    )
    qtbot.mouseClick(home.create_module_button, Qt.MouseButton.LeftButton)

    module = application_paths.managed_library_root / "Revision" / "Methods"
    assert {path.name for path in module.iterdir()} == set(STANDARD_CATEGORY_FOLDERS)
    assert ("Revision", "Methods") in home.module_cards
    assert home.library_browser.folder_item(("Revision", "Methods")) is not None

    warnings: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda parent, title, message: warnings.append(message),
    )
    monkeypatch.setattr(
        QInputDialog, "getText", lambda *args, **kwargs: ("../unsafe", True)
    )
    qtbot.mouseClick(home.create_year_button, Qt.MouseButton.LeftButton)
    assert warnings and "Invalid folder name" in warnings[-1]
    assert not (application_paths.managed_library_root / "unsafe").exists()


def test_home_tree_expansion_survives_module_creation(
    qtbot,
    monkeypatch,
    tracking_service: TrackingService,
    library_service: LibraryService,
    application_paths: ApplicationPaths,
) -> None:
    _create_module(library_service)
    custom_folder = (
        application_paths.managed_library_root
        / "Year 2"
        / "Analysis"
        / "Problem Sheets"
        / "Semester 1"
    )
    custom_folder.mkdir()
    home = HomeScreen(
        tracking_service,
        application_paths,
        library_service=library_service,
    )
    qtbot.addWidget(home)
    expanded_paths = (
        ("Year 2",),
        ("Year 2", "Analysis"),
        ("Year 2", "Analysis", "Problem Sheets"),
        ("Year 2", "Analysis", "Problem Sheets", "Semester 1"),
    )
    for path in expanded_paths:
        home.library_browser.folder_item(path).setExpanded(True)

    monkeypatch.setattr(
        QInputDialog, "getItem", lambda *args, **kwargs: ("Year 2", True)
    )
    monkeypatch.setattr(
        QInputDialog, "getText", lambda *args, **kwargs: ("Topology", True)
    )
    qtbot.mouseClick(home.create_module_button, Qt.MouseButton.LeftButton)

    for path in expanded_paths:
        assert home.library_browser.folder_item(path).isExpanded()


def test_home_tree_expansion_survives_manual_refresh(
    qtbot,
    tracking_service: TrackingService,
    library_service: LibraryService,
    application_paths: ApplicationPaths,
) -> None:
    _create_module(library_service)
    custom_folder = (
        application_paths.managed_library_root
        / "Year 2"
        / "Analysis"
        / "Past Papers"
        / "Archive"
    )
    custom_folder.mkdir()
    home = HomeScreen(
        tracking_service,
        application_paths,
        library_service=library_service,
    )
    qtbot.addWidget(home)
    expanded_paths = (
        ("Year 2",),
        ("Year 2", "Analysis"),
        ("Year 2", "Analysis", "Past Papers"),
        ("Year 2", "Analysis", "Past Papers", "Archive"),
    )
    for path in expanded_paths:
        home.library_browser.folder_item(path).setExpanded(True)

    qtbot.mouseClick(home.refresh_button, Qt.MouseButton.LeftButton)

    for path in expanded_paths:
        assert home.library_browser.folder_item(path).isExpanded()


def test_home_tree_selection_survives_refresh_by_relative_path(
    qtbot,
    tracking_service: TrackingService,
    library_service: LibraryService,
    application_paths: ApplicationPaths,
    pdf_factory,
) -> None:
    _create_module(library_service)
    managed_file = _import_main(
        library_service, application_paths, pdf_factory, "Selected.pdf"
    )
    home = HomeScreen(
        tracking_service,
        application_paths,
        library_service=library_service,
    )
    qtbot.addWidget(home)
    home.library_browser.setCurrentItem(
        home.library_browser.file_item(managed_file.id)
    )

    qtbot.mouseClick(home.refresh_button, Qt.MouseButton.LeftButton)

    assert home.library_browser.selected_file_id() == managed_file.id


def test_home_tree_removed_selected_path_is_handled_safely(
    qtbot,
    tracking_service: TrackingService,
    library_service: LibraryService,
    application_paths: ApplicationPaths,
) -> None:
    _create_module(library_service)
    custom_folder = (
        application_paths.managed_library_root
        / "Year 2"
        / "Analysis"
        / "Past Papers"
        / "Temporary"
    )
    custom_folder.mkdir()
    home = HomeScreen(
        tracking_service,
        application_paths,
        library_service=library_service,
    )
    qtbot.addWidget(home)
    path = ("Year 2", "Analysis", "Past Papers", "Temporary")
    home.library_browser.setCurrentItem(home.library_browser.folder_item(path))
    custom_folder.rmdir()

    qtbot.mouseClick(home.refresh_button, Qt.MouseButton.LeftButton)

    assert home.library_browser.folder_item(path) is None
    assert home.library_browser.currentItem() is None


def test_home_refresh_discovers_external_module_and_pdf_without_restart(
    qtbot,
    tracking_service: TrackingService,
    library_service: LibraryService,
    application_paths: ApplicationPaths,
    pdf_factory,
) -> None:
    home = HomeScreen(
        tracking_service,
        application_paths,
        library_service=library_service,
    )
    qtbot.addWidget(home)
    external = pdf_factory(
        application_paths.managed_library_root
        / "Year 4"
        / "Numerical Methods"
        / "Past Papers"
        / "2026.pdf"
    )
    empty_custom_folder = (
        application_paths.managed_library_root
        / "Year 4"
        / "Numerical Methods"
        / "Past Papers"
        / "2025"
        / "May"
    )
    empty_custom_folder.mkdir(parents=True)

    qtbot.mouseClick(home.refresh_button, Qt.MouseButton.LeftButton)

    assert external.is_file()
    card = home.module_cards[("Year 4", "Numerical Methods")]
    assert "Past Papers: 0 / 1 attempted" in card.text()
    assert home.needs_setup_tree.topLevelItemCount() == 1
    managed_file = next(
        item
        for item in library_service.list_files()
        if item.relative_path.name == "2026.pdf"
    )
    assert home.library_browser.file_item(managed_file.id) is not None
    assert home.library_browser.folder_item(
        ("Year 4", "Numerical Methods", "Past Papers", "2025", "May")
    ) is not None


def test_missing_file_replace_from_home_preserves_tracking_history(
    qtbot,
    monkeypatch,
    tracking_service: TrackingService,
    library_service: LibraryService,
    application_paths: ApplicationPaths,
    pdf_factory,
) -> None:
    _create_module(library_service)
    main = _import_main(library_service, application_paths, pdf_factory, "Sheet.pdf")
    question_set = _question_set_for_main(library_service, main.id)
    question = tracking_service.create_question(question_set.id, "1")
    tracking_service.finish_question_set_setup(question_set.id)
    attempt = tracking_service.add_attempt(question.id, AttemptStatus.PARTIAL)
    library_service.path_for_file(main.id).unlink()
    replacement = pdf_factory(
        application_paths.data_dir / "replacement" / "Replacement.pdf"
    )
    home = HomeScreen(
        tracking_service,
        application_paths,
        library_service=library_service,
    )
    qtbot.addWidget(home)

    missing_item = home.library_browser.file_item(main.id)
    assert missing_item is not None
    assert "Missing" in missing_item.text(0)
    home.library_browser.setCurrentItem(missing_item)
    monkeypatch.setattr(
        QFileDialog,
        "getOpenFileName",
        lambda *args, **kwargs: (str(replacement), "PDF files (*.pdf)"),
    )
    qtbot.mouseClick(home.quick_replace_button, Qt.MouseButton.LeftButton)

    assert library_service.get_file(main.id).availability is FileAvailability.PRESENT
    assert tracking_service.attempts_for_question(question.id)[0].id == attempt.id


@pytest.mark.parametrize("accept", (False, True))
def test_present_home_replacement_requires_confirmation(
    qtbot, monkeypatch, tracking_service, library_service, application_paths,
    pdf_factory, accept,
):
    _create_module(library_service)
    main = _import_main(library_service, application_paths, pdf_factory, "Sheet.pdf")
    original = library_service.path_for_file(main.id).read_bytes()
    replacement = pdf_factory(application_paths.data_dir / "Corrected.pdf")
    replacement.write_bytes(replacement.read_bytes() + b"\n% corrected\n")
    home = HomeScreen(tracking_service, application_paths, library_service=library_service)
    qtbot.addWidget(home)
    home.library_browser.setCurrentItem(home.library_browser.file_item(main.id))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (str(replacement), ""))
    confirmations = []
    def confirm(*args):
        confirmations.append(args)
        return QMessageBox.StandardButton.Yes if accept else QMessageBox.StandardButton.Cancel
    monkeypatch.setattr(QMessageBox, "question", confirm)
    home.quick_replace_button.click()
    assert len(confirmations) == 1
    assert library_service.path_for_file(main.id).read_bytes() == (
        replacement.read_bytes() if accept else original
    )
    assert len(library_service.list_files()) == 1


def test_home_replacement_from_managed_pdf_then_self_reports_no_original_copy(
    qtbot, monkeypatch, tracking_service, library_service, application_paths, pdf_factory,
):
    _create_module(library_service)
    main = _import_main(library_service, application_paths, pdf_factory, "A.pdf")
    other = _import_main(library_service, application_paths, pdf_factory, "B.pdf")
    destination = library_service.path_for_file(main.id)
    source_b = library_service.path_for_file(other.id)
    source_b.write_bytes(source_b.read_bytes() + b"\n% version B\n")
    contents_b = source_b.read_bytes()
    home = HomeScreen(tracking_service, application_paths, library_service=library_service)
    qtbot.addWidget(home)
    home.library_browser.setCurrentItem(home.library_browser.file_item(main.id))
    selections = iter((source_b, destination))
    monkeypatch.setattr(
        QFileDialog, "getOpenFileName", lambda *args: (str(next(selections)), ""),
    )
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[2]))

    home.quick_replace_button.click()
    assert destination.read_bytes() == contents_b
    assert not warnings
    before = library_service.get_file(main.id)
    home.quick_replace_button.click()
    assert len(warnings) == 1
    assert "managed file itself" in warnings[0]
    assert "separate copy" in warnings[0]
    assert library_service.get_file(main.id) == before
    assert source_b.read_bytes() == contents_b
    assert len(library_service.list_files()) == 2
    assert len(tracking_service.list_question_sets()) == 2


def test_duplicate_import_can_explicitly_replace_without_new_set(
    qtbot, monkeypatch, tracking_service, library_service, application_paths, pdf_factory,
):
    _create_module(library_service)
    main = _import_main(library_service, application_paths, pdf_factory, "Sheet.pdf")
    question_set = _question_set_for_main(library_service, main.id)
    source = pdf_factory(application_paths.data_dir / "incoming" / "Sheet.pdf")
    dialog = ImportPdfDialog(library_service, DocumentType.PROBLEM_SHEET)
    qtbot.addWidget(dialog)
    dialog.set_source_path(source)
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    dialog.import_button.click()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.imported_file.id == main.id
    assert [item.id for item in tracking_service.list_question_sets()] == [question_set.id]


def test_home_reference_management_add_reorder_remove_keeps_files_and_tracking(
    qtbot, monkeypatch, tracking_service, library_service, application_paths, pdf_factory,
):
    _create_module(library_service)
    main = _import_main(library_service, application_paths, pdf_factory, "Sheet.pdf")
    question_set = _question_set_for_main(library_service, main.id)
    question = tracking_service.create_question(question_set.id, "1")
    attempt = tracking_service.add_attempt(question.id, AttemptStatus.PARTIAL)
    refs = [
        _import_main(library_service, application_paths, pdf_factory, name, DocumentType.SOLUTION)
        for name in ("Solution A.pdf", "Solution B.pdf")
    ]
    home = HomeScreen(tracking_service, application_paths, library_service=library_service)
    qtbot.addWidget(home)
    home.library_browser.setCurrentItem(home.library_browser.file_item(main.id))
    opened = []
    def edit(dialog):
        opened.append(dialog.question_set_id)
        for file in refs:
            dialog.available.setCurrentIndex(dialog.available.findData(file.id))
            dialog.add_button.click()
        dialog.up_button.click()  # Most recently added reference is selected.
        assert [file.id for file in library_service.files_for_question_set(question_set.id, FileRole.REFERENCE)] == [refs[1].id, refs[0].id]
        dialog.remove_button.click()
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(ReferenceDialog, "exec", edit)
    home.references_button.click()
    assert opened == [question_set.id]
    assert [file.id for file in library_service.files_for_question_set(question_set.id, FileRole.REFERENCE)] == [refs[0].id]
    assert all(library_service.path_for_file(file.id).is_file() for file in refs)
    assert tracking_service.get_attempt(attempt.id).question_id == question.id


def test_solution_from_main_window_home_opens_in_marking_right_pane(
    qtbot,
    tracking_service: TrackingService,
    library_service: LibraryService,
    application_paths: ApplicationPaths,
    pdf_factory,
) -> None:
    _create_module(library_service)
    solution = _import_main(
        library_service,
        application_paths,
        pdf_factory,
        "Solution.pdf",
        DocumentType.SOLUTION,
    )
    window = MainWindow(application_paths, tracking_service, library_service)
    qtbot.addWidget(window)
    home = window.centralWidget()
    assert isinstance(home, HomeScreen)
    home.library_browser.setCurrentItem(home.library_browser.file_item(solution.id))

    qtbot.mouseClick(home.quick_marking_button, Qt.MouseButton.LeftButton)

    workspace = window.centralWidget()
    assert isinstance(workspace, MarkingWorkspace)
    assert workspace.left_pane.mode == "files"
    assert workspace.right_pane.current_file_id == solution.id
