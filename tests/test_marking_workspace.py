from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QUndoStack
from PySide6.QtPdf import QPdfDocument
from PySide6.QtWidgets import QInputDialog, QPlainTextEdit, QPushButton

from question_tracker.application.library_service import LibraryService
from question_tracker.application.tracking_service import TrackingService
from question_tracker.app import MainWindow
from question_tracker.domain.library import FileRole
from question_tracker.domain.tracking import AttemptStatus
from question_tracker.paths import ApplicationPaths
from question_tracker.ui.home import HomeScreen
from question_tracker.ui.marking import MarkingWorkspace
from question_tracker.ui.tracker import TrackerScreen


def _create_ready_managed_set(
    tracking_service: TrackingService,
    library_service: LibraryService,
    application_paths: ApplicationPaths,
    pdf_factory,
    *,
    stem: str = "Sheet",
    references: tuple[str, ...] = ("Solutions",),
    leaf_count: int = 3,
):
    module = application_paths.managed_library_root / "Year 2" / "Analysis"
    main_path = pdf_factory(
        module / "Problem Sheets" / f"{stem}.pdf",
        (f"{stem} page one", f"{stem} page two"),
    )
    for reference in references:
        pdf_factory(
            module
            / "Problem Sheet Solutions"
            / f"{stem} {reference}.pdf",
            (f"{stem} {reference} page one", f"{stem} {reference} page two"),
        )
    library_service.refresh()
    main_file = next(
        item
        for item in library_service.list_files()
        if item.relative_path.name == main_path.name
    )
    question_set = library_service.question_sets_for_file(
        main_file.id, FileRole.MAIN
    )[0]
    leaves = tuple(
        tracking_service.create_question(question_set.id, str(index + 1))
        for index in range(leaf_count)
    )
    tracking_service.finish_question_set_setup(question_set.id)
    reference_files = tuple(
        item
        for item in library_service.list_files()
        if item.relative_path.name.startswith(f"{stem} ")
    )
    for reference_file in reference_files:
        library_service.associate_reference(question_set.id, reference_file.id)
    return question_set, leaves, main_file, reference_files


def _workspace(
    qtbot,
    tracking_service: TrackingService,
    library_service: LibraryService,
    *,
    question_set_id: str | None = None,
    question_id: str | None = None,
    undo_stack: QUndoStack | None = None,
) -> MarkingWorkspace:
    workspace = MarkingWorkspace(
        tracking_service,
        library_service,
        undo_stack or QUndoStack(),
        initial_question_set_id=question_set_id,
        initial_question_id=question_id,
    )
    qtbot.addWidget(workspace)
    workspace.resize(1200, 760)
    workspace.show()
    workspace.activateWindow()
    workspace.compact_tracker.setFocus()
    qtbot.wait(20)
    return workspace


def _attempt_count(service: TrackingService, question_id: str) -> int:
    return len(service.attempts_for_question(question_id))


def _select_home_question_set(home: HomeScreen, question_set_id: str) -> None:
    for row in range(home.question_set_list.count()):
        item = home.question_set_list.item(row)
        if str(item.data(Qt.ItemDataRole.UserRole)) == question_set_id:
            home.question_set_list.setCurrentRow(row)
            return
    raise AssertionError(f"Home question set not found: {question_set_id}")


def _wait_pdf_ready(qtbot, workspace: MarkingWorkspace) -> None:
    qtbot.waitUntil(
        lambda: (
            workspace.left_pane.document.status()
            == QPdfDocument.Status.Ready
            and workspace.right_pane.document.status()
            == QPdfDocument.Status.Ready
        ),
        timeout=3000,
    )


def _browser_texts(workspace: MarkingWorkspace, side: str) -> list[str]:
    browser = getattr(workspace, f"{side}_pane").browser
    texts: list[str] = []

    def collect(item) -> None:
        texts.append(item.text(0))
        for index in range(item.childCount()):
            collect(item.child(index))

    for index in range(browser.topLevelItemCount()):
        collect(browser.topLevelItem(index))
    return texts


def test_opening_set_loads_main_single_reference_and_selected_tracker(
    qtbot,
    tracking_service: TrackingService,
    library_service: LibraryService,
    application_paths: ApplicationPaths,
    pdf_factory,
) -> None:
    question_set, leaves, main_file, references = _create_ready_managed_set(
        tracking_service, library_service, application_paths, pdf_factory
    )

    workspace = _workspace(
        qtbot,
        tracking_service,
        library_service,
        question_set_id=question_set.id,
        question_id=leaves[1].id,
    )

    assert workspace.left_pane.current_file_id == main_file.id
    assert workspace.right_pane.current_file_id == references[0].id
    assert workspace.compact_tracker.active_question_set_id == question_set.id
    assert workspace.compact_tracker.selected_question_id == leaves[1].id


def test_multiple_references_are_highlighted_but_not_arbitrarily_opened(
    qtbot,
    tracking_service: TrackingService,
    library_service: LibraryService,
    application_paths: ApplicationPaths,
    pdf_factory,
) -> None:
    question_set, _, main_file, references = _create_ready_managed_set(
        tracking_service,
        library_service,
        application_paths,
        pdf_factory,
        references=("Solutions A", "Solutions B"),
    )

    workspace = _workspace(
        qtbot,
        tracking_service,
        library_service,
        question_set_id=question_set.id,
    )

    assert workspace.left_pane.current_file_id == main_file.id
    assert workspace.right_pane.mode == "files"
    assert workspace.right_pane.current_file_id is None
    browser_text = _browser_texts(workspace, "right")
    for reference in references:
        assert f"Associated \u2014 {reference.relative_path.name}" in browser_text


def test_missing_main_keeps_tracking_and_removing_reference_allows_single_auto_open(
    qtbot, tracking_service, library_service, application_paths, pdf_factory,
):
    from question_tracker.domain.library import FileAvailability

    question_set, leaves, main, refs = _create_ready_managed_set(
        tracking_service, library_service, application_paths, pdf_factory,
        references=("Solution A", "Solution B"),
    )
    library_service.path_for_file(main.id).unlink()
    library_service.refresh()
    library_service.remove_reference(question_set.id, refs[1].id)
    workspace = _workspace(
        qtbot, tracking_service, library_service, question_set_id=question_set.id,
    )
    assert library_service.get_file(main.id).availability is FileAvailability.MISSING
    assert "missing" in workspace.left_pane.document_state_label.text().lower()
    assert workspace.right_pane.current_file_id == refs[0].id
    assert library_service.path_for_file(refs[1].id).is_file()
    assert workspace.compact_tracker.active_question_set_id == question_set.id
    workspace.compact_tracker.mark_selected(AttemptStatus.PARTIAL, advance=False)
    assert tracking_service.attempts_for_question(leaves[0].id)[0].status is AttemptStatus.PARTIAL


def test_keyboard_marking_navigation_and_auto_advance(
    qtbot,
    tracking_service: TrackingService,
    library_service: LibraryService,
    application_paths: ApplicationPaths,
    pdf_factory,
) -> None:
    question_set, leaves, _, _ = _create_ready_managed_set(
        tracking_service,
        library_service,
        application_paths,
        pdf_factory,
        references=(),
    )
    workspace = _workspace(
        qtbot,
        tracking_service,
        library_service,
        question_set_id=question_set.id,
    )
    tracker = workspace.compact_tracker

    qtbot.keyClick(tracker, Qt.Key.Key_1)
    assert tracking_service.attempts_for_question(leaves[0].id)[0].status is AttemptStatus.CORRECT
    assert tracker.selected_question_id == leaves[1].id
    qtbot.keyClick(tracker, Qt.Key.Key_2)
    assert tracking_service.attempts_for_question(leaves[1].id)[0].status is AttemptStatus.PARTIAL
    assert tracker.selected_question_id == leaves[2].id
    qtbot.keyClick(tracker, Qt.Key.Key_3)
    assert tracking_service.attempts_for_question(leaves[2].id)[0].status is AttemptStatus.INCORRECT
    assert tracker.selected_question_id == leaves[2].id
    qtbot.keyClick(tracker, Qt.Key.Key_Left)
    assert tracker.selected_question_id == leaves[1].id
    qtbot.keyClick(tracker, Qt.Key.Key_Right)
    assert tracker.selected_question_id == leaves[2].id
    qtbot.keyClick(tracker, Qt.Key.Key_4)
    assert len(tracking_service.attempts_for_question(leaves[2].id)) == 2
    assert tracker.selected_question_id == leaves[2].id


def test_up_down_navigate_only_the_most_recently_active_pdf_pane(
    qtbot,
    tracking_service: TrackingService,
    library_service: LibraryService,
    application_paths: ApplicationPaths,
    pdf_factory,
) -> None:
    question_set, _, _, _ = _create_ready_managed_set(
        tracking_service, library_service, application_paths, pdf_factory
    )
    workspace = _workspace(
        qtbot,
        tracking_service,
        library_service,
        question_set_id=question_set.id,
    )
    _wait_pdf_ready(qtbot, workspace)

    qtbot.mouseClick(
        workspace.left_pane.fit_width_button, Qt.MouseButton.LeftButton
    )
    assert workspace.active_pdf_pane is workspace.left_pane
    assert "palette(highlight)" in workspace.left_pane.styleSheet()
    assert "palette(highlight)" not in workspace.right_pane.styleSheet()
    qtbot.keyClick(workspace.compact_tracker, Qt.Key.Key_Down)
    qtbot.waitUntil(lambda: workspace.left_pane.current_page == 1)
    assert workspace.right_pane.current_page == 0
    qtbot.keyClick(workspace.compact_tracker, Qt.Key.Key_Down)
    assert workspace.left_pane.current_page == 1
    qtbot.keyClick(workspace.compact_tracker, Qt.Key.Key_Up)
    qtbot.waitUntil(lambda: workspace.left_pane.current_page == 0)

    qtbot.mouseClick(
        workspace.right_pane.pdf_view.viewport(), Qt.MouseButton.LeftButton
    )
    assert workspace.active_pdf_pane is workspace.right_pane
    assert "palette(highlight)" in workspace.right_pane.styleSheet()
    assert "palette(highlight)" not in workspace.left_pane.styleSheet()
    qtbot.keyClick(workspace.compact_tracker, Qt.Key.Key_Up)
    assert workspace.right_pane.current_page == 0
    qtbot.keyClick(workspace.compact_tracker, Qt.Key.Key_Down)
    qtbot.waitUntil(lambda: workspace.right_pane.current_page == 1)
    assert workspace.left_pane.current_page == 0
    qtbot.keyClick(workspace.compact_tracker, Qt.Key.Key_Up)
    qtbot.waitUntil(lambda: workspace.right_pane.current_page == 0)


def test_pdf_page_shortcuts_are_suppressed_in_entry_controls(
    qtbot,
    tracking_service: TrackingService,
    library_service: LibraryService,
    application_paths: ApplicationPaths,
    pdf_factory,
) -> None:
    question_set, leaves, _, _ = _create_ready_managed_set(
        tracking_service, library_service, application_paths, pdf_factory
    )
    workspace = _workspace(
        qtbot,
        tracking_service,
        library_service,
        question_set_id=question_set.id,
    )
    _wait_pdf_ready(qtbot, workspace)

    qtbot.mouseClick(
        workspace.right_pane.pdf_view.viewport(), Qt.MouseButton.LeftButton
    )
    assert workspace.active_pdf_pane is workspace.right_pane
    search = workspace.left_pane.search_edit
    search.setFocus()
    qtbot.wait(10)
    assert workspace.active_pdf_pane is workspace.left_pane
    qtbot.keyClick(search, Qt.Key.Key_Down)
    qtbot.keyClick(search, Qt.Key.Key_Up)
    assert workspace.left_pane.current_page == 0

    page_spin = workspace.left_pane.page_spin
    page_spin.setFocus()
    qtbot.keyClick(page_spin, Qt.Key.Key_Down)
    assert workspace.left_pane.current_page == 0

    note_editor = QPlainTextEdit(workspace)
    note_editor.show()
    note_editor.setFocus()
    qtbot.keyClick(note_editor, Qt.Key.Key_Down)
    qtbot.keyClick(note_editor, Qt.Key.Key_Up)
    assert workspace.left_pane.current_page == 0
    assert _attempt_count(tracking_service, leaves[0].id) == 0


def test_click_marking_bookmark_note_and_shared_undo_redo(
    qtbot,
    monkeypatch,
    tracking_service: TrackingService,
    library_service: LibraryService,
    application_paths: ApplicationPaths,
    pdf_factory,
) -> None:
    question_set, leaves, _, _ = _create_ready_managed_set(
        tracking_service,
        library_service,
        application_paths,
        pdf_factory,
        references=(),
    )
    undo_stack = QUndoStack()
    workspace = _workspace(
        qtbot,
        tracking_service,
        library_service,
        question_set_id=question_set.id,
        undo_stack=undo_stack,
    )
    tracker = workspace.compact_tracker

    qtbot.mouseClick(
        tracker.status_buttons[AttemptStatus.CORRECT], Qt.MouseButton.LeftButton
    )
    assert _attempt_count(tracking_service, leaves[0].id) == 1
    assert tracker.selected_question_id == leaves[1].id
    qtbot.mouseClick(tracker.undo_button, Qt.MouseButton.LeftButton)
    assert _attempt_count(tracking_service, leaves[0].id) == 0
    qtbot.mouseClick(tracker.redo_button, Qt.MouseButton.LeftButton)
    assert _attempt_count(tracking_service, leaves[0].id) == 1

    tracker.select_question(leaves[1].id)
    qtbot.mouseClick(tracker.bookmark_button, Qt.MouseButton.LeftButton)
    assert tracking_service.get_question(leaves[1].id).bookmarked is True
    monkeypatch.setattr(
        QInputDialog,
        "getMultiLineText",
        lambda *args, **kwargs: ("Review this method", True),
    )
    qtbot.mouseClick(tracker.note_button, Qt.MouseButton.LeftButton)
    assert tracking_service.get_question(leaves[1].id).note == "Review this method"


def test_text_and_numeric_entry_suppress_marking_shortcuts(
    qtbot,
    tracking_service: TrackingService,
    library_service: LibraryService,
    application_paths: ApplicationPaths,
    pdf_factory,
) -> None:
    question_set, leaves, _, _ = _create_ready_managed_set(
        tracking_service, library_service, application_paths, pdf_factory
    )
    workspace = _workspace(
        qtbot,
        tracking_service,
        library_service,
        question_set_id=question_set.id,
    )

    workspace.left_pane.search_edit.setFocus()
    qtbot.keyClicks(workspace.left_pane.search_edit, "1234")
    assert workspace.left_pane.search_edit.text() == "1234"
    assert _attempt_count(tracking_service, leaves[0].id) == 0

    workspace.left_pane.page_spin.setFocus()
    qtbot.keyClick(workspace.left_pane.page_spin, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    qtbot.keyClicks(workspace.left_pane.page_spin, "12")
    assert _attempt_count(tracking_service, leaves[0].id) == 0

    note_editor = QPlainTextEdit(workspace)
    note_editor.show()
    note_editor.setFocus()
    qtbot.keyClicks(note_editor, "1234")
    assert note_editor.toPlainText() == "1234"
    assert _attempt_count(tracking_service, leaves[0].id) == 0


def test_switching_active_tracker_does_not_change_open_pdfs(
    qtbot,
    tracking_service: TrackingService,
    library_service: LibraryService,
    application_paths: ApplicationPaths,
    pdf_factory,
) -> None:
    first_set, _, main_file, references = _create_ready_managed_set(
        tracking_service, library_service, application_paths, pdf_factory
    )
    second_set, second_leaves, _, _ = _create_ready_managed_set(
        tracking_service,
        library_service,
        application_paths,
        pdf_factory,
        stem="Sheet Two",
        references=(),
    )
    workspace = _workspace(
        qtbot,
        tracking_service,
        library_service,
        question_set_id=first_set.id,
    )
    left_id = main_file.id
    right_id = references[0].id

    selector = workspace.compact_tracker.active_selector
    selector.setCurrentIndex(selector.findData(second_set.id))

    assert workspace.compact_tracker.active_question_set_id == second_set.id
    assert workspace.compact_tracker.selected_question_id == second_leaves[0].id
    assert workspace.left_pane.current_file_id == left_id
    assert workspace.right_pane.current_file_id == right_id


def test_compact_tracker_can_be_collapsed(
    qtbot,
    tracking_service: TrackingService,
    library_service: LibraryService,
) -> None:
    workspace = _workspace(qtbot, tracking_service, library_service)

    qtbot.mouseClick(
        workspace.tracker_toggle_button, Qt.MouseButton.LeftButton
    )
    assert workspace.compact_tracker.isHidden()
    assert workspace.tracker_toggle_button.text() == "Show tracker"
    qtbot.mouseClick(
        workspace.tracker_toggle_button, Qt.MouseButton.LeftButton
    )
    assert not workspace.compact_tracker.isHidden()


def test_narrow_marking_workspace_keeps_splitter_and_both_panes_functional(
    qtbot,
    tracking_service: TrackingService,
    library_service: LibraryService,
    application_paths: ApplicationPaths,
    pdf_factory,
) -> None:
    question_set, _, _, _ = _create_ready_managed_set(
        tracking_service, library_service, application_paths, pdf_factory
    )
    window = MainWindow(application_paths, tracking_service, library_service)
    qtbot.addWidget(window)
    window.show_marking(question_set.id)
    workspace = window.centralWidget()
    assert isinstance(workspace, MarkingWorkspace)
    window.resize(540, 650)
    window.show()
    _wait_pdf_ready(qtbot, workspace)

    workspace.document_splitter.setSizes((90, 430))
    qtbot.wait(30)
    sizes = workspace.document_splitter.sizes()

    assert window.minimumWidth() == 0
    assert window.width() <= 540
    assert workspace.minimumWidth() == 0
    assert sizes[0] < 180
    assert sizes[1] > sizes[0]
    assert workspace.left_pane.width() > 0
    assert workspace.right_pane.width() > 0
    assert (
        workspace.left_pane.toolbar_scroll.horizontalScrollBar().maximum()
        > 0
    )
    assert (
        workspace.right_pane.toolbar_scroll.horizontalScrollBar().maximum()
        > 0
    )
    workspace.left_pane.next_page()
    qtbot.waitUntil(lambda: workspace.left_pane.current_page == 1)
    assert workspace.right_pane.current_page == 0


def test_home_and_tracker_route_to_marking_and_back_without_losing_attempt(
    qtbot,
    tracking_service: TrackingService,
    library_service: LibraryService,
    application_paths: ApplicationPaths,
    pdf_factory,
) -> None:
    question_set, leaves, main_file, _ = _create_ready_managed_set(
        tracking_service,
        library_service,
        application_paths,
        pdf_factory,
        references=(),
    )
    window = MainWindow(application_paths, tracking_service, library_service)
    qtbot.addWidget(window)
    window.show()

    window.show_marking()
    direct_workspace = window.centralWidget()
    assert isinstance(direct_workspace, MarkingWorkspace)
    assert direct_workspace.left_pane.mode == "files"
    assert direct_workspace.right_pane.mode == "files"
    assert direct_workspace.compact_tracker.active_question_set_id is None

    window.show_home()
    home = window.centralWidget()
    assert isinstance(home, HomeScreen)
    _select_home_question_set(home, question_set.id)
    qtbot.mouseClick(home.marking_button, Qt.MouseButton.LeftButton)
    home_workspace = window.centralWidget()
    assert isinstance(home_workspace, MarkingWorkspace)
    assert home_workspace.left_pane.current_file_id == main_file.id
    assert home_workspace.compact_tracker.active_question_set_id == question_set.id

    window.show_tracker(question_set.id)
    tracker = window.centralWidget()
    assert isinstance(tracker, TrackerScreen)
    open_marking = tracker.findChild(QPushButton, "openMarkingButton")
    qtbot.mouseClick(open_marking, Qt.MouseButton.LeftButton)
    workspace = window.centralWidget()
    assert isinstance(workspace, MarkingWorkspace)
    assert workspace.undo_stack is window.undo_stack
    assert workspace.left_pane.current_file_id == main_file.id
    assert workspace.compact_tracker.active_question_set_id == question_set.id

    workspace.compact_tracker.mark_selected(AttemptStatus.CORRECT)
    qtbot.mouseClick(
        workspace.findChild(QPushButton, "markingBackButton"),
        Qt.MouseButton.LeftButton,
    )
    assert isinstance(window.centralWidget(), HomeScreen)
    window.show_tracker(question_set.id)
    assert _attempt_count(tracking_service, leaves[0].id) == 1


def test_home_selected_set_controls_marking_context_and_tree_expansion(
    qtbot,
    tracking_service: TrackingService,
    library_service: LibraryService,
    application_paths: ApplicationPaths,
    pdf_factory,
) -> None:
    first_set, _, first_main, first_references = _create_ready_managed_set(
        tracking_service,
        library_service,
        application_paths,
        pdf_factory,
        stem="Sheet A",
    )
    second_set, _, second_main, _ = _create_ready_managed_set(
        tracking_service,
        library_service,
        application_paths,
        pdf_factory,
        stem="Sheet B",
        references=("Solutions 1", "Solutions 2"),
    )
    window = MainWindow(application_paths, tracking_service, library_service)
    qtbot.addWidget(window)
    window.show()

    home = window.centralWidget()
    assert isinstance(home, HomeScreen)
    _select_home_question_set(home, first_set.id)
    qtbot.mouseClick(home.marking_button, Qt.MouseButton.LeftButton)
    first_workspace = window.centralWidget()
    assert isinstance(first_workspace, MarkingWorkspace)
    assert first_workspace.compact_tracker.active_question_set_id == first_set.id
    assert first_workspace.left_pane.current_file_id == first_main.id
    assert first_workspace.right_pane.current_file_id == first_references[0].id

    for pane in (first_workspace.left_pane, first_workspace.right_pane):
        assert pane.browser.folder_item(("Year 2",)).isExpanded()
        assert pane.browser.folder_item(("Year 2", "Analysis")).isExpanded()
    assert first_workspace.left_pane.browser.folder_item(
        ("Year 2", "Analysis", "Problem Sheets")
    ).isExpanded()
    assert first_workspace.right_pane.browser.folder_item(
        ("Year 2", "Analysis", "Problem Sheet Solutions")
    ).isExpanded()

    window.show_home()
    home = window.centralWidget()
    assert isinstance(home, HomeScreen)
    _select_home_question_set(home, second_set.id)
    qtbot.mouseClick(home.marking_button, Qt.MouseButton.LeftButton)
    second_workspace = window.centralWidget()
    assert isinstance(second_workspace, MarkingWorkspace)
    assert second_workspace.compact_tracker.active_question_set_id == second_set.id
    assert second_workspace.left_pane.current_file_id == second_main.id
    assert second_workspace.right_pane.mode == "files"
    assert second_workspace.right_pane.current_file_id is None
