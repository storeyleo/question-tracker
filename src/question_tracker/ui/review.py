from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QPushButton, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

from question_tracker.application.review_service import ReviewFilter, ReviewService
from question_tracker.ui.layout_spacing import PAGE_MARGIN, compact_layout


class ReviewScreen(QWidget):
    def __init__(self, service: ReviewService, *, on_back, open_tracker, open_marking,
                 initial_module=None, initial_filter=ReviewFilter.REATTEMPT, parent=None):
        super().__init__(parent)
        self.service = service
        self.open_tracker = open_tracker
        self.open_marking = open_marking
        self.initial_module = initial_module
        self.summaries = ()
        back = QPushButton("Home")
        back.clicked.connect(on_back)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh)
        self.module_combo = QComboBox()
        self.module_combo.setObjectName("reviewModuleCombo")
        self.module_combo.currentIndexChanged.connect(self._render_module)
        header = QHBoxLayout()
        for widget in (back, QLabel("Statistics / Review"), QLabel("Module:"), self.module_combo, refresh):
            header.addWidget(widget)
        header.addStretch()
        self.progress_label = QLabel()
        self.progress_label.setWordWrap(True)
        self.breakdown = QTreeWidget()
        self.breakdown.setRootIsDecorated(False)
        self.breakdown.setHeaderLabels(("Question set", "Type", "Attempted / total", "Mastered / total", "Complete", "Mastered"))
        self.breakdown.setMaximumHeight(180)
        self.filter_combo = QComboBox()
        for filter in ReviewFilter:
            self.filter_combo.addItem(filter.value, filter)
        self.filter_combo.setCurrentIndex(self.filter_combo.findData(initial_filter))
        self.filter_combo.currentIndexChanged.connect(self._render_items)
        filters = QHBoxLayout()
        filters.addWidget(QLabel("Review:"))
        filters.addWidget(self.filter_combo)
        filters.addStretch()
        self.review_list = QTreeWidget()
        self.review_list.setRootIsDecorated(False)
        self.review_list.setHeaderLabels(("Module", "Question set", "Question", "Latest status", "Bookmark", "Latest attempt"))
        self.review_list.setAlternatingRowColors(True)
        self.review_list.currentItemChanged.connect(self._selection_changed)
        self.review_list.itemDoubleClicked.connect(lambda *_: self._open(self.open_tracker))
        self.detail_label = QLabel()
        self.detail_label.setWordWrap(True)
        self.tracker_button = QPushButton("Open in Tracker")
        self.marking_button = QPushButton("Open in Marking")
        self.tracker_button.clicked.connect(lambda: self._open(self.open_tracker))
        self.marking_button.clicked.connect(lambda: self._open(self.open_marking))
        actions = QHBoxLayout()
        actions.addWidget(self.tracker_button)
        actions.addWidget(self.marking_button)
        actions.addStretch()
        layout = QVBoxLayout(self)
        layout.addLayout(header)
        layout.addWidget(self.progress_label)
        layout.addWidget(self.breakdown)
        layout.addLayout(filters)
        layout.addWidget(self.review_list, 1)
        layout.addWidget(self.detail_label)
        layout.addLayout(actions)
        compact_layout(layout, margin=PAGE_MARGIN)
        for row in (header, filters, actions):
            compact_layout(row)
        self.refresh()

    @staticmethod
    def _module_name(module):
        return f"{module.year_name} / {module.module_name}" if module else "Unassigned"

    def refresh(self):
        selected = self.module_combo.currentData() if self.module_combo.currentIndex() >= 0 else self.initial_module
        self.summaries = self.service.snapshot()
        self.module_combo.blockSignals(True)
        self.module_combo.clear()
        for summary in self.summaries:
            self.module_combo.addItem(self._module_name(summary.module), summary.module)
        index = next((i for i, summary in enumerate(self.summaries) if summary.module == selected), 0)
        self.module_combo.setCurrentIndex(index if self.summaries else -1)
        self.module_combo.blockSignals(False)
        self._render_module()

    def _summary(self):
        index = self.module_combo.currentIndex()
        return self.summaries[index] if 0 <= index < len(self.summaries) else None

    def _render_module(self, *_):
        summary = self._summary()
        self.breakdown.clear()
        if summary is None:
            self.progress_label.setText("No question sets. Import or configure a question set from Home.")
        else:
            c = summary.counts
            self.progress_label.setText(
                f"Attempted: {c.attempted} / {c.total}    Mastered: {c.mastered} / {c.total}\n"
                f"Correct: {c.correct}    Partial: {c.partial}    Incorrect: {c.incorrect}    "
                f"No Start: {c.no_start}    Unattempted: {c.unattempted}    Bookmarks: {c.bookmarked}\n"
                f"Needs Setup: {summary.needs_setup} question sets (excluded from totals)"
            )
            for item in summary.question_sets:
                self.breakdown.addTopLevelItem(QTreeWidgetItem((
                    item.question_set.display_name, item.question_set.kind.value.replace("_", " ").title(),
                    f"{item.counts.attempted} / {item.counts.total}",
                    f"{item.counts.mastered} / {item.counts.total}",
                    "Yes" if item.progress.complete else "No", "Yes" if item.progress.mastered else "No",
                )))
        self._render_items()

    def _render_items(self, *_):
        self.review_list.clear()
        summary = self._summary()
        if summary is not None:
            for item in summary.review_items(self.filter_combo.currentData()):
                row = QTreeWidgetItem((
                    self._module_name(item.module), item.question_set.display_name, item.label,
                    item.latest.status.value.replace("_", " ").title() if item.latest else "Unattempted",
                    "Yes" if item.question.bookmarked else "",
                    item.latest.timestamp.astimezone().strftime("%Y-%m-%d %H:%M") if item.latest else "",
                ))
                row.setData(0, Qt.ItemDataRole.UserRole, item)
                self.review_list.addTopLevelItem(row)
        if self.review_list.topLevelItemCount():
            self.review_list.setCurrentItem(self.review_list.topLevelItem(0))
        self._selection_changed()

    def _selection_changed(self, *_):
        row = self.review_list.currentItem()
        self.tracker_button.setEnabled(row is not None)
        self.marking_button.setEnabled(row is not None)
        item = row.data(0, Qt.ItemDataRole.UserRole) if row else None
        self.detail_label.setText(
            f"Question note: {item.question.note or '—'}\n"
            f"Latest attempt note: {(item.latest.note if item.latest else '') or '—'}"
            if item else "No questions match this filter."
        )

    def _open(self, callback):
        row = self.review_list.currentItem()
        if row is not None:
            callback(row.data(0, Qt.ItemDataRole.UserRole), self.filter_combo.currentData())
