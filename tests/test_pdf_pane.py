from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtPdf import QPdfDocument
from PySide6.QtPdfWidgets import QPdfView
from PySide6.QtWidgets import QTreeWidgetItem

from question_tracker.application.library_service import LibraryService
from question_tracker.domain.library import FileAvailability
from question_tracker.paths import ApplicationPaths
from question_tracker.ui.pdf_pane import PdfPane


def _managed_pdf_files(
    library_service: LibraryService,
    application_paths: ApplicationPaths,
    pdf_factory,
):
    module = application_paths.managed_library_root / "Year 2" / "Analysis"
    paths = (
        pdf_factory(
            module / "Problem Sheets" / "Sheet.pdf",
            ("first needle", "second page"),
        ),
        pdf_factory(
            module / "Problem Sheets" / "Other.pdf",
            ("another document",),
        ),
        pdf_factory(
            module / "Problem Sheet Solutions" / "Sheet Solutions.pdf",
            ("worked solution",),
        ),
        pdf_factory(
            module / "Past Papers" / "Exam.pdf",
            ("past paper",),
        ),
    )
    library_service.scan_library()
    by_name = {
        item.relative_path.name: item for item in library_service.list_files()
    }
    return paths, by_name


def _wait_ready(qtbot, pane: PdfPane) -> None:
    qtbot.waitUntil(
        lambda: pane.document.status() == QPdfDocument.Status.Ready,
        timeout=3000,
    )


def _find_file_item(
    parent: QTreeWidgetItem, file_id: str
) -> QTreeWidgetItem | None:
    if parent.data(0, parent.treeWidget().FILE_ID_ROLE) == file_id:
        return parent
    for index in range(parent.childCount()):
        if result := _find_file_item(parent.child(index), file_id):
            return result
    return None


def _browser_item(pane: PdfPane, file_id: str) -> QTreeWidgetItem:
    for index in range(pane.browser.topLevelItemCount()):
        item = _find_file_item(pane.browser.topLevelItem(index), file_id)
        if item is not None:
            return item
    raise AssertionError(f"Browser item not found: {file_id}")


def test_two_panes_keep_files_and_back_navigation_independent(
    qtbot,
    library_service: LibraryService,
    application_paths: ApplicationPaths,
    pdf_factory,
) -> None:
    _, files = _managed_pdf_files(
        library_service, application_paths, pdf_factory
    )
    left = PdfPane(library_service, "left")
    right = PdfPane(library_service, "right")
    qtbot.addWidget(left)
    qtbot.addWidget(right)

    left.open_managed_file(files["Sheet.pdf"].id)
    right.open_managed_file(files["Sheet Solutions.pdf"].id)
    _wait_ready(qtbot, left)
    _wait_ready(qtbot, right)

    left.open_managed_file(files["Other.pdf"].id)
    _wait_ready(qtbot, left)
    assert left.current_file_id == files["Other.pdf"].id
    assert right.current_file_id == files["Sheet Solutions.pdf"].id

    left.show_files()
    assert left.mode == "files"
    assert right.mode == "pdf"
    assert right.current_file_id == files["Sheet Solutions.pdf"].id

    left.open_managed_file(files["Exam.pdf"].id)
    _wait_ready(qtbot, left)
    right.show_files()
    assert right.mode == "files"
    assert left.mode == "pdf"
    assert left.current_file_id == files["Exam.pdf"].id


def test_browser_shows_all_categories_and_opens_selection_in_its_pane(
    qtbot,
    library_service: LibraryService,
    application_paths: ApplicationPaths,
    pdf_factory,
) -> None:
    _, files = _managed_pdf_files(
        library_service, application_paths, pdf_factory
    )
    pane = PdfPane(library_service, "left")
    qtbot.addWidget(pane)

    all_text = []

    def collect(item: QTreeWidgetItem) -> None:
        all_text.append(item.text(0))
        for child_index in range(item.childCount()):
            collect(item.child(child_index))

    for index in range(pane.browser.topLevelItemCount()):
        collect(pane.browser.topLevelItem(index))
    assert "Problem Sheets" in all_text
    assert "Problem Sheet Solutions" in all_text
    assert "Past Papers" in all_text
    assert "Sheet Solutions.pdf" in all_text

    pane.browser.setCurrentItem(_browser_item(pane, files["Exam.pdf"].id))
    qtbot.mouseClick(pane.open_button, Qt.MouseButton.LeftButton)
    _wait_ready(qtbot, pane)
    assert pane.current_file_id == files["Exam.pdf"].id
    assert pane.mode == "pdf"


def test_switching_document_releases_and_resets_only_that_pane(
    qtbot,
    library_service: LibraryService,
    application_paths: ApplicationPaths,
    pdf_factory,
) -> None:
    _, files = _managed_pdf_files(
        library_service, application_paths, pdf_factory
    )
    left = PdfPane(library_service, "left")
    right = PdfPane(library_service, "right")
    qtbot.addWidget(left)
    qtbot.addWidget(right)
    left.open_managed_file(files["Sheet.pdf"].id)
    right.open_managed_file(files["Sheet Solutions.pdf"].id)
    _wait_ready(qtbot, left)
    _wait_ready(qtbot, right)

    left.next_page()
    left.zoom_in()
    left.search_edit.setText("needle")
    qtbot.waitUntil(lambda: left.current_page == 1)
    right.zoom_out()
    right_zoom = right.zoom_factor

    left.open_managed_file(files["Other.pdf"].id)
    _wait_ready(qtbot, left)
    assert left.current_page == 0
    assert left.zoom_factor == pytest.approx(1.0)
    assert left.search_edit.text() == ""
    assert right.current_file_id == files["Sheet Solutions.pdf"].id
    assert right.zoom_factor == pytest.approx(right_zoom)


def test_pdf_page_zoom_fit_search_and_independent_state(
    qtbot,
    library_service: LibraryService,
    application_paths: ApplicationPaths,
    pdf_factory,
) -> None:
    _, files = _managed_pdf_files(
        library_service, application_paths, pdf_factory
    )
    left = PdfPane(library_service, "left")
    right = PdfPane(library_service, "right")
    qtbot.addWidget(left)
    qtbot.addWidget(right)
    left.open_managed_file(files["Sheet.pdf"].id)
    right.open_managed_file(files["Other.pdf"].id)
    _wait_ready(qtbot, left)
    _wait_ready(qtbot, right)

    left.next_page()
    qtbot.waitUntil(lambda: left.current_page == 1)
    left.previous_page()
    qtbot.waitUntil(lambda: left.current_page == 0)
    left.jump_to_page(999)
    qtbot.waitUntil(lambda: left.current_page == 1)
    left.jump_to_page(-10)
    qtbot.waitUntil(lambda: left.current_page == 0)

    left.zoom_in()
    assert left.zoom_factor == pytest.approx(1.2)
    assert right.zoom_factor == pytest.approx(1.0)
    left.zoom_out()
    left.reset_zoom()
    assert left.zoom_factor == pytest.approx(1.0)
    left.fit_page()
    assert left.pdf_view.zoomMode() == QPdfView.ZoomMode.FitInView
    left.fit_width()
    assert left.pdf_view.zoomMode() == QPdfView.ZoomMode.FitToWidth

    left.search_edit.setText("needle")
    qtbot.waitUntil(lambda: left.search_model.count() == 1, timeout=3000)
    left.next_search_result()
    assert left.pdf_view.currentSearchResultIndex() == 0
    left.previous_search_result()
    assert left.pdf_view.currentSearchResultIndex() == 0
    right.search_edit.setText("not present")
    qtbot.wait(50)
    assert left.search_edit.text() == "needle"
    assert right.search_model.count() == 0


def test_missing_and_malformed_pdf_show_non_crashing_error(
    qtbot,
    library_service: LibraryService,
    application_paths: ApplicationPaths,
    pdf_factory,
) -> None:
    paths, files = _managed_pdf_files(
        library_service, application_paths, pdf_factory
    )
    paths[0].unlink()
    library_service.refresh()
    missing = library_service.get_file(files["Sheet.pdf"].id)
    assert missing.availability is FileAvailability.MISSING

    pane = PdfPane(library_service, "left")
    qtbot.addWidget(pane)
    assert pane.open_managed_file(missing.id) is False
    assert "missing" in pane.document_state_label.text().lower()
    assert not pane.replace_button.isHidden()
    pane.show_files()
    assert pane.mode == "files"

    malformed_path = (
        application_paths.managed_library_root
        / "Year 2"
        / "Analysis"
        / "Past Papers"
        / "Malformed.pdf"
    )
    malformed_path.write_bytes(b"not a pdf")
    library_service.refresh()
    malformed = next(
        item for item in library_service.list_files()
        if item.relative_path.name == "Malformed.pdf"
    )
    pane.open_managed_file(malformed.id)
    qtbot.waitUntil(
        lambda: pane.document.status() == QPdfDocument.Status.Error,
        timeout=3000,
    )
    assert "valid readable PDF" in pane.document_state_label.text()


def test_branch_expand_all_is_scoped_and_independent_between_browsers(
    qtbot,
    library_service: LibraryService,
    application_paths: ApplicationPaths,
    pdf_factory,
) -> None:
    root = application_paths.managed_library_root / "Year 1"
    pdf_factory(
        root
        / "Module A"
        / "Problem Sheets"
        / "Semester 1"
        / "Week 1"
        / "A.pdf"
    )
    pdf_factory(
        root
        / "Module B"
        / "Past Papers"
        / "Archive"
        / "B.pdf"
    )
    library_service.refresh()
    left = PdfPane(library_service, "left")
    right = PdfPane(library_service, "right")
    qtbot.addWidget(left)
    qtbot.addWidget(right)

    left.browser.collapseAll()
    right.browser.collapseAll()
    left_year = left.browser.folder_item(("Year 1",))
    right_year = right.browser.folder_item(("Year 1",))
    assert left_year is not None
    assert right_year is not None
    left.browser.itemClicked.emit(left_year, 1)

    assert left_year.isExpanded()
    assert left.browser.folder_item(("Year 1", "Module A")).isExpanded()
    assert left.browser.folder_item(
        ("Year 1", "Module A", "Problem Sheets", "Semester 1", "Week 1")
    ).isExpanded()
    assert not right_year.isExpanded()

    left.browser.collapseAll()
    left_year.setExpanded(True)
    module_a = left.browser.folder_item(("Year 1", "Module A"))
    module_b = left.browser.folder_item(("Year 1", "Module B"))
    assert module_a is not None
    assert module_b is not None
    left.browser.itemClicked.emit(module_a, 1)
    assert module_a.isExpanded()
    assert left.browser.folder_item(
        ("Year 1", "Module A", "Problem Sheets", "Semester 1")
    ).isExpanded()
    assert not module_b.isExpanded()


def test_pdf_toolbar_scrolls_horizontally_without_imposing_pane_width(
    qtbot,
    library_service: LibraryService,
    application_paths: ApplicationPaths,
    pdf_factory,
) -> None:
    _, files = _managed_pdf_files(
        library_service, application_paths, pdf_factory
    )
    pane = PdfPane(library_service, "left")
    qtbot.addWidget(pane)
    pane.resize(220, 500)
    pane.show()
    pane.open_managed_file(files["Sheet.pdf"].id)
    _wait_ready(qtbot, pane)
    qtbot.waitUntil(
        lambda: pane.toolbar_scroll.horizontalScrollBar().maximum() > 0
    )

    assert pane.minimumWidth() == 0
    assert pane.toolbar_scroll.minimumWidth() == 0
    assert (
        pane.toolbar_scroll.verticalScrollBarPolicy()
        == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    )
    assert pane.width() <= 220
    assert pane.fit_width_button.isEnabled()
    pane.fit_width_button.click()
    assert pane.pdf_view.zoomMode() == QPdfView.ZoomMode.FitToWidth
