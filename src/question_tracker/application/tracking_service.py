from __future__ import annotations

import re
import sqlite3
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from question_tracker.domain.questions import (
    MAX_QUESTION_DEPTH,
    Question,
    QuestionStructureError,
    is_leaf,
    leaf_questions_in_display_order,
    leftmost_leaf,
    question_depth,
    question_subtree_in_display_order,
    validate_question_structure,
)
from question_tracker.domain.tracking import (
    Attempt,
    AttemptStatus,
    DEFAULT_TOPIC_COLOUR,
    NoteConflictPolicy,
    QuestionSet,
    QuestionSetKind,
    QuestionSetProgress,
    SetupState,
    Topic,
    calculate_progress,
    latest_attempt,
)
from question_tracker.persistence.database import Database
from question_tracker.persistence.tracking_repository import TrackingRepository


class TrackingError(RuntimeError):
    """Base error for invalid tracking operations."""


class NotFoundError(TrackingError):
    """Raised when an operation references an entity that does not exist."""


class ValidationError(TrackingError):
    """Raised when an operation would violate a tracking rule."""


class TrackingService:
    """Non-Qt application API for question tracking operations."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def create_question_set(
        self,
        kind: QuestionSetKind | str,
        display_name: str,
        *,
        setup_state: SetupState | str = SetupState.NEEDS_SETUP,
        general_note: str = "",
    ) -> QuestionSet:
        name = _required_text(display_name, "Display name")
        entity = QuestionSet(
            id=str(uuid4()),
            kind=_enum_value(QuestionSetKind, kind, "question-set kind"),
            display_name=name,
            setup_state=_enum_value(SetupState, setup_state, "setup state"),
            general_note=general_note,
        )
        now = _time_text(_utc_now())
        with self.database.transaction() as connection:
            TrackingRepository(connection).insert_question_set(entity, now)
        return entity

    def get_question_set(self, question_set_id: str) -> QuestionSet:
        with self.database.read() as connection:
            return _require_question_set(TrackingRepository(connection), question_set_id)

    def list_question_sets(
        self, setup_state: SetupState | str | None = None
    ) -> tuple[QuestionSet, ...]:
        state = (
            None
            if setup_state is None
            else _enum_value(SetupState, setup_state, "setup state")
        )
        with self.database.read() as connection:
            return TrackingRepository(connection).list_question_sets(state)

    def finish_question_set_setup(self, question_set_id: str) -> QuestionSet:
        with self.database.transaction() as connection:
            repository = TrackingRepository(connection)
            _require_question_set(repository, question_set_id)
            questions = repository.list_questions(question_set_id)
            validate_question_structure(questions)
            if not leaf_questions_in_display_order(questions, question_set_id):
                raise ValidationError(
                    "Setup requires at least one markable leaf question"
                )
            now = _time_text(_utc_now())
            repository.update_question_set_state(
                question_set_id, SetupState.READY, now
            )
            return _require_question_set(repository, question_set_id)

    def rename_question_set(self, question_set_id: str, display_name: str) -> QuestionSet:
        name = _required_text(display_name, "Display name")
        with self.database.transaction() as connection:
            repository = TrackingRepository(connection)
            _require_question_set(repository, question_set_id)
            repository.update_question_set_name(
                question_set_id, name, _time_text(_utc_now())
            )
            return _require_question_set(repository, question_set_id)

    def set_question_set_setup_state(
        self, question_set_id: str, setup_state: SetupState | str
    ) -> QuestionSet:
        state = _enum_value(SetupState, setup_state, "setup state")
        with self.database.transaction() as connection:
            repository = TrackingRepository(connection)
            _require_question_set(repository, question_set_id)
            repository.update_question_set_state(
                question_set_id, state, _time_text(_utc_now())
            )
            return _require_question_set(repository, question_set_id)

    def set_question_set_note(self, question_set_id: str, note: str) -> QuestionSet:
        with self.database.transaction() as connection:
            repository = TrackingRepository(connection)
            _require_question_set(repository, question_set_id)
            repository.update_question_set_note(
                question_set_id, note, _time_text(_utc_now())
            )
            return _require_question_set(repository, question_set_id)

    def create_question(
        self,
        question_set_id: str,
        local_label: str,
        *,
        parent_id: str | None = None,
    ) -> Question:
        label = _required_text(local_label, "Question label")
        with self.database.transaction() as connection:
            repository = TrackingRepository(connection)
            _require_question_set(repository, question_set_id)
            parent = None
            if parent_id is not None:
                parent = _require_question(repository, parent_id)
                if parent.question_set_id != question_set_id:
                    raise ValidationError(
                        "Parent and child must belong to the same question set"
                    )
            return self._create_question(repository, question_set_id, label, parent)

    def add_sibling(self, question_id: str, local_label: str) -> Question:
        label = _required_text(local_label, "Question label")
        with self.database.transaction() as connection:
            repository = TrackingRepository(connection)
            sibling = _require_question(repository, question_id)
            questions = repository.list_questions(sibling.question_set_id)
            siblings = _siblings(questions, sibling.parent_id)
            insertion_index = siblings.index(sibling) + 1
            now = _time_text(_utc_now())
            new_question = Question(
                id=str(uuid4()),
                question_set_id=sibling.question_set_id,
                parent_id=sibling.parent_id,
                local_label=label,
                sort_order=_next_temporary_order(questions),
            )
            repository.insert_question(new_question, now)
            siblings.insert(insertion_index, new_question)
            _rewrite_question_order(
                repository,
                sibling.question_set_id,
                sibling.parent_id,
                siblings,
                now,
            )
            validate_question_structure(
                repository.list_questions(sibling.question_set_id)
            )
            return _require_question(repository, new_question.id)

    def add_child(self, question_id: str, local_label: str) -> Question:
        label = _required_text(local_label, "Question label")
        with self.database.transaction() as connection:
            repository = TrackingRepository(connection)
            parent = _require_question(repository, question_id)
            return self._create_question(
                repository, parent.question_set_id, label, parent
            )

    def rename_question(self, question_id: str, local_label: str) -> Question:
        label = _required_text(local_label, "Question label")
        with self.database.transaction() as connection:
            repository = TrackingRepository(connection)
            _require_question(repository, question_id)
            repository.update_question_label(
                question_id, label, _time_text(_utc_now())
            )
            return _require_question(repository, question_id)

    def reorder_question(self, question_id: str, new_index: int) -> Question:
        with self.database.transaction() as connection:
            repository = TrackingRepository(connection)
            question = _require_question(repository, question_id)
            questions = repository.list_questions(question.question_set_id)
            siblings = _siblings(questions, question.parent_id)
            if new_index < 0 or new_index >= len(siblings):
                raise ValidationError("Question position is out of range")
            siblings.remove(question)
            siblings.insert(new_index, question)
            _rewrite_question_order(
                repository,
                question.question_set_id,
                question.parent_id,
                siblings,
                _time_text(_utc_now()),
            )
            return _require_question(repository, question_id)

    def reparent_question(
        self,
        question_id: str,
        new_parent_id: str | None,
        *,
        new_index: int | None = None,
        note_conflict_policy: NoteConflictPolicy | str | None = None,
    ) -> Question:
        with self.database.transaction() as connection:
            repository = TrackingRepository(connection)
            question = _require_question(repository, question_id)
            questions = repository.list_questions(question.question_set_id)
            new_parent = None
            if new_parent_id is not None:
                new_parent = _require_question(repository, new_parent_id)
                if new_parent.question_set_id != question.question_set_id:
                    raise ValidationError(
                        "Parent and child must belong to the same question set"
                    )

            subtree = question_subtree_in_display_order(question_id, questions)
            subtree_ids = {item.id for item in subtree}
            if new_parent_id in subtree_ids:
                raise QuestionStructureError("Question hierarchy contains a cycle")

            old_depth = question_depth(question_id, questions)
            relative_depth = max(
                question_depth(item.id, questions) - old_depth for item in subtree
            )
            new_depth = 1 if new_parent is None else question_depth(new_parent.id, questions) + 1
            if new_depth + relative_depth > MAX_QUESTION_DEPTH:
                raise QuestionStructureError(
                    f"Question hierarchy cannot exceed {MAX_QUESTION_DEPTH} levels"
                )

            destination_parent_id = new_parent.id if new_parent is not None else None
            if destination_parent_id == question.parent_id:
                siblings = _siblings(questions, question.parent_id)
                destination = len(siblings) - 1 if new_index is None else new_index
                if destination < 0 or destination >= len(siblings):
                    raise ValidationError("Question position is out of range")
                siblings.remove(question)
                siblings.insert(destination, question)
                _rewrite_question_order(
                    repository,
                    question.question_set_id,
                    question.parent_id,
                    siblings,
                    _time_text(_utc_now()),
                )
                return _require_question(repository, question_id)

            destination_siblings = _siblings(questions, destination_parent_id)
            destination = len(destination_siblings) if new_index is None else new_index
            if destination < 0 or destination > len(destination_siblings):
                raise ValidationError("Question position is out of range")
            parent_was_leaf = new_parent is not None and is_leaf(new_parent.id, questions)

            now = _time_text(_utc_now())
            source_siblings = _siblings(questions, question.parent_id)
            source_siblings.remove(question)
            repository.update_question_position(
                question.id,
                question.parent_id,
                _next_temporary_order(questions),
                now,
            )
            _rewrite_question_order(
                repository,
                question.question_set_id,
                question.parent_id,
                source_siblings,
                now,
            )

            refreshed = repository.list_questions(question.question_set_id)
            repository.update_question_position(
                question.id,
                destination_parent_id,
                _next_temporary_order(refreshed),
                now,
            )
            destination_siblings.insert(destination, question)
            _rewrite_question_order(
                repository,
                question.question_set_id,
                destination_parent_id,
                destination_siblings,
                now,
            )

            updated_questions = repository.list_questions(question.question_set_id)
            validate_question_structure(updated_questions)
            if parent_was_leaf and new_parent is not None:
                target = leftmost_leaf(question.id, updated_questions)
                _transfer_history(
                    repository,
                    (new_parent.id,),
                    target.id,
                    now,
                    note_conflict_policy,
                )
            return _require_question(repository, question_id)

    def list_questions(self, question_set_id: str) -> tuple[Question, ...]:
        with self.database.read() as connection:
            repository = TrackingRepository(connection)
            _require_question_set(repository, question_set_id)
            return repository.list_questions(question_set_id)

    def get_question(self, question_id: str) -> Question:
        with self.database.read() as connection:
            return _require_question(TrackingRepository(connection), question_id)

    def leaf_questions(self, question_set_id: str) -> tuple[Question, ...]:
        questions = self.list_questions(question_set_id)
        return leaf_questions_in_display_order(questions, question_set_id)

    def set_question_note(self, question_id: str, note: str) -> Question:
        with self.database.transaction() as connection:
            repository = TrackingRepository(connection)
            _require_question(repository, question_id)
            repository.update_question_note(question_id, note, _time_text(_utc_now()))
            return _require_question(repository, question_id)

    def toggle_question_bookmark(self, question_id: str) -> Question:
        with self.database.transaction() as connection:
            repository = TrackingRepository(connection)
            question = _require_question(repository, question_id)
            repository.update_question_bookmark(
                question_id, not question.bookmarked, _time_text(_utc_now())
            )
            return _require_question(repository, question_id)

    def set_question_bookmark(self, question_id: str, bookmarked: bool) -> Question:
        if not isinstance(bookmarked, bool):
            raise ValidationError("Bookmark state must be true or false")
        with self.database.transaction() as connection:
            repository = TrackingRepository(connection)
            _require_question(repository, question_id)
            repository.update_question_bookmark(
                question_id, bookmarked, _time_text(_utc_now())
            )
            return _require_question(repository, question_id)

    def delete_question_permanently(self, question_id: str) -> None:
        with self.database.transaction() as connection:
            repository = TrackingRepository(connection)
            question = _require_question(repository, question_id)
            parent_id = question.parent_id
            repository.delete_question(question_id)
            remaining = repository.list_questions(question.question_set_id)
            _rewrite_question_order(
                repository,
                question.question_set_id,
                parent_id,
                _siblings(remaining, parent_id),
                _time_text(_utc_now()),
            )

    def delete_question_and_move_history(
        self,
        question_id: str,
        target_leaf_id: str,
        *,
        note_conflict_policy: NoteConflictPolicy | str | None = None,
    ) -> None:
        with self.database.transaction() as connection:
            repository = TrackingRepository(connection)
            source = _require_question(repository, question_id)
            target = _require_question(repository, target_leaf_id)
            if source.question_set_id != target.question_set_id:
                raise ValidationError("History can only move within a question set")
            questions = repository.list_questions(source.question_set_id)
            if not is_leaf(target.id, questions):
                raise ValidationError("History target must be a leaf question")
            subtree = question_subtree_in_display_order(source.id, questions)
            source_ids = tuple(question.id for question in subtree)
            if target.id in source_ids:
                raise ValidationError("History target cannot be inside the deleted subtree")

            now = _time_text(_utc_now())
            _transfer_history(
                repository,
                source_ids,
                target.id,
                now,
                note_conflict_policy,
            )
            repository.delete_question(source.id)
            remaining = repository.list_questions(source.question_set_id)
            _rewrite_question_order(
                repository,
                source.question_set_id,
                source.parent_id,
                _siblings(remaining, source.parent_id),
                now,
            )

    def question_subtree_has_tracking_data(self, question_id: str) -> bool:
        with self.database.read() as connection:
            repository = TrackingRepository(connection)
            question = _require_question(repository, question_id)
            questions = repository.list_questions(question.question_set_id)
            subtree = question_subtree_in_display_order(question.id, questions)
            return any(
                item.note
                or item.bookmarked
                or repository.topic_ids_for_question(item.id)
                or repository.list_attempts_for_question(item.id)
                for item in subtree
            )

    def compatible_history_targets(self, question_id: str) -> tuple[Question, ...]:
        with self.database.read() as connection:
            repository = TrackingRepository(connection)
            question = _require_question(repository, question_id)
            questions = repository.list_questions(question.question_set_id)
            subtree_ids = {
                item.id
                for item in question_subtree_in_display_order(question.id, questions)
            }
            return tuple(
                leaf
                for leaf in leaf_questions_in_display_order(
                    questions, question.question_set_id
                )
                if leaf.id not in subtree_ids
            )

    def create_topic(
        self,
        question_set_id: str,
        title: str,
        *,
        colour: str = DEFAULT_TOPIC_COLOUR,
    ) -> Topic:
        clean_title = _required_text(title, "Topic title")
        clean_colour = _topic_colour(colour)
        with self.database.transaction() as connection:
            repository = TrackingRepository(connection)
            _require_question_set(repository, question_set_id)
            topics = repository.list_topics(question_set_id)
            topic = Topic(
                id=str(uuid4()),
                question_set_id=question_set_id,
                title=clean_title,
                sort_order=len(topics),
                colour=clean_colour,
            )
            try:
                repository.insert_topic(topic)
            except sqlite3.IntegrityError as error:
                raise ValidationError(
                    "Topic titles must be unique within a question set"
                ) from error
            return topic

    def rename_topic(self, topic_id: str, title: str) -> Topic:
        clean_title = _required_text(title, "Topic title")
        with self.database.transaction() as connection:
            repository = TrackingRepository(connection)
            _require_topic(repository, topic_id)
            try:
                repository.update_topic_title(topic_id, clean_title)
            except sqlite3.IntegrityError as error:
                raise ValidationError(
                    "Topic titles must be unique within a question set"
                ) from error
            return _require_topic(repository, topic_id)

    def reorder_topic(self, topic_id: str, new_index: int) -> Topic:
        with self.database.transaction() as connection:
            repository = TrackingRepository(connection)
            topic = _require_topic(repository, topic_id)
            topics = list(repository.list_topics(topic.question_set_id))
            if new_index < 0 or new_index >= len(topics):
                raise ValidationError("Topic position is out of range")
            topics.remove(topic)
            topics.insert(new_index, topic)
            _rewrite_topic_order(repository, topics)
            return _require_topic(repository, topic_id)

    def set_topic_colour(self, topic_id: str, colour: str) -> Topic:
        clean_colour = _topic_colour(colour)
        with self.database.transaction() as connection:
            repository = TrackingRepository(connection)
            _require_topic(repository, topic_id)
            repository.update_topic_colour(topic_id, clean_colour)
            return _require_topic(repository, topic_id)

    def list_topics(self, question_set_id: str) -> tuple[Topic, ...]:
        with self.database.read() as connection:
            repository = TrackingRepository(connection)
            _require_question_set(repository, question_set_id)
            return repository.list_topics(question_set_id)

    def delete_topic(self, topic_id: str) -> None:
        with self.database.transaction() as connection:
            repository = TrackingRepository(connection)
            topic = _require_topic(repository, topic_id)
            repository.delete_topic(topic.id)
            remaining = list(repository.list_topics(topic.question_set_id))
            _rewrite_topic_order(repository, remaining)

    def get_topic(self, topic_id: str) -> Topic:
        with self.database.read() as connection:
            return _require_topic(TrackingRepository(connection), topic_id)

    def assign_topic(self, question_id: str, topic_id: str) -> None:
        with self.database.transaction() as connection:
            repository = TrackingRepository(connection)
            question = _require_question(repository, question_id)
            topic = _require_topic(repository, topic_id)
            if question.question_set_id != topic.question_set_id:
                raise ValidationError("Topic and question must belong to the same set")
            questions = repository.list_questions(question.question_set_id)
            if not is_leaf(question.id, questions):
                raise ValidationError("Topics can only be assigned to leaf questions")
            repository.assign_topic(question.id, topic.id, question.question_set_id)

    def remove_topic_assignment(self, question_id: str, topic_id: str) -> None:
        with self.database.transaction() as connection:
            repository = TrackingRepository(connection)
            _require_question(repository, question_id)
            _require_topic(repository, topic_id)
            repository.remove_topic_assignment(question_id, topic_id)

    def topic_ids_for_question(self, question_id: str) -> tuple[str, ...]:
        with self.database.read() as connection:
            repository = TrackingRepository(connection)
            _require_question(repository, question_id)
            return repository.topic_ids_for_question(question_id)

    def set_topic_assignments(
        self, question_ids: tuple[str, ...], topic_id: str, assigned: bool
    ) -> None:
        """Apply a multi-selection checkbox change atomically."""
        with self.database.transaction() as connection:
            repository = TrackingRepository(connection)
            topic = _require_topic(repository, topic_id)
            questions = repository.list_questions(topic.question_set_id)
            leaves = {q.id for q in leaf_questions_in_display_order(
                questions, topic.question_set_id
            )}
            if not set(question_ids) <= leaves:
                raise ValidationError("Topics require leaf questions in the same set")
            for question_id in question_ids:
                if assigned:
                    repository.assign_topic(question_id, topic.id, topic.question_set_id)
                else:
                    repository.remove_topic_assignment(question_id, topic.id)

    def effective_leaf_question_ids(
        self, question_set_id: str, selected_question_ids: tuple[str, ...]
    ) -> tuple[str, ...]:
        """Expand selected hierarchy nodes to distinct leaves in visible order."""
        if not selected_question_ids:
            return ()
        with self.database.read() as connection:
            repository = TrackingRepository(connection)
            _require_question_set(repository, question_set_id)
            questions = repository.list_questions(question_set_id)
            selected_leaf_ids: set[str] = set()
            leaves = leaf_questions_in_display_order(questions, question_set_id)
            leaf_ids = {leaf.id for leaf in leaves}
            for question_id in dict.fromkeys(selected_question_ids):
                question = _require_question(repository, question_id)
                if question.question_set_id != question_set_id:
                    raise ValidationError(
                        "Selected questions must belong to the same question set"
                    )
                selected_leaf_ids.update(
                    item.id
                    for item in question_subtree_in_display_order(
                        question.id, questions
                    )
                    if item.id in leaf_ids
                )
            return tuple(
                leaf.id for leaf in leaves if leaf.id in selected_leaf_ids
            )

    def append_question_structure(
        self, question_set_id: str, child_counts: tuple[tuple[int, ...], ...]
    ) -> tuple[Question, ...]:
        """Append roots; each inner tuple gives grandchild counts for its children."""
        if not child_counts or len(child_counts) > 100:
            raise ValidationError("Choose between 1 and 100 main questions")
        for counts in child_counts:
            if len(counts) > 26 or any(
                type(count) is not int or count < 0 or count > 20 for count in counts
            ):
                raise ValidationError("Use at most 26 children and 20 grandchildren")
        with self.database.transaction() as connection:
            repository = TrackingRepository(connection)
            _require_question_set(repository, question_set_id)
            existing = repository.list_questions(question_set_id)
            used_labels = {q.local_label for q in existing if q.parent_id is None}
            number = 1
            for counts in child_counts:
                while str(number) in used_labels:
                    number += 1
                root = self._create_question(repository, question_set_id, str(number), None)
                used_labels.add(str(number))
                for index, count in enumerate(counts):
                    child = self._create_question(
                        repository, question_set_id, chr(ord('a') + index), root
                    )
                    for grandchild_index in range(1, count + 1):
                        self._create_question(
                            repository, question_set_id, _roman_label(grandchild_index), child
                        )
            return repository.list_questions(question_set_id)

    def add_attempt(
        self,
        question_id: str,
        status: AttemptStatus | str,
        *,
        timestamp: datetime | None = None,
        note: str = "",
    ) -> Attempt:
        attempt_status = _enum_value(AttemptStatus, status, "attempt status")
        generated_timestamp = timestamp is None
        occurred_at = _aware_utc(timestamp or _utc_now())
        created_at = _utc_now()
        with self.database.transaction() as connection:
            repository = TrackingRepository(connection)
            question = _require_question(repository, question_id)
            questions = repository.list_questions(question.question_set_id)
            if not is_leaf(question.id, questions):
                raise ValidationError("Attempts can only be added to leaf questions")
            existing_attempts = repository.list_attempts_for_question(question.id)
            if existing_attempts:
                latest_timestamp = max(
                    attempt.timestamp for attempt in existing_attempts
                )
                latest_created_at = max(
                    attempt.created_at for attempt in existing_attempts
                )
                if generated_timestamp and occurred_at <= latest_timestamp:
                    occurred_at = latest_timestamp + timedelta(microseconds=1)
                if created_at <= latest_created_at:
                    created_at = latest_created_at + timedelta(microseconds=1)
            attempt = Attempt(
                id=str(uuid4()),
                question_id=question_id,
                status=attempt_status,
                timestamp=occurred_at,
                note=note,
                created_at=created_at,
            )
            repository.insert_attempt(attempt)
            return attempt

    def edit_attempt(
        self,
        attempt_id: str,
        *,
        status: AttemptStatus | str | None = None,
        timestamp: datetime | None = None,
        note: str | None = None,
    ) -> Attempt:
        with self.database.transaction() as connection:
            repository = TrackingRepository(connection)
            attempt = _require_attempt(repository, attempt_id)
            updated_status = (
                attempt.status
                if status is None
                else _enum_value(AttemptStatus, status, "attempt status")
            )
            updated_timestamp = (
                attempt.timestamp if timestamp is None else _aware_utc(timestamp)
            )
            updated_note = attempt.note if note is None else note
            repository.update_attempt(
                attempt.id,
                updated_status,
                updated_timestamp,
                updated_note,
                _utc_now(),
            )
            return _require_attempt(repository, attempt_id)

    def delete_attempt(self, attempt_id: str) -> None:
        with self.database.transaction() as connection:
            repository = TrackingRepository(connection)
            _require_attempt(repository, attempt_id)
            repository.delete_attempt(attempt_id)

    def restore_attempt(self, attempt: Attempt) -> Attempt:
        """Restore an exact attempt snapshot for session undo/redo."""
        with self.database.transaction() as connection:
            repository = TrackingRepository(connection)
            if repository.get_attempt(attempt.id) is not None:
                raise ValidationError(f"Attempt already exists: {attempt.id}")
            question = _require_question(repository, attempt.question_id)
            questions = repository.list_questions(question.question_set_id)
            if not is_leaf(question.id, questions):
                raise ValidationError("Attempts can only be restored to leaf questions")
            repository.insert_attempt(attempt)
            return _require_attempt(repository, attempt.id)

    def reset_attempt_history(self, question_set_id: str) -> None:
        """Permanently remove only attempts belonging to one question set."""
        with self.database.transaction() as connection:
            repository = TrackingRepository(connection)
            _require_question_set(repository, question_set_id)
            repository.delete_attempts_for_set(question_set_id)

    def reset_questions(self, question_set_id: str) -> QuestionSet:
        """Remove the hierarchy and history, preserving the set and its topics."""
        with self.database.transaction() as connection:
            repository = TrackingRepository(connection)
            _require_question_set(repository, question_set_id)
            repository.delete_questions_for_set(question_set_id)
            repository.update_question_set_state(
                question_set_id, SetupState.NEEDS_SETUP, _time_text(_utc_now())
            )
            return _require_question_set(repository, question_set_id)

    def attempts_for_question(self, question_id: str) -> tuple[Attempt, ...]:
        with self.database.read() as connection:
            repository = TrackingRepository(connection)
            _require_question(repository, question_id)
            return repository.list_attempts_for_question(question_id)

    def get_attempt(self, attempt_id: str) -> Attempt:
        with self.database.read() as connection:
            return _require_attempt(TrackingRepository(connection), attempt_id)

    def latest_attempt_for_question(self, question_id: str) -> Attempt | None:
        return latest_attempt(self.attempts_for_question(question_id))

    def question_set_progress(self, question_set_id: str) -> QuestionSetProgress:
        with self.database.read() as connection:
            repository = TrackingRepository(connection)
            question_set = _require_question_set(repository, question_set_id)
            questions = repository.list_questions(question_set_id)
            attempts = repository.list_attempts_for_set(question_set_id)
            return calculate_progress(question_set, questions, attempts)

    def _create_question(
        self,
        repository: TrackingRepository,
        question_set_id: str,
        label: str,
        parent: Question | None,
    ) -> Question:
        questions = repository.list_questions(question_set_id)
        parent_id = parent.id if parent is not None else None
        if parent is not None and question_depth(parent.id, questions) >= MAX_QUESTION_DEPTH:
            raise QuestionStructureError(
                f"Question hierarchy cannot exceed {MAX_QUESTION_DEPTH} levels"
            )
        siblings = _siblings(questions, parent_id)
        parent_was_leaf = parent is not None and is_leaf(parent.id, questions)
        now = _time_text(_utc_now())
        new_question = Question(
            id=str(uuid4()),
            question_set_id=question_set_id,
            parent_id=parent_id,
            local_label=label,
            sort_order=(siblings[-1].sort_order + 1) if siblings else 0,
        )
        repository.insert_question(new_question, now)
        if parent_was_leaf and parent is not None:
            _transfer_history(repository, (parent.id,), new_question.id, now, None)
        validate_question_structure(repository.list_questions(question_set_id))
        return _require_question(repository, new_question.id)


def _require_question_set(
    repository: TrackingRepository, question_set_id: str
) -> QuestionSet:
    entity = repository.get_question_set(question_set_id)
    if entity is None:
        raise NotFoundError(f"Question set not found: {question_set_id}")
    return entity


def _require_question(repository: TrackingRepository, question_id: str) -> Question:
    entity = repository.get_question(question_id)
    if entity is None:
        raise NotFoundError(f"Question not found: {question_id}")
    return entity


def _require_topic(repository: TrackingRepository, topic_id: str) -> Topic:
    entity = repository.get_topic(topic_id)
    if entity is None:
        raise NotFoundError(f"Topic not found: {topic_id}")
    return entity


def _require_attempt(repository: TrackingRepository, attempt_id: str) -> Attempt:
    entity = repository.get_attempt(attempt_id)
    if entity is None:
        raise NotFoundError(f"Attempt not found: {attempt_id}")
    return entity


def _siblings(
    questions: tuple[Question, ...], parent_id: str | None
) -> list[Question]:
    return sorted(
        (question for question in questions if question.parent_id == parent_id),
        key=lambda question: question.sort_order,
    )


def _rewrite_question_order(
    repository: TrackingRepository,
    question_set_id: str,
    parent_id: str | None,
    questions: list[Question],
    now: str,
) -> None:
    if not questions:
        return
    all_questions = repository.list_questions(question_set_id)
    temporary_start = _next_temporary_order(all_questions) + len(questions)
    for offset, question in enumerate(questions):
        repository.update_question_position(
            question.id, parent_id, temporary_start + offset, now
        )
    for sort_order, question in enumerate(questions):
        repository.update_question_position(question.id, parent_id, sort_order, now)


def _rewrite_topic_order(
    repository: TrackingRepository, topics: list[Topic]
) -> None:
    if not topics:
        return
    temporary_start = max(topic.sort_order for topic in topics) + len(topics) + 1
    for offset, topic in enumerate(topics):
        repository.update_topic_order(topic.id, temporary_start + offset)
    for sort_order, topic in enumerate(topics):
        repository.update_topic_order(topic.id, sort_order)


def _next_temporary_order(questions: tuple[Question, ...]) -> int:
    return max((question.sort_order for question in questions), default=-1) + len(questions) + 1


def _transfer_history(
    repository: TrackingRepository,
    source_ids: tuple[str, ...],
    target_id: str,
    now: str,
    note_conflict_policy: NoteConflictPolicy | str | None,
) -> None:
    target = _require_question(repository, target_id)
    sources = tuple(_require_question(repository, source_id) for source_id in source_ids)
    source_notes = tuple(source.note for source in sources if source.note)
    resolved_note = _resolve_note_conflict(
        target.note, source_notes, note_conflict_policy
    )
    merged_bookmark = target.bookmarked or any(source.bookmarked for source in sources)

    repository.transfer_attempts(source_ids, target.id, now)
    repository.transfer_topic_assignments(source_ids, target.id, target.question_set_id)
    repository.update_question_note(target.id, resolved_note, now)
    repository.update_question_bookmark(target.id, merged_bookmark, now)
    for source in sources:
        repository.update_question_note(source.id, "", now)
        repository.update_question_bookmark(source.id, False, now)


def _resolve_note_conflict(
    target_note: str,
    source_notes: tuple[str, ...],
    policy: NoteConflictPolicy | str | None,
) -> str:
    source_note = "\n\n".join(source_notes)
    if not source_note:
        return target_note
    if not target_note:
        return source_note
    if policy is None:
        raise ValidationError(
            "Source and target both have notes; a note conflict policy is required"
        )

    resolved_policy = _enum_value(
        NoteConflictPolicy, policy, "note conflict policy"
    )
    if resolved_policy is NoteConflictPolicy.KEEP_TARGET:
        return target_note
    if resolved_policy is NoteConflictPolicy.USE_SOURCE:
        return source_note
    return f"{target_note}\n\n{source_note}"


def _roman_label(number: int) -> str:
    result = ""
    for value, label in ((10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i")):
        while number >= value:
            result += label
            number -= value
    return result


def _required_text(value: str, field_name: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValidationError(f"{field_name} cannot be empty")
    return cleaned


def _topic_colour(value: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"#[0-9A-Fa-f]{6}", value) is None:
        raise ValidationError("Topic colour must use #RRGGBB format")
    return value.upper()


def _enum_value(enum_type, value, field_name: str):
    try:
        return enum_type(value)
    except ValueError as error:
        raise ValidationError(f"Invalid {field_name}: {value}") from error


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValidationError("Attempt timestamps must include a timezone")
    return value.astimezone(UTC)


def _time_text(value: datetime) -> str:
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")
