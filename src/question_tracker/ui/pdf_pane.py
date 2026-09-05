from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QPointF, Qt, Signal
from PySide6.QtPdf import QPdfDocument, QPdfSearchModel
from PySide6.QtPdfWidgets import QPdfView
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from question_tracker.application.library_service import LibraryError, LibraryService
from question_tracker.domain.library import FileAvailability, LibraryModule, ManagedFile


PDF_ERROR_MESSAGES = {
    QPdfDocument.Error.Unknown: "The PDF could not be opened.",
    QPdfDocument.Error.DataNotYetAvailable: "The PDF data is not available yet.",
    QPdfDocument.Error.FileNotFound: "The PDF file could not be found.",
    QPdfDocument.Error.InvalidFileFormat: "The file is not a valid readable PDF.",
    QPdfDocument.Error.IncorrectPassword: "The PDF is password-protected or the password is incorrect.",
    QPdfDocument.Error.UnsupportedSecurityScheme: "The PDF uses an unsupported security scheme.",
}


class ManagedPdfBrowser(QTreeWidget):
    """Small managed-library browser used independently by each PDF pane."""

    file_activated = Signal(str)
    FILE_ID_ROLE = int(Qt.ItemDataRole.UserRole)
    FOLDER_PATH_ROLE = FILE_ID_ROLE + 1

    def __init__(
        self, library_service: LibraryService, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.library_service = library_service
        self._folder_items: dict[tuple[str, ...], QTreeWidgetItem] = {}
        self.setColumnCount(2)
        self.setHeaderLabels(("Managed PDFs", ""))
        self.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.header().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents
        )
        self.itemActivated.connect(self._activate_item)
        self.itemClicked.connect(self._item_clicked)

    def populate(
        self,
        *,
        focus_module: LibraryModule | None = None,
        highlighted_file_ids: Iterable[str] = (),
    ) -> None:
        highlighted = set(highlighted_file_ids)
        self.clear()
        self._folder_items = {}
        module_item: QTreeWidgetItem | None = None
        for managed_file in sorted(
            self.library_service.list_files(),
            key=lambda item: item.relative_path.as_posix().casefold(),
        ):
            parts = managed_file.relative_path.parts
            if len(parts) < 4:
                continue
            parent: QTreeWidgetItem | None = None
            for depth, part in enumerate(parts[:-1], start=1):
                key = parts[:depth]
                item = self._folder_items.get(key)
                if item is None:
                    item = QTreeWidgetItem([part, "Expand all"])
                    item.setData(0, self.FOLDER_PATH_ROLE, "/".join(key))
                    item.setToolTip(1, f"Expand everything under {part}")
                    self._folder_items[key] = item
                    if parent is None:
                        self.addTopLevelItem(item)
                    else:
                        parent.addChild(item)
                parent = item
                if (
                    focus_module is not None
                    and key
                    == (focus_module.year_name, focus_module.module_name)
                ):
                    module_item = item

            assert parent is not None
            label = parts[-1]
            if managed_file.id in highlighted:
                label = f"Associated — {label}"
            if managed_file.availability is FileAvailability.MISSING:
                label = f"{label} [Missing]"
            file_item = QTreeWidgetItem([label])
            file_item.setData(0, self.FILE_ID_ROLE, managed_file.id)
            file_item.setToolTip(0, managed_file.relative_path.as_posix())
            parent.addChild(file_item)
            if managed_file.id in highlighted:
                highlighted_parent = parent
                while highlighted_parent is not None:
                    highlighted_parent.setExpanded(True)
                    highlighted_parent = highlighted_parent.parent()

        if module_item is not None:
            parent = module_item
            while parent is not None:
                parent.setExpanded(True)
                parent = parent.parent()
            for index in range(module_item.childCount()):
                module_item.child(index).setExpanded(True)
            self.setCurrentItem(module_item)

    def folder_item(self, parts: tuple[str, ...]) -> QTreeWidgetItem | None:
        return self._folder_items.get(parts)

    def expand_descendants(self, item: QTreeWidgetItem) -> None:
        """Expand one folder branch without changing unrelated branches."""
        item.setExpanded(True)
        for index in range(item.childCount()):
            child = item.child(index)
            if child.childCount():
                self.expand_descendants(child)

    def selected_file_id(self) -> str | None:
        item = self.currentItem()
        if item is None:
            return None
        file_id = item.data(0, self.FILE_ID_ROLE)
        return str(file_id) if file_id is not None else None

    def _activate_item(self, item: QTreeWidgetItem, column: int) -> None:
        del column
        file_id = item.data(0, self.FILE_ID_ROLE)
        if file_id is not None:
            self.file_activated.emit(str(file_id))

    def _item_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        if column == 1 and item.data(0, self.FILE_ID_ROLE) is None:
            self.expand_descendants(item)


class PdfPane(QWidget):
    """One independent managed-file browser and QPdfView document session."""

    file_opened = Signal(str)
    mode_changed = Signal(str)
    interaction_occurred = Signal()

    DEFAULT_ZOOM_FACTOR = 1.0
    MIN_ZOOM_FACTOR = 0.25
    MAX_ZOOM_FACTOR = 4.0
    ZOOM_STEP = 1.2

    def __init__(
        self,
        library_service: LibraryService,
        pane_name: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.library_service = library_service
        self.pane_name = pane_name
        self.setObjectName(f"{pane_name}PdfPane")
        self.setMinimumWidth(0)
        self.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding
        )
        self.current_file_id: str | None = None
        self.mode = "files"
        self._focus_module: LibraryModule | None = None
        self._highlighted_file_ids: tuple[str, ...] = ()
        self._updating_page = False
        self._releasing_document = False
        self._zoom_factor = self.DEFAULT_ZOOM_FACTOR

        self.document = QPdfDocument(self)
        self.search_model = QPdfSearchModel(self)
        self.search_model.setDocument(self.document)
        self.pdf_view = QPdfView()
        self.pdf_view.setObjectName(f"{pane_name}PdfView")
        self.pdf_view.setDocument(self.document)
        self.pdf_view.setSearchModel(self.search_model)
        self.pdf_view.setPageMode(QPdfView.PageMode.MultiPage)
        self.pdf_view.setMinimumWidth(0)

        title = QLabel(pane_name.title())
        title.setStyleSheet("font-weight: bold;")
        self.mode_label = QLabel("Files mode")
        self.mode_label.setObjectName(f"{pane_name}PaneMode")
        pane_header = QHBoxLayout()
        pane_header.addWidget(title)
        pane_header.addStretch()
        pane_header.addWidget(self.mode_label)

        self.browser = ManagedPdfBrowser(library_service)
        self.browser.setObjectName(f"{pane_name}PdfBrowser")
        self.browser.file_activated.connect(self.open_managed_file)
        self.browser.currentItemChanged.connect(self._browser_selection_changed)
        self.open_button = QPushButton("Open PDF")
        self.open_button.setObjectName(f"{pane_name}OpenPdfButton")
        self.open_button.setEnabled(False)
        self.open_button.clicked.connect(self.open_selected_file)
        browser_page = QWidget()
        browser_layout = QVBoxLayout(browser_page)
        browser_layout.setContentsMargins(0, 0, 0, 0)
        browser_layout.addWidget(QLabel("Double-click a managed PDF, or select one and open it."))
        browser_layout.addWidget(self.browser)
        browser_layout.addWidget(self.open_button)

        self.back_button = QPushButton("< Files")
        self.back_button.setObjectName(f"{pane_name}BackToFilesButton")
        self.back_button.clicked.connect(self.show_files)
        self.file_title_label = QLabel("")
        self.file_title_label.setObjectName(f"{pane_name}PdfTitle")
        self.file_title_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.replace_button = QPushButton("Replace…")
        self.replace_button.setObjectName(f"{pane_name}ReplacePdfButton")
        self.replace_button.clicked.connect(self._replace_current_file)
        file_row = QHBoxLayout()
        file_row.addWidget(self.back_button)
        file_row.addWidget(self.file_title_label, 1)
        file_row.addWidget(self.replace_button)

        self.previous_page_button = QPushButton("Previous")
        self.previous_page_button.setObjectName(f"{pane_name}PreviousPageButton")
        self.previous_page_button.clicked.connect(self.previous_page)
        self.next_page_button = QPushButton("Next")
        self.next_page_button.setObjectName(f"{pane_name}NextPageButton")
        self.next_page_button.clicked.connect(self.next_page)
        self.page_spin = QSpinBox()
        self.page_spin.setObjectName(f"{pane_name}PageSpin")
        self.page_spin.setRange(1, 1)
        self.page_spin.valueChanged.connect(self.jump_to_page)
        self.page_count_label = QLabel("/ 0")
        self.zoom_out_button = QPushButton("−")
        self.zoom_out_button.setObjectName(f"{pane_name}PdfZoomOutButton")
        self.zoom_out_button.clicked.connect(self.zoom_out)
        self.zoom_in_button = QPushButton("+")
        self.zoom_in_button.setObjectName(f"{pane_name}PdfZoomInButton")
        self.zoom_in_button.clicked.connect(self.zoom_in)
        self.reset_zoom_button = QPushButton("100%")
        self.reset_zoom_button.setObjectName(f"{pane_name}PdfResetZoomButton")
        self.reset_zoom_button.clicked.connect(self.reset_zoom)
        self.fit_page_button = QPushButton("Fit page")
        self.fit_page_button.setObjectName(f"{pane_name}FitPageButton")
        self.fit_page_button.clicked.connect(self.fit_page)
        self.fit_width_button = QPushButton("Fit width")
        self.fit_width_button.setObjectName(f"{pane_name}FitWidthButton")
        self.fit_width_button.clicked.connect(self.fit_width)
        navigation_row = QHBoxLayout()
        for widget in (
            self.previous_page_button,
            self.next_page_button,
            self.page_spin,
            self.page_count_label,
            self.zoom_out_button,
            self.zoom_in_button,
            self.reset_zoom_button,
            self.fit_page_button,
            self.fit_width_button,
        ):
            navigation_row.addWidget(widget)
        navigation_row.addStretch()

        self.search_edit = QLineEdit()
        self.search_edit.setObjectName(f"{pane_name}PdfSearchEdit")
        self.search_edit.setPlaceholderText("Search PDF")
        self.search_edit.textChanged.connect(self._search_text_changed)
        self.previous_result_button = QPushButton("Previous result")
        self.previous_result_button.setObjectName(f"{pane_name}PreviousSearchButton")
        self.previous_result_button.clicked.connect(self.previous_search_result)
        self.next_result_button = QPushButton("Next result")
        self.next_result_button.setObjectName(f"{pane_name}NextSearchButton")
        self.next_result_button.clicked.connect(self.next_search_result)
        self.clear_search_button = QPushButton("Clear")
        self.clear_search_button.setObjectName(f"{pane_name}ClearSearchButton")
        self.clear_search_button.clicked.connect(self.search_edit.clear)
        self.search_result_label = QLabel("0 results")
        search_row = QHBoxLayout()
        search_row.addWidget(self.search_edit, 1)
        search_row.addWidget(self.previous_result_button)
        search_row.addWidget(self.next_result_button)
        search_row.addWidget(self.clear_search_button)
        search_row.addWidget(self.search_result_label)

        self.toolbar_content = QWidget()
        toolbar_layout = QVBoxLayout(self.toolbar_content)
        toolbar_layout.setContentsMargins(0, 0, 0, 0)
        toolbar_layout.addLayout(file_row)
        toolbar_layout.addLayout(navigation_row)
        toolbar_layout.addLayout(search_row)
        self.toolbar_content.setSizePolicy(
            QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed
        )
        self.toolbar_content.setMinimumWidth(
            self.toolbar_content.sizeHint().width()
        )
        self.toolbar_scroll = QScrollArea()
        self.toolbar_scroll.setObjectName(f"{pane_name}PdfToolbarScroll")
        self.toolbar_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.toolbar_scroll.setWidgetResizable(True)
        self.toolbar_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.toolbar_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.toolbar_scroll.setMinimumWidth(0)
        self.toolbar_scroll.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed
        )
        self.toolbar_scroll.setWidget(self.toolbar_content)
        toolbar_height = (
            self.toolbar_content.sizeHint().height()
            + self.toolbar_scroll.horizontalScrollBar().sizeHint().height()
            + self.toolbar_scroll.frameWidth() * 2
        )
        self.toolbar_scroll.setFixedHeight(toolbar_height)

        self.document_state_label = QLabel("Loading PDF…")
        self.document_state_label.setObjectName(f"{pane_name}PdfStateLabel")
        self.document_state_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.document_state_label.setWordWrap(True)
        self.document_stack = QStackedWidget()
        self.document_stack.addWidget(self.pdf_view)
        self.document_stack.addWidget(self.document_state_label)
        pdf_page = QWidget()
        pdf_layout = QVBoxLayout(pdf_page)
        pdf_layout.setContentsMargins(0, 0, 0, 0)
        pdf_layout.addWidget(self.toolbar_scroll)
        pdf_layout.addWidget(self.document_stack, 1)

        self.mode_stack = QStackedWidget()
        self.mode_stack.setObjectName(f"{pane_name}PaneStack")
        self.mode_stack.addWidget(browser_page)
        self.mode_stack.addWidget(pdf_page)
        self.mode_stack.setMinimumWidth(0)
        self.mode_stack.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding
        )

        layout = QVBoxLayout(self)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addLayout(pane_header)
        layout.addWidget(self.mode_stack, 1)

        self._document_controls = (
            self.previous_page_button,
            self.next_page_button,
            self.page_spin,
            self.zoom_out_button,
            self.zoom_in_button,
            self.reset_zoom_button,
            self.fit_page_button,
            self.fit_width_button,
            self.search_edit,
            self.previous_result_button,
            self.next_result_button,
            self.clear_search_button,
        )
        self.document.statusChanged.connect(self._document_status_changed)
        self.document.pageCountChanged.connect(self._page_count_changed)
        self.pdf_view.pageNavigator().currentPageChanged.connect(
            self._current_page_changed
        )
        self.search_model.countChanged.connect(self._search_count_changed)
        self._set_document_controls_enabled(False)
        self.replace_button.setVisible(False)
        self.refresh_browser()
        self._install_interaction_tracking()
        self.set_active(False)

    @property
    def current_page(self) -> int:
        return self.pdf_view.pageNavigator().currentPage()

    @property
    def zoom_factor(self) -> float:
        return self._zoom_factor

    def set_active(self, active: bool) -> None:
        border = "#7aa7d9" if active else "transparent"
        self.setStyleSheet(
            f"#{self.objectName()} {{ border: 1px solid {border}; }}"
        )

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() in (
            QEvent.Type.FocusIn,
            QEvent.Type.MouseButtonPress,
            QEvent.Type.Wheel,
        ):
            self.interaction_occurred.emit()
        return super().eventFilter(watched, event)

    def refresh_browser(
        self,
        *,
        focus_module: LibraryModule | None = None,
        highlighted_file_ids: Iterable[str] | None = None,
    ) -> None:
        if focus_module is not None:
            self._focus_module = focus_module
        if highlighted_file_ids is not None:
            self._highlighted_file_ids = tuple(highlighted_file_ids)
        self.browser.populate(
            focus_module=self._focus_module,
            highlighted_file_ids=self._highlighted_file_ids,
        )
        self._browser_selection_changed(self.browser.currentItem(), None)

    def open_selected_file(self) -> None:
        file_id = self.browser.selected_file_id()
        if file_id is not None:
            self.open_managed_file(file_id)

    def open_managed_file(self, file_id: str) -> bool:
        managed_file = self.library_service.get_file(file_id)
        self._release_document()
        self.current_file_id = managed_file.id
        self.file_title_label.setText(managed_file.relative_path.name)
        self.file_title_label.setToolTip(managed_file.relative_path.as_posix())
        self.mode = "pdf"
        self.mode_label.setText("PDF mode")
        self.mode_stack.setCurrentIndex(1)
        self.mode_changed.emit(self.mode)
        self._reset_document_state()

        path = self.library_service.path_for_file(file_id)
        if (
            managed_file.availability is FileAvailability.MISSING
            or not path.is_file()
        ):
            self._show_error(
                "This managed PDF is missing. Use Replace to restore it, or "
                "return to Files."
            )
            self.replace_button.setVisible(True)
            return False

        self.document_state_label.setText("Loading PDF…")
        self.document_stack.setCurrentWidget(self.document_state_label)
        error = self.document.load(str(path))
        if error != QPdfDocument.Error.None_:
            self._show_pdf_error(error)
            return False
        self._document_status_changed(self.document.status())
        self.file_opened.emit(file_id)
        return True

    def show_files(self) -> None:
        self._release_document()
        self.current_file_id = None
        self.mode = "files"
        self.mode_label.setText("Files mode")
        self.mode_stack.setCurrentIndex(0)
        self.mode_changed.emit(self.mode)

    def previous_page(self) -> None:
        self.jump_to_page(self.current_page)

    def next_page(self) -> None:
        self.jump_to_page(self.current_page + 2)

    def jump_to_page(self, page_number: int) -> None:
        if (
            self._updating_page
            or self.document.status() != QPdfDocument.Status.Ready
        ):
            return
        page_count = self.document.pageCount()
        if page_count <= 0:
            return
        page_index = max(0, min(page_number - 1, page_count - 1))
        self.pdf_view.pageNavigator().jump(page_index, QPointF(0, 0))

    def zoom_in(self) -> None:
        self._set_custom_zoom(self._zoom_factor * self.ZOOM_STEP)

    def zoom_out(self) -> None:
        self._set_custom_zoom(self._zoom_factor / self.ZOOM_STEP)

    def reset_zoom(self) -> None:
        self._set_custom_zoom(self.DEFAULT_ZOOM_FACTOR)

    def fit_page(self) -> None:
        self.pdf_view.setZoomMode(QPdfView.ZoomMode.FitInView)

    def fit_width(self) -> None:
        self.pdf_view.setZoomMode(QPdfView.ZoomMode.FitToWidth)

    def next_search_result(self) -> None:
        self._move_search_result(1)

    def previous_search_result(self) -> None:
        self._move_search_result(-1)

    def closeEvent(self, event) -> None:
        self._release_document()
        super().closeEvent(event)

    def _install_interaction_tracking(self) -> None:
        self.installEventFilter(self)
        for widget in self.findChildren(QWidget):
            widget.installEventFilter(self)

    def _browser_selection_changed(
        self,
        current: QTreeWidgetItem | None,
        previous: QTreeWidgetItem | None,
    ) -> None:
        del current, previous
        self.open_button.setEnabled(self.browser.selected_file_id() is not None)

    def _reset_document_state(self) -> None:
        self.search_edit.clear()
        self.pdf_view.setCurrentSearchResultIndex(-1)
        self._zoom_factor = self.DEFAULT_ZOOM_FACTOR
        self.pdf_view.setZoomMode(QPdfView.ZoomMode.Custom)
        self.pdf_view.setZoomFactor(self._zoom_factor)
        self.reset_zoom_button.setText("100%")
        self._updating_page = True
        try:
            self.page_spin.setRange(1, 1)
            self.page_spin.setValue(1)
        finally:
            self._updating_page = False
        self.page_count_label.setText("/ 0")
        self.search_result_label.setText("0 results")
        self._set_document_controls_enabled(False)
        self.replace_button.setVisible(False)

    def _release_document(self) -> None:
        self._releasing_document = True
        try:
            self.search_model.setSearchString("")
            self.pdf_view.setCurrentSearchResultIndex(-1)
            self.pdf_view.pageNavigator().clear()
            self.document.close()
        finally:
            self._releasing_document = False

    def _document_status_changed(self, status: QPdfDocument.Status) -> None:
        if self._releasing_document or self.mode != "pdf":
            return
        if status == QPdfDocument.Status.Ready:
            self.document_stack.setCurrentWidget(self.pdf_view)
            self._set_document_controls_enabled(True)
            self.replace_button.setVisible(False)
            self._page_count_changed(self.document.pageCount())
            if self.document.pageCount():
                self.jump_to_page(1)
        elif status == QPdfDocument.Status.Error:
            self._show_pdf_error(self.document.error())
        elif status in (QPdfDocument.Status.Loading, QPdfDocument.Status.Unloading):
            self.document_state_label.setText("Loading PDF…")
            self.document_stack.setCurrentWidget(self.document_state_label)

    def _show_pdf_error(self, error: QPdfDocument.Error) -> None:
        self._show_error(PDF_ERROR_MESSAGES.get(error, PDF_ERROR_MESSAGES[QPdfDocument.Error.Unknown]))
        self.replace_button.setVisible(True)

    def _show_error(self, message: str) -> None:
        self.document_state_label.setText(message)
        self.document_stack.setCurrentWidget(self.document_state_label)
        self._set_document_controls_enabled(False)

    def _page_count_changed(self, page_count: int) -> None:
        self._updating_page = True
        try:
            self.page_spin.setRange(1, max(1, page_count))
        finally:
            self._updating_page = False
        self.page_count_label.setText(f"/ {page_count}")
        self._current_page_changed(self.current_page)

    def _current_page_changed(self, page_index: int) -> None:
        page_count = self.document.pageCount()
        self._updating_page = True
        try:
            self.page_spin.setValue(max(1, min(page_index + 1, max(1, page_count))))
        finally:
            self._updating_page = False
        self.previous_page_button.setEnabled(page_count > 0 and page_index > 0)
        self.next_page_button.setEnabled(
            page_count > 0 and page_index < page_count - 1
        )

    def _set_custom_zoom(self, factor: float) -> None:
        self._zoom_factor = max(
            self.MIN_ZOOM_FACTOR, min(factor, self.MAX_ZOOM_FACTOR)
        )
        self.pdf_view.setZoomMode(QPdfView.ZoomMode.Custom)
        self.pdf_view.setZoomFactor(self._zoom_factor)
        self.reset_zoom_button.setText(f"{round(self._zoom_factor * 100)}%")

    def _search_text_changed(self, text: str) -> None:
        self.search_model.setSearchString(text)
        self.pdf_view.setCurrentSearchResultIndex(-1)
        self._search_count_changed()

    def _search_count_changed(self) -> None:
        count = self.search_model.count()
        self.search_result_label.setText(
            f"{count} result{'s' if count != 1 else ''}"
        )
        enabled = bool(self.search_edit.text()) and count > 0
        self.previous_result_button.setEnabled(enabled)
        self.next_result_button.setEnabled(enabled)

    def _move_search_result(self, offset: int) -> None:
        count = self.search_model.count()
        if count <= 0:
            return
        current = self.pdf_view.currentSearchResultIndex()
        if current < 0:
            target = 0 if offset > 0 else count - 1
        else:
            target = (current + offset) % count
        self.pdf_view.setCurrentSearchResultIndex(target)
        link = self.search_model.resultAtIndex(target)
        if link.isValid():
            self.pdf_view.pageNavigator().jump(link)

    def _set_document_controls_enabled(self, enabled: bool) -> None:
        for control in self._document_controls:
            control.setEnabled(enabled)
        if enabled:
            self._current_page_changed(self.current_page)
            self._search_count_changed()

    def _replace_current_file(self) -> None:
        if self.current_file_id is None:
            return
        filename, _ = QFileDialog.getOpenFileName(
            self, "Replace managed PDF", "", "PDF files (*.pdf)"
        )
        if not filename:
            return
        file_id = self.current_file_id
        try:
            self.library_service.replace_file(file_id, Path(filename))
        except LibraryError as error:
            QMessageBox.warning(self, "Replace PDF", str(error))
            return
        self.refresh_browser()
        self.open_managed_file(file_id)
