from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog, QFormLayout, QLabel, QMessageBox, QPushButton, QSpinBox,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from question_tracker.application.tracking_service import TrackingError, TrackingService


class QuickBuilderDialog(QDialog):
    """Numeric input for appending ordinary three-level question hierarchies."""

    def __init__(
        self, service: TrackingService, question_set_id: str, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.service = service
        self.question_set_id = question_set_id
        self.setWindowTitle("Quick question builder")
        self.resize(460, 500)
        self.main_count = QSpinBox()
        self.main_count.setRange(1, 100)
        self.main_count.setObjectName("mainQuestionCount")
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Question", "Child count"])
        self.child_counts: list[QSpinBox] = []
        self.grandchild_counts: list[list[QSpinBox]] = []
        form = QFormLayout()
        form.addRow("Number of main questions", self.main_count)
        self.build_button = QPushButton("Add questions")
        self.build_button.clicked.connect(self._build)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "Adds new questions using unused root numbers. Existing questions stay intact."
        ))
        layout.addLayout(form)
        layout.addWidget(self.tree)
        layout.addWidget(self.build_button)
        layout.addWidget(close)
        self.main_count.valueChanged.connect(self._resize_roots)
        self.main_count.setValue(6)

    def _resize_roots(self, count: int) -> None:
        while len(self.child_counts) > count:
            self.tree.takeTopLevelItem(len(self.child_counts) - 1)
            self.child_counts.pop()
            self.grandchild_counts.pop()
        while len(self.child_counts) < count:
            index = len(self.child_counts)
            item = QTreeWidgetItem([f"New main question {index + 1}"])
            self.tree.addTopLevelItem(item)
            spin = QSpinBox()
            spin.setRange(0, 26)
            self.tree.setItemWidget(item, 1, spin)
            self.child_counts.append(spin)
            self.grandchild_counts.append([])
            spin.valueChanged.connect(
                lambda value, root_index=index: self._resize_children(root_index, value)
            )

    def _resize_children(self, root_index: int, count: int) -> None:
        item = self.tree.topLevelItem(root_index)
        spins = self.grandchild_counts[root_index]
        while len(spins) > count:
            item.takeChild(len(spins) - 1)
            spins.pop()
        while len(spins) < count:
            child = QTreeWidgetItem([f"{chr(ord('a') + len(spins))} grandchildren"])
            item.addChild(child)
            spin = QSpinBox()
            spin.setRange(0, 20)
            spins.append(spin)
            self.tree.setItemWidget(child, 1, spin)
        item.setExpanded(True)

    def _build(self) -> None:
        counts = tuple(tuple(spin.value() for spin in row) for row in self.grandchild_counts)
        try:
            self.service.append_question_structure(self.question_set_id, counts)
        except TrackingError as error:
            QMessageBox.warning(self, "Cannot build questions", str(error))
            return
        self.accept()
