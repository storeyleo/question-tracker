from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass


MAX_QUESTION_DEPTH = 3


class QuestionStructureError(ValueError):
    """Raised when a question hierarchy violates a domain rule."""


@dataclass(frozen=True, slots=True)
class Question:
    id: str
    question_set_id: str
    parent_id: str | None
    local_label: str
    sort_order: int
    note: str = ""
    bookmarked: bool = False  # Derived "any bookmark type" summary in persisted views.


def validate_question_structure(questions: Iterable[Question]) -> None:
    items = tuple(questions)
    by_id = {question.id: question for question in items}
    if len(by_id) != len(items):
        raise QuestionStructureError("Question IDs must be unique")

    sibling_positions: set[tuple[str, str | None, int]] = set()
    for question in items:
        if not question.id:
            raise QuestionStructureError("Question IDs cannot be empty")
        if not question.question_set_id:
            raise QuestionStructureError("Question-set IDs cannot be empty")
        if not question.local_label.strip():
            raise QuestionStructureError("Question labels cannot be empty")
        if question.sort_order < 0:
            raise QuestionStructureError("Question sort order cannot be negative")

        position = (
            question.question_set_id,
            question.parent_id,
            question.sort_order,
        )
        if position in sibling_positions:
            raise QuestionStructureError("Sibling sort orders must be unique")
        sibling_positions.add(position)

        if question.parent_id is not None:
            parent = by_id.get(question.parent_id)
            if parent is None:
                raise QuestionStructureError("Every parent question must exist")
            if parent.question_set_id != question.question_set_id:
                raise QuestionStructureError(
                    "Parent and child must belong to the same question set"
                )

    for question in items:
        _ancestor_chain(question.id, by_id)


def question_depth(question_id: str, questions: Iterable[Question]) -> int:
    by_id = _validated_index(questions)
    return len(_ancestor_chain(question_id, by_id))


def label_components(question_id: str, questions: Iterable[Question]) -> tuple[str, ...]:
    by_id = _validated_index(questions)
    return tuple(
        by_id[ancestor_id].local_label
        for ancestor_id in reversed(_ancestor_chain(question_id, by_id))
    )


def visible_question_label(question_id: str, questions: Iterable[Question]) -> str:
    components = label_components(question_id, questions)
    if len(components) == 1:
        return components[0]
    label = f"{components[0]}.{components[1]}"
    if len(components) == 3:
        label = f"{label}){components[2]}"
    return label


def is_leaf(question_id: str, questions: Iterable[Question]) -> bool:
    items = tuple(questions)
    by_id = _validated_index(items)
    if question_id not in by_id:
        raise QuestionStructureError(f"Unknown question ID: {question_id}")
    return not any(question.parent_id == question_id for question in items)


def leaf_questions_in_display_order(
    questions: Iterable[Question], question_set_id: str
) -> tuple[Question, ...]:
    items = tuple(questions)
    validate_question_structure(items)

    children: dict[str | None, list[Question]] = defaultdict(list)
    for question in items:
        if question.question_set_id == question_set_id:
            children[question.parent_id].append(question)
    for siblings in children.values():
        siblings.sort(key=lambda question: question.sort_order)

    leaves: list[Question] = []

    def visit(question: Question) -> None:
        descendants = children.get(question.id, [])
        if not descendants:
            leaves.append(question)
            return
        for child in descendants:
            visit(child)

    for root in children.get(None, []):
        visit(root)
    return tuple(leaves)


def question_subtree_in_display_order(
    question_id: str, questions: Iterable[Question]
) -> tuple[Question, ...]:
    items = tuple(questions)
    by_id = _validated_index(items)
    root = by_id.get(question_id)
    if root is None:
        raise QuestionStructureError(f"Unknown question ID: {question_id}")

    children: dict[str, list[Question]] = defaultdict(list)
    for question in items:
        if question.parent_id is not None:
            children[question.parent_id].append(question)
    for siblings in children.values():
        siblings.sort(key=lambda question: question.sort_order)

    result: list[Question] = []

    def visit(question: Question) -> None:
        result.append(question)
        for child in children.get(question.id, []):
            visit(child)

    visit(root)
    return tuple(result)


def leftmost_leaf(question_id: str, questions: Iterable[Question]) -> Question:
    subtree = question_subtree_in_display_order(question_id, questions)
    subtree_ids = {question.id for question in subtree}
    parent_ids = {
        question.parent_id
        for question in subtree
        if question.parent_id in subtree_ids
    }
    return next(question for question in subtree if question.id not in parent_ids)


def _validated_index(questions: Iterable[Question]) -> dict[str, Question]:
    items = tuple(questions)
    validate_question_structure(items)
    return {question.id: question for question in items}


def _ancestor_chain(question_id: str, by_id: dict[str, Question]) -> tuple[str, ...]:
    if question_id not in by_id:
        raise QuestionStructureError(f"Unknown question ID: {question_id}")

    chain: list[str] = []
    seen: set[str] = set()
    current: Question | None = by_id[question_id]
    while current is not None:
        if current.id in seen:
            raise QuestionStructureError("Question hierarchy contains a cycle")
        seen.add(current.id)
        chain.append(current.id)
        if len(chain) > MAX_QUESTION_DEPTH:
            raise QuestionStructureError(
                f"Question hierarchy cannot exceed {MAX_QUESTION_DEPTH} levels"
            )
        current = by_id.get(current.parent_id) if current.parent_id is not None else None
    return tuple(chain)
