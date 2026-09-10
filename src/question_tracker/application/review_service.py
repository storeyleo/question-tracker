"""Current progress and review lists, derived without persistent queue records."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from enum import Enum
from pathlib import PurePosixPath

from question_tracker.domain.library import LibraryModule
from question_tracker.domain.questions import Question, leaf_questions_in_display_order, visible_question_label
from question_tracker.domain.tracking import (
    Attempt, AttemptStatus, QuestionSet, QuestionSetKind, QuestionSetProgress,
    SetupState, calculate_progress,
)
from question_tracker.persistence.database import Database
from question_tracker.persistence.tracking_repository import TrackingRepository


class ReviewFilter(str, Enum):
    REATTEMPT = "All reattempt-needed"
    PARTIAL = "Partial"
    INCORRECT = "Incorrect"
    NO_START = "No Start"
    BOOKMARKED = "Bookmarked"


@dataclass(frozen=True, slots=True)
class ProgressCounts:
    total: int = 0
    correct: int = 0
    partial: int = 0
    incorrect: int = 0
    no_start: int = 0
    bookmarked: int = 0

    @property
    def attempted(self) -> int:
        return self.correct + self.partial + self.incorrect + self.no_start

    @property
    def unattempted(self) -> int:
        return self.total - self.attempted

    @property
    def mastered(self) -> int:
        return self.correct


@dataclass(frozen=True, slots=True)
class ReviewItem:
    module: LibraryModule | None
    question_set: QuestionSet
    question: Question
    label: str
    latest: Attempt | None


@dataclass(frozen=True, slots=True)
class QuestionSetProgressSummary:
    question_set: QuestionSet
    counts: ProgressCounts
    progress: QuestionSetProgress


@dataclass(frozen=True, slots=True)
class ModuleProgressSummary:
    module: LibraryModule | None
    counts: ProgressCounts
    question_sets: tuple[QuestionSetProgressSummary, ...]
    needs_setup: int
    items: tuple[ReviewItem, ...]

    def review_items(self, filter: ReviewFilter | str = ReviewFilter.REATTEMPT) -> tuple[ReviewItem, ...]:
        filter = ReviewFilter(filter)  # Qt may return str-enum item data as plain text.
        def matches(item: ReviewItem) -> bool:
            if filter is ReviewFilter.BOOKMARKED:
                return item.question.bookmarked
            if item.latest is None:
                return False
            if filter is ReviewFilter.REATTEMPT:
                return item.latest.status in (AttemptStatus.PARTIAL, AttemptStatus.INCORRECT, AttemptStatus.NO_START)
            return item.latest.status.value == filter.name.lower()
        return tuple(item for item in self.items if matches(item))


def _counts(items: tuple[ReviewItem, ...]) -> ProgressCounts:
    return ProgressCounts(
        total=len(items),
        correct=sum(item.latest is not None and item.latest.status is AttemptStatus.CORRECT for item in items),
        partial=sum(item.latest is not None and item.latest.status is AttemptStatus.PARTIAL for item in items),
        incorrect=sum(item.latest is not None and item.latest.status is AttemptStatus.INCORRECT for item in items),
        no_start=sum(item.latest is not None and item.latest.status is AttemptStatus.NO_START for item in items),
        bookmarked=sum(item.question.bookmarked for item in items),
    )


class ReviewService:
    def __init__(self, database: Database):
        self.database = database

    def snapshot(self) -> tuple[ModuleProgressSummary, ...]:
        with self.database.read() as connection:
            connection.execute("BEGIN")  # One consistent read across all batches.
            repository = TrackingRepository(connection)
            sets = repository.review_set_paths()
            questions, attempts = repository.review_questions_and_latest(tuple(
                item.id for item, _ in sets if item.setup_state is SetupState.READY
            ))
        by_set = defaultdict(list)
        for question in questions:
            by_set[question.question_set_id].append(question)
        latest = {attempt.question_id: attempt for attempt in attempts}
        by_module = defaultdict(list)
        for question_set, path in sets:
            parts = PurePosixPath(path).parts if path else ()
            module = LibraryModule(parts[0], parts[1]) if len(parts) >= 2 else None
            by_module[module].append(question_set)
        summaries = []
        for module in sorted(by_module, key=lambda m: (m is None, m.year_name.casefold() if m else "", m.module_name.casefold() if m else "", m.year_name if m else "", m.module_name if m else "")):
            breakdown = []
            items = []
            needs_setup = 0
            for question_set in sorted(by_module[module], key=lambda s: (
                s.kind is not QuestionSetKind.PROBLEM_SHEET, s.display_order is None,
                s.display_order if s.display_order is not None else 0,
                s.display_name.casefold(), s.id,
            )):
                if question_set.setup_state is not SetupState.READY:
                    needs_setup += 1
                    continue
                set_questions = tuple(by_set[question_set.id])
                leaves = leaf_questions_in_display_order(set_questions, question_set.id)
                set_items = tuple(ReviewItem(
                    module, question_set, leaf,
                    visible_question_label(leaf.id, set_questions), latest.get(leaf.id),
                ) for leaf in leaves)
                progress = calculate_progress(question_set, set_questions, tuple(
                    latest[leaf.id] for leaf in leaves if leaf.id in latest
                ))
                breakdown.append(QuestionSetProgressSummary(question_set, _counts(set_items), progress))
                items.extend(set_items)
            summaries.append(ModuleProgressSummary(module, _counts(tuple(items)), tuple(breakdown), needs_setup, tuple(items)))
        return tuple(summaries)
