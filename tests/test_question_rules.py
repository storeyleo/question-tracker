from __future__ import annotations

import pytest

from question_tracker.domain.questions import (
    Question,
    QuestionStructureError,
    is_leaf,
    label_components,
    leaf_questions_in_display_order,
    question_depth,
    validate_question_structure,
)


def question(
    question_id: str,
    label: str,
    order: int,
    parent_id: str | None = None,
    question_set_id: str = "set-1",
) -> Question:
    return Question(
        id=question_id,
        question_set_id=question_set_id,
        parent_id=parent_id,
        local_label=label,
        sort_order=order,
    )


def test_hierarchy_uses_local_labels_and_orders_leaves_depth_first() -> None:
    questions = (
        question("q2", "2", 1),
        question("q1ai", "i", 0, "q1a"),
        question("q1", "1", 0),
        question("q1a", "a", 0, "q1"),
    )

    validate_question_structure(questions)

    assert label_components("q1ai", questions) == ("1", "a", "i")
    assert question_depth("q1ai", questions) == 3
    assert is_leaf("q1", questions) is False
    assert is_leaf("q1ai", questions) is True
    assert [
        item.id for item in leaf_questions_in_display_order(questions, "set-1")
    ] == ["q1ai", "q2"]


def test_hierarchy_rejects_more_than_three_levels() -> None:
    questions = (
        question("q1", "1", 0),
        question("q1a", "a", 0, "q1"),
        question("q1ai", "i", 0, "q1a"),
        question("q1aix", "x", 0, "q1ai"),
    )

    with pytest.raises(QuestionStructureError, match="cannot exceed 3 levels"):
        validate_question_structure(questions)


def test_hierarchy_rejects_cycles() -> None:
    questions = (
        question("q1", "1", 0, "q2"),
        question("q2", "2", 0, "q1"),
    )

    with pytest.raises(QuestionStructureError, match="contains a cycle"):
        validate_question_structure(questions)


def test_hierarchy_rejects_cross_set_parenting() -> None:
    questions = (
        question("q1", "1", 0, question_set_id="set-1"),
        question("q2", "a", 0, "q1", question_set_id="set-2"),
    )

    with pytest.raises(QuestionStructureError, match="same question set"):
        validate_question_structure(questions)


def test_hierarchy_rejects_duplicate_sibling_order() -> None:
    questions = (question("q1", "1", 0), question("q2", "2", 0))

    with pytest.raises(QuestionStructureError, match="sort orders must be unique"):
        validate_question_structure(questions)


def test_leaf_check_rejects_unknown_question() -> None:
    with pytest.raises(QuestionStructureError, match="Unknown question ID"):
        is_leaf("missing", (question("q1", "1", 0),))
