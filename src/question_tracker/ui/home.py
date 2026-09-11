from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLayout,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from question_tracker.application.home_view import (
    HomeSnapshot,
    ModuleSummary,
    build_home_snapshot,
)
from question_tracker.application.library_service import LibraryError, LibraryService
from question_tracker.application.tracking_service import TrackingService
from question_tracker.domain.library import (
    DocumentType,
    FileAvailability,
    FileRole,
    ManagedFile,
)
from question_tracker.domain.tracking import QuestionSet, SetupState
from question_tracker.paths import ApplicationPaths
from question_tracker.ui.import_dialog import ImportPdfDialog
from question_tracker.ui.file_recovery import confirm_pdf_replacement
from question_tracker.ui.reference_dialog import ReferenceDialog
from question_tracker.ui.layout_spacing import (
    PAGE_MARGIN,
    SECTION_SPACING,
    compact_layout,
)
from question_tracker.ui.pdf_pane import ManagedPdfBrowser
from question_tracker.ui.question_editor import QuestionEditorDialog
from question_tracker.ui.preferences import UiPreferences
from question_tracker.application.module_group_service import ModuleGroupService, ModuleGroupError
from question_tracker.ui.flow_layout import FlowLayout
from question_tracker.ui.module_organisation import ModuleDropArea, ModuleDragHandle


class ModuleCard(QPushButton):
    """Compact clickable summary for one managed-library module."""

    def __init__(
        self,
        summary: ModuleSummary,
        open_tracker: Callable[[str], None] | None,
        parent: QWidget | None = None,
    ) -> None:
        sheets = summary.problem_sheets
        papers = summary.past_papers
        text = (
            f"{summary.module.module_name}\n"
            f"{summary.module.year_name}\n"
            f"Problem Sheets: {sheets.complete} / {sheets.total} attempted"
            f"\n  {sheets.mastered} mastered\n"
            f"Past Papers: {papers.complete} / {papers.total} attempted"
            f"\n  {papers.mastered} mastered"
        )
        if summary.needs_setup_count:
            text += f"\nNeeds setup: {summary.needs_setup_count}"
        super().__init__(text, parent)
        self.summary = summary
        self.setObjectName("moduleCard")
        self.setProperty("yearName", summary.module.year_name)
        self.setProperty("moduleName", summary.module.module_name)
        self.setMinimumWidth(270)
        self.setMaximumWidth(330)
        self.setStyleSheet("QPushButton#moduleCard { text-align: left; padding: 8px; }")
        self.setToolTip(
            "Open this module in Question Tracker"
            if summary.first_ready_question_set_id is not None
            else "Set up at least one question set to open this module in Tracker"
        )
        enabled = (
            open_tracker is not None
            and summary.first_ready_question_set_id is not None
        )
        self.setEnabled(enabled)
        if enabled:
            self.clicked.connect(
                lambda checked=False: open_tracker(
                    summary.first_ready_question_set_id
                )
            )


class HomeScreen(QWidget):
    """Home dashboard for module progress and managed-library organisation."""

    def __init__(
        self,
        service: TrackingService,
        paths: ApplicationPaths,
        parent: QWidget | None = None,
        *,
        open_tracker: Callable[[str], None] | None = None,
        open_review: Callable[[], None] | None = None,
        open_marking: Callable[[str | None], None] | None = None,
        open_file_marking: Callable[[str], None] | None = None,
        library_service: LibraryService | None = None,
        preferences: UiPreferences | None = None,
        expanded_roots: set[tuple[str, ...]] | None = None,
    ) -> None:
        super().__init__(parent)
        self.preferences = preferences or UiPreferences()
        self.module_groups = ModuleGroupService(service.database)
        self.service = service
        self.paths = paths
        self.open_tracker = open_tracker
        self.open_marking = open_marking
        self.open_file_marking = open_file_marking
        self.library_service = library_service or LibraryService(
            service.database, paths.managed_library_root
        )
        self.snapshot = HomeSnapshot((), (), ())
        self.module_cards: dict[tuple[str, str], ModuleCard] = {}

        heading = QLabel("Question Tracker")
        heading.setObjectName("homeHeading")
        heading.setStyleSheet("font-size: 22px; font-weight: bold;")
        self.generic_marking_button = QPushButton("Open Marking")
        self.generic_marking_button.setObjectName("openGenericMarkingButton")
        self.generic_marking_button.setEnabled(open_marking is not None)
        if open_marking is not None:
            self.generic_marking_button.clicked.connect(
                lambda checked=False: open_marking(None)
            )
        self.refresh_button = QPushButton("Refresh Library")
        self.refresh_button.setObjectName("refreshHomeButton")
        self.refresh_button.clicked.connect(self.refresh)
        header = QHBoxLayout()
        header.addWidget(heading)
        header.addStretch()
        self.review_button = QPushButton("Statistics / Review")
        self.review_button.setObjectName("openStatisticsReviewButton")
        self.review_button.setEnabled(open_review is not None)
        if open_review is not None:
            self.review_button.clicked.connect(lambda checked=False: open_review())
        header.addWidget(self.review_button)
        header.addWidget(self.generic_marking_button)
        header.addWidget(self.refresh_button)

        library_path = QLabel(f"Managed library: {paths.managed_library_root}")
        library_path.setWordWrap(True)

        self.module_container = QWidget()
        self.module_card_layout = QVBoxLayout(self.module_container)
        self.module_card_layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        compact_layout(self.module_card_layout)
        self.module_card_layout.setSpacing(SECTION_SPACING)
        self.module_card_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.module_scroll = QScrollArea()
        self.module_scroll.setObjectName("moduleCardScroll")
        self.module_scroll.setWidgetResizable(True)
        self.module_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.module_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.module_scroll.setWidget(self.module_container)
        self.module_scroll.setFrameShape(QFrame.Shape.NoFrame)

        self.library_browser = ManagedPdfBrowser(
            self.library_service,
            expanded_roots=expanded_roots if expanded_roots is not None else set(),
        )
        self.library_browser.setObjectName("homeLibraryBrowser")
        self.library_browser.currentItemChanged.connect(
            self._browser_selection_changed
        )
        self.library_browser.file_activated.connect(self._open_browser_file_marking)
        self.selected_file_label = QLabel("Select a managed PDF")
        self.selected_file_label.setObjectName("homeSelectedFileLabel")
        self.selected_file_label.setWordWrap(True)
        self.quick_tracker_button = QPushButton("Open Question Tracker")
        self.quick_tracker_button.setObjectName("homeBrowserTrackerButton")
        self.quick_tracker_button.clicked.connect(self._open_browser_file_tracker)
        self.quick_marking_button = QPushButton("Open Marking")
        self.quick_marking_button.setObjectName("homeBrowserMarkingButton")
        self.quick_marking_button.clicked.connect(
            lambda checked=False: self._open_browser_file_marking()
        )
        self.quick_setup_button = QPushButton("Set Up / Edit Questions")
        self.quick_setup_button.setObjectName("homeBrowserSetupButton")
        self.quick_setup_button.clicked.connect(self._edit_browser_question_set)
        self.quick_replace_button = QPushButton("Replace PDF...")
        self.quick_replace_button.setObjectName("homeBrowserReplaceButton")
        self.quick_replace_button.clicked.connect(self._replace_browser_file)
        self.references_button = QPushButton("Manage References...")
        self.references_button.setObjectName("homeManageReferencesButton")
        self.references_button.clicked.connect(self._manage_references)
        browser_actions = QHBoxLayout()
        for button in (
            self.quick_tracker_button,
            self.quick_marking_button,
            self.quick_setup_button,
            self.quick_replace_button,
            self.references_button,
        ):
            browser_actions.addWidget(button)
        browser_actions.addStretch()
        browser_group = QGroupBox("Managed library")
        browser_layout = QVBoxLayout(browser_group)
        browser_layout.addWidget(self.library_browser)
        browser_layout.addWidget(self.selected_file_label)
        browser_layout.addLayout(browser_actions)

        self.needs_setup_tree = QTreeWidget()
        self.needs_setup_tree.setObjectName("needsSetupTree")
        self.needs_setup_tree.setColumnCount(5)
        self.needs_setup_tree.setHeaderLabels(
            ("Year", "Module", "Document", "Type / status", "")
        )
        needs_group = QGroupBox("Needs Setup")
        needs_layout = QVBoxLayout(needs_group)
        needs_layout.addWidget(self.needs_setup_tree)

        # Keep the existing all-set selector as a compact navigation convenience.
        self.question_set_list = QListWidget()
        self.question_set_list.setObjectName("questionSetList")
        self.question_set_list.itemDoubleClicked.connect(self._open_item)
        self.open_button = QPushButton("Open selected")
        self.open_button.setObjectName("openQuestionSetButton")
        self.open_button.clicked.connect(self.open_selected_question_set)
        self.marking_button = QPushButton("Mark selected")
        self.marking_button.setObjectName("openMarkingFromHomeButton")
        self.marking_button.setEnabled(open_marking is not None)
        if open_marking is not None:
            self.marking_button.clicked.connect(self.open_marking_workspace)
        all_sets_group = QGroupBox("All question sets")
        all_sets_layout = QVBoxLayout(all_sets_group)
        all_sets_layout.addWidget(self.question_set_list)
        all_sets_actions = QHBoxLayout()
        all_sets_actions.addWidget(self.open_button)
        all_sets_actions.addWidget(self.marking_button)
        all_sets_actions.addStretch()
        all_sets_layout.addLayout(all_sets_actions)

        right_column = QSplitter(Qt.Orientation.Vertical)
        right_column.setObjectName('homeRightSplitter')
        right_column.addWidget(needs_group)
        right_column.addWidget(all_sets_group)
        content_splitter = QSplitter(Qt.Orientation.Horizontal)
        content_splitter.setObjectName("homeContentSplitter")
        content_splitter.addWidget(browser_group)
        content_splitter.addWidget(right_column)
        content_splitter.setSizes((700, 400))

        self.add_problem_sheet_button = QPushButton("Add Problem Sheet")
        self.add_problem_sheet_button.setObjectName("addProblemSheetButton")
        self.add_problem_sheet_button.clicked.connect(
            lambda: self._open_import_dialog(DocumentType.PROBLEM_SHEET)
        )
        self.add_solution_button = QPushButton("Add Solution")
        self.add_solution_button.setObjectName("addSolutionButton")
        self.add_solution_button.clicked.connect(
            lambda: self._open_import_dialog(DocumentType.SOLUTION)
        )
        self.add_past_paper_button = QPushButton("Add Past Paper")
        self.add_past_paper_button.setObjectName("addPastPaperButton")
        self.add_past_paper_button.clicked.connect(
            lambda: self._open_import_dialog(DocumentType.PAST_PAPER)
        )
        self.create_year_button = QPushButton("Create Year...")
        self.create_year_button.setObjectName("createYearButton")
        self.create_year_button.clicked.connect(self._create_year)
        self.create_module_button = QPushButton("Create Module...")
        self.create_module_button.setObjectName("createModuleButton")
        self.create_module_button.clicked.connect(self._create_module)
        import_actions = QHBoxLayout()
        import_actions.addWidget(QLabel("Import PDF:"))
        for button in (
            self.add_problem_sheet_button,
            self.add_solution_button,
            self.add_past_paper_button,
        ):
            import_actions.addWidget(button)
        import_actions.addStretch()
        import_actions.addWidget(QLabel("Create:"))
        import_actions.addWidget(self.create_year_button)
        import_actions.addWidget(self.create_module_button)
        import_group = QGroupBox("Add to managed library")
        import_layout = QVBoxLayout(import_group)
        import_layout.addLayout(import_actions)

        layout = QVBoxLayout(self)
        layout.addLayout(header)
        layout.addWidget(library_path)
        modules_heading = QLabel("Modules / progress")
        heading_font = modules_heading.font()
        heading_font.setBold(True)
        modules_heading.setFont(heading_font)
        layout.addWidget(modules_heading)
        panels = QSplitter(Qt.Orientation.Vertical)
        panels.setObjectName('homePanelsSplitter')
        panels.addWidget(self.module_scroll)
        panels.addWidget(content_splitter)
        panels.setStretchFactor(0, 0)
        panels.setStretchFactor(1, 1)
        layout.addWidget(panels, 1)
        layout.addWidget(import_group)

        for group in (browser_group, needs_group, all_sets_group, import_group):
            group.setFlat(True)
            compact_layout(group.layout(), margin=PAGE_MARGIN)
        for row in (header, browser_actions, all_sets_actions, import_actions):
            compact_layout(row)
        compact_layout(layout, margin=PAGE_MARGIN)
        layout.setSpacing(SECTION_SPACING)
        for tree in (self.library_browser, self.needs_setup_tree):
            tree.setAlternatingRowColors(True)
            tree.setFrameShape(QFrame.Shape.NoFrame)
        self.question_set_list.setFrameShape(QFrame.Shape.NoFrame)

        self.refresh()
        self.preferences.bind_splitter(content_splitter, 'home/content', (700, 400))
        self.preferences.bind_splitter(right_column, 'home/right', (350, 170))
        self.preferences.bind_splitter(
            panels, 'home/panels', (self.module_scroll.minimumHeight(), 520)
        )

    def refresh(self) -> None:
        selected_id = self._selected_question_set_id()
        try:
            scan_result = self.library_service.refresh()
            folders = self.library_service.list_folders()
        except LibraryError as error:
            QMessageBox.warning(self, "Refresh Library", str(error))
            return
        self.snapshot = build_home_snapshot(
            self.service, self.library_service, scan_result
        )
        self._populate_module_cards()
        self.library_browser.populate(
            years=self.snapshot.years,
            modules=(summary.module for summary in self.snapshot.modules),
            folders=folders,
        )
        self._populate_needs_setup()
        self._populate_question_sets(selected_id)
        self._browser_selection_changed(self.library_browser.currentItem(), None)

    def open_selected_question_set(self) -> None:
        question_set_id = self._selected_question_set_id()
        if question_set_id is None:
            return
        question_set = self.service.get_question_set(question_set_id)
        if (
            question_set.setup_state is SetupState.READY
            and self.open_tracker is not None
        ):
            self.open_tracker(question_set_id)
            return
        self._edit_question_set(question_set_id)

    def open_marking_workspace(self) -> None:
        if self.open_marking is not None:
            self.open_marking(self._selected_question_set_id())

    def _populate_module_cards(self) -> None:
        while self.module_card_layout.count():
            item = self.module_card_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().hide()
                item.widget().deleteLater()
        self.module_cards = {}
        self.module_sections = {}
        groups, assignments = self.module_groups.snapshot()
        years = set(self.snapshot.years) | {s.module.year_name for s in self.snapshot.modules}
        for year in sorted(years, key=str.casefold):
            year_widget = QWidget()
            year_widget.setProperty("yearName", year)
            year_layout = QVBoxLayout(year_widget)
            compact_layout(year_layout)
            heading_row = QHBoxLayout()
            heading = QLabel(year or "Other")
            heading.setObjectName("moduleYearHeading")
            font = heading.font()
            font.setBold(True)
            heading.setFont(font)
            heading_row.addWidget(heading)
            heading_row.addStretch()
            create = QPushButton("+ New Group")
            create.clicked.connect(lambda checked=False, y=year: self._create_module_group(y))
            heading_row.addWidget(create)
            year_layout.addLayout(heading_row)
            year_groups = [group for group in groups if group.year_key == year.casefold()]
            summaries = [s for s in self.snapshot.modules if s.module.year_name == year]
            for group in [*year_groups, None]:
                group_id = group.id if group else None
                area = ModuleDropArea(year, group_id)
                area.module_dropped.connect(self._assign_module_group)
                self.module_sections[(year, group_id)] = area
                section_layout = QVBoxLayout(area)
                compact_layout(section_layout)
                row = QHBoxLayout()
                if group or year_groups:
                    row.addWidget(QLabel(group.name if group else "Ungrouped"))
                row.addStretch()
                if group:
                    rename = QPushButton("Rename…")
                    rename.clicked.connect(lambda checked=False, g=group: self._rename_module_group(g))
                    delete = QPushButton("Delete…")
                    delete.clicked.connect(lambda checked=False, g=group: self._delete_module_group(g))
                    row.addWidget(rename)
                    row.addWidget(delete)
                section_layout.addLayout(row)
                cards = QWidget()
                flow = FlowLayout(cards)
                for summary in summaries:
                    key = (summary.module.year_name, summary.module.module_name)
                    assigned = assignments.get(tuple(part.casefold() for part in key))
                    if assigned != group_id:
                        continue
                    wrapper = QWidget()
                    wrapper.setMinimumWidth(270)
                    wrapper.setMaximumWidth(330)
                    card_layout = QVBoxLayout(wrapper)
                    compact_layout(card_layout)
                    card_layout.addWidget(ModuleDragHandle(summary.module))
                    card = ModuleCard(summary, self.open_tracker)
                    self.module_cards[key] = card
                    card_layout.addWidget(card)
                    flow.addWidget(wrapper)
                if not flow.count():
                    section_layout.addWidget(QLabel("Drop modules here" if group or year_groups else "No modules yet"))
                section_layout.addWidget(cards)
                year_layout.addWidget(area)
            self.module_card_layout.addWidget(year_widget)
            # Include new widgets in height-for-width calculations immediately.
            year_widget.show()
        # Scroll vertically as years/groups grow; never force all rows into the
        # splitter minimum. Keep enough viewport height for a usable card.
        card_height = max((card.sizeHint().height() for card in self.module_cards.values()), default=80)
        self.module_scroll.setMinimumHeight(card_height + 60)
        self.module_container.updateGeometry()

    def _create_module_group(self, year: str) -> None:
        name, accepted = QInputDialog.getText(self, "New Group", "Group name:")
        if accepted:
            self._group_change(lambda: self.module_groups.create(year, name))

    def _rename_module_group(self, group) -> None:
        name, accepted = QInputDialog.getText(self, "Rename Group", "Group name:", text=group.name)
        if accepted:
            self._group_change(lambda: self.module_groups.rename(group.id, name))

    def _delete_module_group(self, group) -> None:
        if QMessageBox.question(
            self, "Delete Group", f'Delete "{group.name}"? Modules return to Ungrouped. '
            "Modules, PDFs and tracking data will not be deleted.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        ) == QMessageBox.StandardButton.Yes:
            self._group_change(lambda: self.module_groups.delete(group.id))

    def _assign_module_group(self, module, group_id) -> None:
        # Accept only identities represented by the current Home snapshot.
        if module not in {summary.module for summary in self.snapshot.modules}:
            return
        self._group_change(lambda: self.module_groups.assign(module, group_id))

    def _group_change(self, operation) -> None:
        try:
            operation()
        except ModuleGroupError as error:
            QMessageBox.warning(self, "Module organisation", str(error))
            return
        # Complete drag/drop or button callbacks before retiring their widgets.
        QTimer.singleShot(0, self, self._populate_module_cards)

    def _populate_needs_setup(self) -> None:
        self.needs_setup_tree.clear()
        for setup_item in self.snapshot.needs_setup:
            question_set = setup_item.question_set
            kind = question_set.kind.value.replace("_", " ").title()
            status = f"{kind} — {'Missing' if setup_item.is_missing else 'Needs setup'}"
            item = QTreeWidgetItem(
                [
                    setup_item.module.year_name,
                    setup_item.module.module_name,
                    question_set.display_name,
                    status,
                    "",
                ]
            )
            item.setData(0, Qt.ItemDataRole.UserRole, question_set.id)
            self.needs_setup_tree.addTopLevelItem(item)
            button = QPushButton("Set Up")
            button.setObjectName(f"setupQuestionSet-{question_set.id}")
            button.setProperty("questionSetId", question_set.id)
            button.clicked.connect(
                lambda checked=False, question_set_id=question_set.id: (
                    self._edit_question_set(question_set_id)
                )
            )
            self.needs_setup_tree.setItemWidget(item, 4, button)
        self.needs_setup_tree.resizeColumnToContents(0)
        self.needs_setup_tree.resizeColumnToContents(1)
        self.needs_setup_tree.resizeColumnToContents(3)

    def _populate_question_sets(self, selected_id: str | None) -> None:
        self.question_set_list.clear()
        selected_item: QListWidgetItem | None = None
        for question_set in self.service.list_question_sets():
            module = self.library_service.module_for_question_set(question_set.id)
            context = (
                f"{module.year_name} / {module.module_name} / "
                if module is not None
                else ""
            )
            state = (
                "Needs setup"
                if question_set.setup_state is SetupState.NEEDS_SETUP
                else "Ready"
            )
            kind = question_set.kind.value.replace("_", " ").title()
            main_files = self.library_service.files_for_question_set(
                question_set.id, FileRole.MAIN
            )
            missing = (
                " — Missing"
                if main_files
                and main_files[0].availability is FileAvailability.MISSING
                else ""
            )
            item = QListWidgetItem(
                f"{context}{question_set.display_name} — {kind} — {state}{missing}"
            )
            item.setData(Qt.ItemDataRole.UserRole, question_set.id)
            self.question_set_list.addItem(item)
            if question_set.id == selected_id:
                selected_item = item
        if selected_item is not None:
            self.question_set_list.setCurrentItem(selected_item)
        elif self.question_set_list.count():
            self.question_set_list.setCurrentRow(0)
        enabled = self.question_set_list.count() > 0
        self.open_button.setEnabled(enabled)
        self.marking_button.setEnabled(enabled and self.open_marking is not None)

    def _browser_selection_changed(
        self, current: QTreeWidgetItem | None, previous: QTreeWidgetItem | None
    ) -> None:
        del current, previous
        managed_file = self._selected_managed_file()
        question_set = self._question_set_for_file(managed_file)
        self.quick_tracker_button.setEnabled(
            question_set is not None
            and question_set.setup_state is SetupState.READY
            and self.open_tracker is not None
        )
        self.quick_setup_button.setEnabled(question_set is not None)
        self.references_button.setEnabled(question_set is not None)
        self.quick_marking_button.setEnabled(
            managed_file is not None
            and (
                (question_set is not None and self.open_marking is not None)
                or (question_set is None and self.open_file_marking is not None)
            )
        )
        self.quick_replace_button.setEnabled(managed_file is not None)
        if managed_file is None:
            self.selected_file_label.setText("Select a managed PDF")
            return
        status = (
            "Missing"
            if managed_file.availability is FileAvailability.MISSING
            else "Present"
        )
        self.selected_file_label.setText(
            f"{managed_file.relative_path.as_posix()} — {status}"
        )
        self.quick_setup_button.setText(
            "Set Up"
            if question_set is not None
            and question_set.setup_state is SetupState.NEEDS_SETUP
            else "Edit Questions"
        )

    def _open_browser_file_tracker(self) -> None:
        question_set = self._question_set_for_file(self._selected_managed_file())
        if (
            question_set is not None
            and question_set.setup_state is SetupState.READY
            and self.open_tracker is not None
        ):
            self.open_tracker(question_set.id)

    def _open_browser_file_marking(self, file_id: str | None = None) -> None:
        managed_file = (
            self.library_service.get_file(file_id)
            if file_id is not None
            else self._selected_managed_file()
        )
        if managed_file is None:
            return
        question_set = self._question_set_for_file(managed_file)
        if question_set is not None and self.open_marking is not None:
            self.open_marking(question_set.id)
        elif self.open_file_marking is not None:
            self.open_file_marking(managed_file.id)

    def _edit_browser_question_set(self) -> None:
        question_set = self._question_set_for_file(self._selected_managed_file())
        if question_set is not None:
            self._edit_question_set(question_set.id)

    def _manage_references(self) -> None:
        question_set = self._question_set_for_file(self._selected_managed_file())
        if question_set is not None:
            ReferenceDialog(self.library_service, question_set.id, self).exec()

    def _replace_browser_file(self) -> None:
        managed_file = self._selected_managed_file()
        if managed_file is None:
            return
        filename, _ = QFileDialog.getOpenFileName(
            self, "Replace managed PDF", "", "PDF files (*.pdf)"
        )
        if not filename:
            return
        try:
            if not confirm_pdf_replacement(self, self.library_service, managed_file.id):
                return
            self.library_service.replace_file(managed_file.id, Path(filename))
        except LibraryError as error:
            QMessageBox.warning(self, "Replace PDF", str(error))
            return
        self.refresh()

    def _open_import_dialog(self, document_type: DocumentType) -> None:
        dialog = ImportPdfDialog(self.library_service, document_type, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.refresh()

    def _create_year(self) -> None:
        name, accepted = QInputDialog.getText(self, "Create Year", "Year name")
        if not accepted:
            return
        try:
            self.library_service.create_year(name)
        except LibraryError as error:
            QMessageBox.warning(self, "Create Year", str(error))
            return
        self.refresh()

    def _create_module(self) -> None:
        if not self.snapshot.years:
            QMessageBox.warning(self, "Create Module", "Create a Year first.")
            return
        year, accepted = QInputDialog.getItem(
            self,
            "Create Module",
            "Year",
            self.snapshot.years,
            editable=False,
        )
        if not accepted:
            return
        name, accepted = QInputDialog.getText(
            self, "Create Module", "Module name"
        )
        if not accepted:
            return
        try:
            self.library_service.create_module(year, name)
        except LibraryError as error:
            QMessageBox.warning(self, "Create Module", str(error))
            return
        self.refresh()

    def _edit_question_set(self, question_set_id: str) -> None:
        editor = QuestionEditorDialog(self.service, question_set_id, self)
        editor.exec()
        self.refresh()

    def _open_item(self, item: QListWidgetItem) -> None:
        self.question_set_list.setCurrentItem(item)
        self.open_selected_question_set()

    def _selected_question_set_id(self) -> str | None:
        item = self.question_set_list.currentItem()
        if item is None:
            return None
        return str(item.data(Qt.ItemDataRole.UserRole))

    def _selected_managed_file(self) -> ManagedFile | None:
        file_id = self.library_browser.selected_file_id()
        return self.library_service.get_file(file_id) if file_id is not None else None

    def _question_set_for_file(
        self, managed_file: ManagedFile | None
    ) -> QuestionSet | None:
        if managed_file is None:
            return None
        question_sets = self.library_service.question_sets_for_file(
            managed_file.id, FileRole.MAIN
        )
        return question_sets[0] if question_sets else None
