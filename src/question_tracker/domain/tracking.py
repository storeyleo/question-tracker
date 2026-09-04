from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from question_tracker.domain.questions import Question, leaf_questions_in_display_order


class QuestionSetKind(str, Enum):
    PROBLEM_SHEET = "problem_sheet"
    PAST_PAPER = "past_paper"


class SetupState(str, Enum):
    NEEDS_SETUP = "needs_setup"
    READY = "ready"


class AttemptStatus(str, Enum):
    CORRECT = "correct"
    PARTIAL = "partial"
    INCORRECT = "incorrect"
    NO_START = "no_start"


class NoteConflictPolicy(str, Enum):
    KEEP_TARGET = "keep_target"
    USE_SOURCE = "use_source"
    COMBINE = "combine"


@dataclass(frozen=True, slots=True)
class QuestionSet:
    id: str
    kind: QuestionSetKind
    display_name: str
    setup_state: SetupState
    general_note: str = ""


@dataclass(frozen=True, slots=True)
class Topic:
    id: str
    question_set_id: str
    title: str
    sort_order: int


@dataclass(frozen=True, slots=True)
class Attempt:
    id: str
    question_id: str
    status: AttemptStatus
    timestamp: datetime
    note: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class QuestionSetProgress:
    leaf_count: int
    complete: bool
    mastered: bool


def latest_attempt(attempts: tuple[Attempt, ...]) -> Attempt | None:
    if not attempts:
        return None
    return max(
        attempts,
        key=lambda attempt: (attempt.timestamp, attempt.created_at, attempt.id),
    )


def calculate_progress(
    question_set: QuestionSet,
    questions: tuple[Question, ...],
    attempts: tuple[Attempt, ...],
) -> QuestionSetProgress:
    leaves = leaf_questions_in_display_order(questions, question_set.id)
    if question_set.setup_state is not SetupState.READY or not leaves:
        return QuestionSetProgress(
            leaf_count=len(leaves), complete=False, mastered=False
        )

    attempts_by_question: dict[str, list[Attempt]] = {}
    for attempt in attempts:
        attempts_by_question.setdefault(attempt.question_id, []).append(attempt)

    latest_by_leaf = {
        leaf.id: latest_attempt(tuple(attempts_by_question.get(leaf.id, [])))
        for leaf in leaves
    }
    complete = all(attempt is not None for attempt in latest_by_leaf.values())
    mastered = complete and all(
        attempt is not None and attempt.status is AttemptStatus.CORRECT
        for attempt in latest_by_leaf.values()
    )
    return QuestionSetProgress(
        leaf_count=len(leaves), complete=complete, mastered=mastered
    )
