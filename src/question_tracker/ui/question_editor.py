from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QColorDialog,
    QDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QStyle,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from question_tracker.application.tracking_service import (
    TrackingError,
    TrackingService,
    ValidationError,
)
from question_tracker.domain.questions import (
    Question,
    QuestionStructureError,
    visible_question_label,
)
from question_tracker.domain.tracking import NoteConflictPolicy, SetupState, Topic
from question_tracker.ui.colours import contrasting_text_colour
from question_tracker.ui.layout_spacing import (
    PAGE_MARGIN,
    SECTION_SPACING,
    compact_layout,
)
from question_tracker.ui.quick_builder import QuickBuilderDialog


class QuestionEditorDialog(QDialog):
    """Service-backed editor for question-set setup and structural changes."""

    def __init__(
        self,
        service: TrackingService,
        question_set_id: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.service = service
        self.question_set_id = question_set_id
        self._questions: tuple[Question, ...] = ()
        self._topics: tuple[Topic, ...] = ()
        self._question_items: dict[str, QTreeWidgetItem] = {}
        self._updating_assignments = False
        self._loading_question_details = False

        question_set = service.get_question_set(question_set_id)
        self.setWindowTitle(f"Question setup — {question_set.display_name}")
        self.resize(1000, 700)

        self.display_name_edit = QLineEdit(question_set.display_name)
        self.display_name_edit.setObjectName("displayNameEdit")
        self.display_name_edit.editingFinished.connect(self._save_display_name)
        self.general_note_edit = QPlainTextEdit(question_set.general_note)
        self.general_note_edit.setObjectName("generalNoteEdit")
        self.general_note_edit.setMaximumHeight(64)
        self.general_note_edit.textChanged.connect(self._save_general_note)
        metadata = QFormLayout()
        metadata.addRow("Display name", self.display_name_edit)
        metadata.addRow("General note", self.general_note_edit)

        self.question_tree = QTreeWidget()
        self.question_tree.setObjectName("questionTree")
        self.question_tree.setColumnCount(2)
        self.question_tree.setHeaderLabels(("Question hierarchy", "Label"))
        self.question_tree.setAlternatingRowColors(True)
        self.question_tree.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.question_tree.itemSelectionChanged.connect(
            self._question_selection_changed
        )

        question_buttons = QGridLayout()
        self.add_root_button = self._button("Add root", "addRootButton", self._add_root)
        self.add_sibling_button = self._button(
            "Add sibling", "addSiblingButton", self._add_sibling
        )
        self.add_child_button = self._button(
            "Add child", "addChildButton", self._add_child
        )
        self.rename_button = self._button("Rename", "renameQuestionButton", self._rename)
        self.move_up_button = self._button("Move up", "moveQuestionUpButton", self._move_up)
        self.move_down_button = self._button(
            "Move down", "moveQuestionDownButton", self._move_down
        )
        self.reparent_button = self._button(
            "Change parent", "reparentQuestionButton", self._reparent
        )
        self.delete_button = self._button("Delete", "deleteQuestionButton", self._delete)
        for index, button in enumerate(
            (
                self.add_root_button,
                self.add_sibling_button,
                self.add_child_button,
                self.rename_button,
                self.move_up_button,
                self.move_down_button,
                self.reparent_button,
                self.delete_button,
            )
        ):
            question_buttons.addWidget(button, index // 4, index % 4)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(self.question_tree)
        left_layout.addLayout(question_buttons)
        self.quick_builder_button = self._button(
            "Quick builder…", "quickBuilderButton", self._quick_build
        )
        structure_tools = QHBoxLayout()
        structure_tools.addWidget(self.quick_builder_button)
        structure_tools.addStretch()
        left_layout.addLayout(structure_tools)
        self.reset_button = QPushButton("Reset")
        self.reset_button.setObjectName("resetQuestionSetButton")
        reset_menu = QMenu(self.reset_button)
        self.reset_attempts_action = QAction("Reset attempt history", reset_menu)
        self.reset_attempts_action.setObjectName("resetAttemptHistoryAction")
        self.reset_attempts_action.triggered.connect(self._reset_attempt_history)
        self.reset_questions_action = QAction("Reset questions", reset_menu)
        self.reset_questions_action.setObjectName("resetQuestionsAction")
        self.reset_questions_action.triggered.connect(self._reset_questions)
        reset_menu.addAction(self.reset_attempts_action)
        reset_menu.addAction(self.reset_questions_action)
        self.reset_button.setMenu(reset_menu)
        self.reset_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxWarning)
        )
        self.reset_button.setToolTip(
            "Reset tracking history or questions (confirmation required)"
        )

        self.question_note_edit = QPlainTextEdit()
        self.question_note_edit.setObjectName("questionNoteEdit")
        self.question_note_edit.setMaximumHeight(64)
        self.question_note_edit.textChanged.connect(self._save_question_note)
        self.bookmark_check = QCheckBox("Bookmarked")
        self.bookmark_check.setObjectName("bookmarkCheck")
        self.bookmark_check.toggled.connect(self._save_bookmark)
        details = QGroupBox("Selected question")
        details_layout = QVBoxLayout(details)
        details_layout.addWidget(QLabel("Question note"))
        details_layout.addWidget(self.question_note_edit)
        details_layout.addWidget(self.bookmark_check)

        self.topic_list = QListWidget()
        self.topic_list.setObjectName("topicList")
        self.topic_list.currentItemChanged.connect(self._topic_selection_changed)
        topic_buttons = QGridLayout()
        self.add_topic_button = self._button("Add", "addTopicButton", self._add_topic)
        self.rename_topic_button = self._button(
            "Rename", "renameTopicButton", self._rename_topic
        )
        self.delete_topic_button = self._button(
            "Delete", "deleteTopicButton", self._delete_topic
        )
        self.topic_up_button = self._button("Up", "moveTopicUpButton", self._topic_up)
        self.topic_down_button = self._button(
            "Down", "moveTopicDownButton", self._topic_down
        )
        self.topic_colour_button = self._button(
            "Colour", "topicColourButton", self._change_topic_colour
        )
        for index, button in enumerate(
            (
                self.add_topic_button,
                self.rename_topic_button,
                self.delete_topic_button,
                self.topic_up_button,
                self.topic_down_button,
                self.topic_colour_button,
            )
        ):
            topic_buttons.addWidget(button, index // 3, index % 3)

        self.assignment_list = QListWidget()
        self.assignment_list.setObjectName("topicAssignmentList")
        self.assignment_list.itemChanged.connect(self._assignment_changed)
        topics = QGroupBox("Labels")
        topics_layout = QVBoxLayout(topics)
        topics_layout.addWidget(self.topic_list)
        topics_layout.addLayout(topic_buttons)
        self.assignment_heading = QLabel("Label for selected leaves (a new choice replaces the previous label)")
        self.assignment_heading.setWordWrap(True)
        topics_layout.addWidget(self.assignment_heading)
        topics_layout.addWidget(self.assignment_list)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(details)
        right_layout.addWidget(topics)

        splitter = QSplitter()
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setSizes([550, 450])

        self.finish_setup_button = self._button(
            "Finish setup", "finishSetupButton", self._finish_setup
        )
        self.finish_setup_button.setVisible(
            question_set.setup_state is SetupState.NEEDS_SETUP
        )
        self.close_button = self._button("Close", "closeEditorButton", self.accept)
        final_buttons = QHBoxLayout()
        final_buttons.addWidget(self.reset_button)
        final_buttons.addStretch()
        final_buttons.addWidget(self.close_button)
        final_buttons.addWidget(self.finish_setup_button)

        layout = QVBoxLayout(self)
        layout.addLayout(metadata)
        layout.addWidget(splitter, 1)
        layout.addLayout(final_buttons)

        for group in (details, topics):
            group.setFlat(True)
            compact_layout(group.layout(), margin=PAGE_MARGIN)
        for inner in (
            metadata, question_buttons, left_layout, structure_tools,
            topic_buttons, right_layout, final_buttons,
        ):
            compact_layout(inner)
        compact_layout(layout, margin=PAGE_MARGIN)
        layout.setSpacing(SECTION_SPACING)

        self._refresh_topics()
        self._refresh_questions()

    def _button(
        self, text: str, object_name: str, callback: Callable[[], None]
    ) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName(object_name)
        button.clicked.connect(callback)
        return button

    def _refresh_questions(self, preferred_id: str | None = None) -> None:
        if preferred_id is None:
            preferred_id = self._selected_question_id()
        self._questions = self.service.list_questions(self.question_set_id)
        children: dict[str | None, list[Question]] = defaultdict(list)
        for question in self._questions:
            children[question.parent_id].append(question)
        for siblings in children.values():
            siblings.sort(key=lambda item: item.sort_order)

        self.question_tree.clear()
        self._question_items = {}
        selected_item: QTreeWidgetItem | None = None

        def add_items(parent_item: QTreeWidgetItem | None, parent_id: str | None) -> None:
            nonlocal selected_item
            for question in children.get(parent_id, []):
                item = QTreeWidgetItem(
                    [visible_question_label(question.id, self._questions)]
                )
                item.setData(0, Qt.ItemDataRole.UserRole, question.id)
                self._question_items[question.id] = item
                if parent_item is None:
                    self.question_tree.addTopLevelItem(item)
                else:
                    parent_item.addChild(item)
                if question.id == preferred_id:
                    selected_item = item
                add_items(item, question.id)

        add_items(None, None)
        self._refresh_question_topic_badges()
        self.question_tree.expandAll()
        if selected_item is not None:
            self.question_tree.setCurrentItem(selected_item)
        elif self.question_tree.topLevelItemCount():
            self.question_tree.setCurrentItem(self.question_tree.topLevelItem(0))
        else:
            self._question_selected(None, None)

    def _refresh_topics(self, preferred_id: str | None = None) -> None:
        if preferred_id is None:
            preferred_id = self._selected_topic_id()
        self._topics = self.service.list_topics(self.question_set_id)
        self.topic_list.clear()
        selected_item: QListWidgetItem | None = None
        for topic in self._topics:
            item = QListWidgetItem(topic.title)
            item.setData(Qt.ItemDataRole.UserRole, topic.id)
            self._style_topic_item(item, topic)
            self.topic_list.addItem(item)
            if topic.id == preferred_id:
                selected_item = item
        if selected_item is not None:
            self.topic_list.setCurrentItem(selected_item)
        elif self.topic_list.count():
            self.topic_list.setCurrentRow(0)
        self._refresh_assignments()
        self._refresh_question_topic_badges()

    def _question_selected(
        self, current: QTreeWidgetItem | None, previous: QTreeWidgetItem | None
    ) -> None:
        del previous
        selected_questions = self._selected_questions()
        question = (
            selected_questions[0] if len(selected_questions) == 1 else None
        )
        enabled = question is not None
        for button in (
            self.add_sibling_button,
            self.add_child_button,
            self.rename_button,
            self.move_up_button,
            self.move_down_button,
            self.reparent_button,
            self.delete_button,
        ):
            button.setEnabled(enabled)
        self.question_note_edit.setEnabled(enabled)
        self.bookmark_check.setEnabled(enabled)
        self._loading_question_details = True
        try:
            if question is None:
                self.question_note_edit.clear()
                self.bookmark_check.setChecked(False)
            else:
                self.question_note_edit.setPlainText(question.note)
                types = self.service.list_bookmark_types()
                self.bookmark_check.setText(types[0].name if types else "No bookmark types")
                self.bookmark_check.setEnabled(bool(types))
                self.bookmark_check.setChecked(self.service.has_default_bookmark(question.id))
        finally:
            self._loading_question_details = False
        self._refresh_assignments()

    def _question_selection_changed(self) -> None:
        self._question_selected(self.question_tree.currentItem(), None)

    def _topic_selection_changed(
        self, current: QListWidgetItem | None, previous: QListWidgetItem | None
    ) -> None:
        del previous
        enabled = self._selected_topic_id() is not None
        self.rename_topic_button.setEnabled(enabled)
        self.delete_topic_button.setEnabled(enabled)
        self.topic_up_button.setEnabled(enabled)
        self.topic_down_button.setEnabled(enabled)
        self.topic_colour_button.setEnabled(enabled)
        if current is None:
            self.topic_colour_button.setStyleSheet("")
            return
        topic = self.service.get_topic(
            str(current.data(Qt.ItemDataRole.UserRole))
        )
        self.topic_colour_button.setStyleSheet(
            f"background: {topic.colour}; "
            f"color: {contrasting_text_colour(topic.colour)};"
        )

    def _refresh_assignments(self) -> None:
        selected_ids = self._selected_question_ids()
        selected_leaf_ids = self.service.effective_leaf_question_ids(
            self.question_set_id, selected_ids
        )
        assignable = bool(selected_leaf_ids)
        assignments = (
            {
                question_id: set(self.service.topic_ids_for_question(question_id))
                for question_id in selected_leaf_ids
            }
            if assignable
            else {}
        )
        self._updating_assignments = True
        try:
            self.assignment_list.clear()
            for topic in self._topics:
                item = QListWidgetItem(topic.title)
                item.setData(Qt.ItemDataRole.UserRole, topic.id)
                self._style_topic_item(item, topic)
                item.setFlags(
                    item.flags()
                    | Qt.ItemFlag.ItemIsUserCheckable
                )
                assigned_count = sum(
                    topic.id in assigned for assigned in assignments.values()
                )
                if assigned_count == len(selected_leaf_ids) and selected_leaf_ids:
                    state = Qt.CheckState.Checked
                elif assigned_count:
                    state = Qt.CheckState.PartiallyChecked
                else:
                    state = Qt.CheckState.Unchecked
                item.setCheckState(state)
                self.assignment_list.addItem(item)
            self.assignment_list.setEnabled(assignable)
            self.assignment_heading.setText(
                f"One label per leaf — {len(selected_leaf_ids)} selected. "
                "Checking a label replaces the previous choice."
            )
        finally:
            self._updating_assignments = False

    def _add_root(self) -> None:
        label = self._ask_text("Add root question", "Local label")
        if label is None:
            return
        self._run_question_action(
            lambda: self.service.create_question(self.question_set_id, label)
        )

    def _quick_build(self) -> None:
        dialog = QuickBuilderDialog(self.service, self.question_set_id, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._refresh_questions()

    def _add_sibling(self) -> None:
        question_id = self._selected_question_id()
        if question_id is None:
            return
        label = self._ask_text("Add sibling question", "Local label")
        if label is None:
            return
        self._run_question_action(lambda: self.service.add_sibling(question_id, label))

    def _add_child(self) -> None:
        question_id = self._selected_question_id()
        if question_id is None:
            return
        label = self._ask_text("Add child question", "Local label")
        if label is None:
            return
        self._run_question_action(lambda: self.service.add_child(question_id, label))

    def _rename(self) -> None:
        question_id = self._selected_question_id()
        if question_id is None:
            return
        question = self.service.get_question(question_id)
        label = self._ask_text(
            "Rename question", "Local label", question.local_label
        )
        if label is None:
            return
        self._run_question_action(
            lambda: self.service.rename_question(question_id, label)
        )

    def _move_up(self) -> None:
        self._move_selected_question(-1)

    def _move_down(self) -> None:
        self._move_selected_question(1)

    def _move_selected_question(self, offset: int) -> None:
        question_id = self._selected_question_id()
        if question_id is None:
            return
        question = self.service.get_question(question_id)
        siblings = sorted(
            (
                item
                for item in self._questions
                if item.parent_id == question.parent_id
            ),
            key=lambda item: item.sort_order,
        )
        target_index = siblings.index(question) + offset
        if target_index < 0 or target_index >= len(siblings):
            return
        self._run_question_action(
            lambda: self.service.reorder_question(question_id, target_index)
        )

    def _reparent(self) -> None:
        question_id = self._selected_question_id()
        if question_id is None:
            return
        question = self.service.get_question(question_id)
        options: list[tuple[str, str | None]] = [("Top level", None)]
        options.extend(
            (
                f"{visible_question_label(item.id, self._questions)} [{item.id[:8]}]",
                item.id,
            )
            for item in self._questions
            if item.id != question_id
        )
        chosen, accepted = QInputDialog.getItem(
            self,
            "Change question parent",
            "New parent",
            [label for label, _ in options],
            editable=False,
        )
        if not accepted:
            return
        new_parent_id = dict(options)[chosen]
        if new_parent_id == question.parent_id:
            return
        self._run_with_note_conflict(
            lambda policy: self.service.reparent_question(
                question_id, new_parent_id, note_conflict_policy=policy
            ),
            preferred_id=question_id,
        )

    def _delete(self) -> None:
        question_id = self._selected_question_id()
        if question_id is None:
            return
        if not self.service.question_subtree_has_tracking_data(question_id):
            response = QMessageBox.question(
                self,
                "Delete question",
                "Delete this question and all of its children?",
            )
            if response == QMessageBox.StandardButton.Yes:
                self._run_question_action(
                    lambda: self.service.delete_question_permanently(question_id),
                    preferred_id=None,
                )
            return

        prompt = QMessageBox(self)
        prompt.setIcon(QMessageBox.Icon.Warning)
        prompt.setWindowTitle("Delete question with history")
        prompt.setText(
            "This question or one of its children has notes, bookmarks, topics, "
            "or attempts. Choose what to do with that history."
        )
        permanent = prompt.addButton(
            "Delete permanently", QMessageBox.ButtonRole.DestructiveRole
        )
        move = prompt.addButton("Move history", QMessageBox.ButtonRole.ActionRole)
        prompt.addButton(QMessageBox.StandardButton.Cancel)
        prompt.exec()
        if prompt.clickedButton() is permanent:
            self._run_question_action(
                lambda: self.service.delete_question_permanently(question_id),
                preferred_id=None,
            )
        elif prompt.clickedButton() is move:
            self._move_history_and_delete(question_id)

    def _move_history_and_delete(self, question_id: str) -> None:
        targets = self.service.compatible_history_targets(question_id)
        if not targets:
            QMessageBox.warning(
                self,
                "Cannot move history",
                "There is no compatible leaf question outside this question subtree.",
            )
            return
        options = {
            f"{visible_question_label(target.id, self._questions)} [{target.id[:8]}]": target.id
            for target in targets
        }
        chosen, accepted = QInputDialog.getItem(
            self,
            "Move question history",
            "Destination leaf",
            list(options),
            editable=False,
        )
        if not accepted:
            return
        target_id = options[chosen]
        self._run_with_note_conflict(
            lambda policy: self.service.delete_question_and_move_history(
                question_id, target_id, note_conflict_policy=policy
            ),
            preferred_id=target_id,
        )

    def _save_display_name(self) -> None:
        current = self.service.get_question_set(self.question_set_id)
        if self.display_name_edit.text().strip() == current.display_name:
            return
        saved = self._run(
            lambda: self.service.rename_question_set(
                self.question_set_id, self.display_name_edit.text()
            )
        )
        if saved is None:
            self.display_name_edit.setText(current.display_name)
            return
        self.display_name_edit.setText(saved.display_name)
        self.setWindowTitle(f"Question setup — {saved.display_name}")

    def _save_general_note(self) -> None:
        self._run(
            lambda: self.service.set_question_set_note(
                self.question_set_id, self.general_note_edit.toPlainText()
            )
        )

    def _save_question_note(self) -> None:
        if self._loading_question_details:
            return
        question_id = self._selected_question_id()
        if question_id is None:
            return
        saved = self._run(
            lambda: self.service.set_question_note(
                question_id, self.question_note_edit.toPlainText()
            )
        )
        if isinstance(saved, Question):
            self._replace_cached_question(saved)

    def _save_bookmark(self, checked: bool) -> None:
        if self._loading_question_details:
            return
        question_id = self._selected_question_id()
        if question_id is None:
            return
        current = next(
            question for question in self._questions if question.id == question_id
        )
        if self.service.has_default_bookmark(question_id) == checked:
            return
        saved = self._run(lambda: self.service.set_question_bookmark(question_id, checked))
        if isinstance(saved, Question):
            self._replace_cached_question(saved)
            return
        self._loading_question_details = True
        try:
            self.bookmark_check.setChecked(self.service.has_default_bookmark(current.id))
        finally:
            self._loading_question_details = False

    def _add_topic(self) -> None:
        title = self._ask_text("Add label", "Title")
        if title is None:
            return
        topic = self._run(lambda: self.service.create_topic(self.question_set_id, title))
        if topic is not None:
            self._refresh_topics(topic.id)

    def _rename_topic(self) -> None:
        topic_id = self._selected_topic_id()
        if topic_id is None:
            return
        topic = self.service.get_topic(topic_id)
        title = self._ask_text("Rename label", "Title", topic.title)
        if title is None:
            return
        if self._run(lambda: self.service.rename_topic(topic_id, title)) is not None:
            self._refresh_topics(topic_id)

    def _delete_topic(self) -> None:
        topic_id = self._selected_topic_id()
        if topic_id is None:
            return
        if (
            QMessageBox.question(
                self,
                "Delete label",
                "Delete this label and remove all of its assignments?",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        if self._run(lambda: self.service.delete_topic(topic_id), none_is_success=True):
            self._refresh_topics()

    def _topic_up(self) -> None:
        self._move_topic(-1)

    def _topic_down(self) -> None:
        self._move_topic(1)

    def _move_topic(self, offset: int) -> None:
        topic_id = self._selected_topic_id()
        if topic_id is None:
            return
        topic = self.service.get_topic(topic_id)
        target_index = topic.sort_order + offset
        if target_index < 0 or target_index >= len(self._topics):
            return
        if self._run(lambda: self.service.reorder_topic(topic_id, target_index)) is not None:
            self._refresh_topics(topic_id)

    def _change_topic_colour(self) -> None:
        topic_id = self._selected_topic_id()
        if topic_id is None:
            return
        topic = self.service.get_topic(topic_id)
        chosen = QColorDialog.getColor(
            QColor(topic.colour), self, "Choose topic colour"
        )
        if not chosen.isValid():
            return
        colour = chosen.name(QColor.NameFormat.HexRgb).upper()
        if colour == topic.colour:
            return
        if self._run(
            lambda: self.service.set_topic_colour(topic_id, colour)
        ) is not None:
            self._refresh_topics(topic_id)

    def _assignment_changed(self, item: QListWidgetItem) -> None:
        if self._updating_assignments:
            return
        question_ids = self.service.effective_leaf_question_ids(
            self.question_set_id, self._selected_question_ids()
        )
        if not question_ids:
            return
        topic_id = str(item.data(Qt.ItemDataRole.UserRole))
        assigned = item.checkState() == Qt.CheckState.Checked
        if not assigned:
            question_ids = tuple(qid for qid in question_ids if topic_id in self.service.topic_ids_for_question(qid))
        operation = lambda: self.service.set_question_labels(question_ids, topic_id if assigned else None)
        if self._run(operation, none_is_success=True) is False:
            self._refresh_assignments()
            return
        self._refresh_assignments()
        self._refresh_question_topic_badges()

    def _reset_attempt_history(self, checked: bool = False) -> None:
        del checked
        if (
            QMessageBox.question(
                self,
                "Reset attempt history",
                "Permanently delete every attempt and attempt note for this "
                "question set? Questions, bookmarks, question notes, topics, "
                "and files will be preserved.",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        self._run(
            lambda: self.service.reset_attempt_history(self.question_set_id),
            none_is_success=True,
        )

    def _reset_questions(self, checked: bool = False) -> None:
        del checked
        response = QMessageBox.warning(
            self,
            "Reset questions",
            "Permanently delete the complete question hierarchy, all attempt "
            "history, question notes, bookmarks, and topic assignments? The "
            "question set, its files, metadata, and topic definitions will be "
            "preserved. This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if response != QMessageBox.StandardButton.Yes:
            return
        reset = self._run(
            lambda: self.service.reset_questions(self.question_set_id)
        )
        if reset is None:
            return
        self.finish_setup_button.setVisible(True)
        self._refresh_questions()
        self._refresh_topics()

    def _finish_setup(self) -> None:
        saved = self._run(
            lambda: self.service.finish_question_set_setup(self.question_set_id)
        )
        if saved is not None:
            self.accept()

    def _replace_cached_question(self, updated: Question) -> None:
        self._questions = tuple(
            updated if question.id == updated.id else question
            for question in self._questions
        )

    def _refresh_question_topic_badges(self) -> None:
        if not self._question_items:
            return
        leaf_ids = {
            question.id for question in self.service.leaf_questions(self.question_set_id)
        }
        topics_by_id = {topic.id: topic for topic in self._topics}
        for question_id, item in self._question_items.items():
            existing = self.question_tree.itemWidget(item, 1)
            if existing is not None:
                self.question_tree.removeItemWidget(item, 1)
                existing.setParent(None)
                existing.deleteLater()
            if question_id not in leaf_ids:
                continue
            topics = tuple(
                topics_by_id[topic_id]
                for topic_id in self.service.topic_ids_for_question(question_id)
                if topic_id in topics_by_id
            )
            if not topics:
                continue
            container = QWidget()
            badges = QHBoxLayout(container)
            badges.setContentsMargins(2, 0, 2, 0)
            badges.setSpacing(3)
            for topic in sorted(topics, key=lambda value: value.sort_order):
                badge = QLabel(topic.title)
                badge.setObjectName(f"topicBadge-{question_id}-{topic.id}")
                badge.setStyleSheet(
                    f"background: {topic.colour}; "
                    f"color: {contrasting_text_colour(topic.colour)}; "
                    "border-radius: 5px; padding: 1px 4px;"
                )
                badge.setToolTip(topic.title)
                badges.addWidget(badge)
            badges.addStretch()
            self.question_tree.setItemWidget(item, 1, container)

    @staticmethod
    def _style_topic_item(item: QListWidgetItem, topic: Topic) -> None:
        item.setBackground(QBrush(QColor(topic.colour)))
        item.setForeground(QBrush(QColor(contrasting_text_colour(topic.colour))))

    def _run_question_action(
        self,
        operation: Callable[[], object],
        preferred_id: str | None = None,
    ) -> None:
        result = self._run(operation, none_is_success=True)
        if result is False:
            return
        result_id = getattr(result, "id", preferred_id)
        self._refresh_topics()
        self._refresh_questions(result_id)

    def _run_with_note_conflict(
        self,
        operation: Callable[[NoteConflictPolicy | None], object],
        *,
        preferred_id: str | None,
    ) -> None:
        try:
            result = operation(None)
        except ValidationError as error:
            if "note conflict policy is required" not in str(error):
                self._show_error(error)
                return
            policy = self._ask_note_conflict_policy()
            if policy is None:
                return
            result = self._run(lambda: operation(policy), none_is_success=True)
            if result is False:
                return
        except (TrackingError, QuestionStructureError) as error:
            self._show_error(error)
            return
        result_id = getattr(result, "id", preferred_id)
        self._refresh_topics()
        self._refresh_questions(result_id)

    def _ask_note_conflict_policy(self) -> NoteConflictPolicy | None:
        labels = {
            "Keep destination note": NoteConflictPolicy.KEEP_TARGET,
            "Use source note": NoteConflictPolicy.USE_SOURCE,
            "Combine both notes": NoteConflictPolicy.COMBINE,
        }
        chosen, accepted = QInputDialog.getItem(
            self,
            "Resolve note conflict",
            "Both questions have notes",
            list(labels),
            editable=False,
        )
        return labels[chosen] if accepted else None

    def _run(
        self, operation: Callable[[], object], *, none_is_success: bool = False
    ) -> object | bool | None:
        try:
            result = operation()
            return True if none_is_success and result is None else result
        except (TrackingError, QuestionStructureError) as error:
            self._show_error(error)
            return False if none_is_success else None

    def _show_error(self, error: Exception) -> None:
        QMessageBox.warning(self, "Question Tracker", str(error))

    def _ask_text(self, title: str, label: str, value: str = "") -> str | None:
        text, accepted = QInputDialog.getText(self, title, label, text=value)
        return text if accepted else None

    def _selected_question_id(self) -> str | None:
        selected_ids = self._selected_question_ids()
        return selected_ids[0] if len(selected_ids) == 1 else None

    def _selected_question_ids(self) -> tuple[str, ...]:
        return tuple(
            str(item.data(0, Qt.ItemDataRole.UserRole))
            for item in self.question_tree.selectedItems()
        )

    def _selected_questions(self) -> tuple[Question, ...]:
        selected_ids = set(self._selected_question_ids())
        return tuple(
            question for question in self._questions if question.id in selected_ids
        )

    def _selected_topic_id(self) -> str | None:
        item = self.topic_list.currentItem()
        if item is None:
            return None
        return str(item.data(Qt.ItemDataRole.UserRole))
