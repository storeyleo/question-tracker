from __future__ import annotations

from dataclasses import dataclass

from question_tracker.application.library_service import LibraryService
from question_tracker.application.tracking_service import TrackingService
from question_tracker.domain.library import LibraryModule
from question_tracker.domain.questions import (
    Question,
    question_depth,
    question_subtree_in_display_order,
    visible_question_label,
)
from question_tracker.domain.tracking import (
    Attempt,
    QuestionSet,
    QuestionSetProgress,
    SetupState,
    Topic,
    latest_attempt,
)


@dataclass(frozen=True, slots=True)
class AttemptView:
    attempt: Attempt
    is_latest: bool


@dataclass(frozen=True, slots=True)
class QuestionColumn:
    question: Question
    visible_label: str
    attempts: tuple[AttemptView, ...]
    topic_ids: frozenset[str]
    depth: int = 0

    @property
    def is_unattempted(self) -> bool:
        return not self.attempts


@dataclass(frozen=True, slots=True)
class TopicSpan:
    topic: Topic
    start_column: int
    end_column: int


@dataclass(frozen=True, slots=True)
class HierarchyHeading:
    question: Question
    visible_label: str
    depth: int
    start_column: int
    end_column: int
    is_leaf: bool


@dataclass(frozen=True, slots=True)
class TrackerSnapshot:
    question_set: QuestionSet
    columns: tuple[QuestionColumn, ...]
    topic_spans: tuple[TopicSpan, ...]
    hierarchy_headings: tuple[HierarchyHeading, ...]
    heading_row_count: int
    attempted_count: int
    progress: QuestionSetProgress

    @property
    def parent_spans(self) -> tuple[HierarchyHeading, ...]:
        """Compatibility view containing only non-leaf hierarchy headings."""
        return tuple(span for span in self.hierarchy_headings if not span.is_leaf)

    @property
    def parent_row_count(self) -> int:
        return self.heading_row_count


@dataclass(frozen=True, slots=True)
class ModuleTrackerSnapshot:
    module: LibraryModule | None
    question_sets: tuple[TrackerSnapshot, ...]

    @property
    def columns(self) -> tuple[QuestionColumn, ...]:
        return tuple(
            column
            for snapshot in self.question_sets
            for column in snapshot.columns
        )

    @property
    def attempted_count(self) -> int:
        return sum(snapshot.attempted_count for snapshot in self.question_sets)


def build_tracker_snapshot(
    service: TrackingService, question_set_id: str
) -> TrackerSnapshot:
    """Build immutable display data from the tracking application service."""
    question_set = service.get_question_set(question_set_id)
    questions = service.list_questions(question_set_id)
    leaves = service.leaf_questions(question_set_id)
    columns: list[QuestionColumn] = []

    for question in leaves:
        attempts = service.attempts_for_question(question.id)
        latest = latest_attempt(attempts)
        columns.append(
            QuestionColumn(
                question=question,
                visible_label=visible_question_label(question.id, questions),
                attempts=tuple(
                    AttemptView(
                        attempt=attempt,
                        is_latest=latest is not None and attempt.id == latest.id,
                    )
                    for attempt in attempts
                ),
                topic_ids=frozenset(
                    service.topic_ids_for_question(question.id)
                ),
                depth=question_depth(question.id, questions) - 1,
            )
        )

    topic_spans: list[TopicSpan] = []
    for topic in service.list_topics(question_set_id):
        assigned_columns = [
            index
            for index, column in enumerate(columns)
            if topic.id in column.topic_ids
        ]
        if not assigned_columns:
            continue
        run_start = assigned_columns[0]
        previous = run_start
        for index in assigned_columns[1:]:
            if index != previous + 1:
                topic_spans.append(TopicSpan(topic, run_start, previous))
                run_start = index
            previous = index
        topic_spans.append(TopicSpan(topic, run_start, previous))

    leaf_indexes = {question.id: index for index, question in enumerate(leaves)}
    hierarchy_headings: list[HierarchyHeading] = []
    for question in questions:
        depth = question_depth(question.id, questions) - 1
        if depth >= 2:
            continue
        descendant_columns = [
            leaf_indexes[item.id]
            for item in question_subtree_in_display_order(question.id, questions)
            if item.id in leaf_indexes
        ]
        if not descendant_columns:
            continue
        hierarchy_headings.append(
            HierarchyHeading(
                question=question,
                visible_label=visible_question_label(question.id, questions),
                depth=depth,
                start_column=min(descendant_columns),
                end_column=max(descendant_columns),
                is_leaf=question.id in leaf_indexes,
            )
        )
    hierarchy_headings.sort(key=lambda span: (span.depth, span.start_column))

    return TrackerSnapshot(
        question_set=question_set,
        columns=tuple(columns),
        topic_spans=tuple(topic_spans),
        hierarchy_headings=tuple(hierarchy_headings),
        heading_row_count=2 if columns else 0,
        attempted_count=sum(bool(column.attempts) for column in columns),
        progress=service.question_set_progress(question_set_id),
    )


def build_module_tracker_snapshot(
    service: TrackingService,
    library_service: LibraryService,
    question_set_id: str,
) -> ModuleTrackerSnapshot:
    """Build all ready question-set snapshots for the selected module."""
    module = library_service.module_for_question_set(question_set_id)
    if module is None:
        return ModuleTrackerSnapshot(
            module=None,
            question_sets=(build_tracker_snapshot(service, question_set_id),),
        )

    question_sets = tuple(
        item
        for item in library_service.question_sets_for_module(
            module.year_name, module.module_name
        )
        if item.setup_state is SetupState.READY
    )
    return ModuleTrackerSnapshot(
        module=module,
        question_sets=tuple(
            build_tracker_snapshot(service, item.id) for item in question_sets
        ),
    )
