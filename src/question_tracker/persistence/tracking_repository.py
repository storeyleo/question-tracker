from __future__ import annotations

import sqlite3
from datetime import datetime

from question_tracker.domain.questions import Question
from question_tracker.domain.tracking import (
    Attempt,
    AttemptStatus,
    QuestionSet,
    QuestionSetKind,
    SetupState,
    Topic,
)


class TrackingRepository:
    """SQLite mapping for tracking entities within a caller-owned connection."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def insert_question_set(self, question_set: QuestionSet, now: str) -> None:
        self.connection.execute(
            """
            INSERT INTO question_sets (
                id, kind, display_name, general_note, setup_state,
                created_at_utc, updated_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                question_set.id,
                question_set.kind.value,
                question_set.display_name,
                question_set.general_note,
                question_set.setup_state.value,
                now,
                now,
            ),
        )

    def get_question_set(self, question_set_id: str) -> QuestionSet | None:
        row = self.connection.execute(
            "SELECT * FROM question_sets WHERE id = ?", (question_set_id,)
        ).fetchone()
        return _question_set_from_row(row) if row is not None else None

    def list_question_sets(
        self, setup_state: SetupState | None = None
    ) -> tuple[QuestionSet, ...]:
        if setup_state is None:
            rows = self.connection.execute(
                """
                SELECT * FROM question_sets
                ORDER BY CASE setup_state WHEN 'needs_setup' THEN 0 ELSE 1 END,
                         display_name COLLATE NOCASE, id
                """
            )
        else:
            rows = self.connection.execute(
                """
                SELECT * FROM question_sets
                WHERE setup_state = ?
                ORDER BY display_name COLLATE NOCASE, id
                """,
                (setup_state.value,),
            )
        return tuple(_question_set_from_row(row) for row in rows)

    def update_question_set_name(self, question_set_id: str, name: str, now: str) -> None:
        self.connection.execute(
            "UPDATE question_sets SET display_name = ?, updated_at_utc = ? WHERE id = ?",
            (name, now, question_set_id),
        )

    def review_set_paths(self) -> tuple[tuple[QuestionSet, str | None], ...]:
        rows = self.connection.execute(
            "SELECT qs.*, mf.relative_path FROM question_sets qs "
            "LEFT JOIN question_set_files links ON links.question_set_id = qs.id AND links.role = 'main' "
            "LEFT JOIN managed_files mf ON mf.id = links.file_id"
        )
        return tuple((_question_set_from_row(row), row["relative_path"]) for row in rows)

    def review_questions_and_latest(
        self, set_ids: tuple[str, ...],
    ) -> tuple[tuple[Question, ...], tuple[Attempt, ...]]:
        questions: list[Question] = []
        attempts: list[Attempt] = []
        # Bound SQLite parameters; queries scale with batches, never with leaves.
        for start in range(0, len(set_ids), 500):
            batch = set_ids[start:start + 500]
            parameters = ",".join("?" for _ in batch)
            questions.extend(_question_from_row(row) for row in self.connection.execute(
                f"SELECT * FROM questions WHERE question_set_id IN ({parameters})", batch,
            ))
            attempts.extend(_attempt_from_row(row) for row in self.connection.execute(
                f"""SELECT * FROM (
                    SELECT a.*, ROW_NUMBER() OVER (
                        PARTITION BY a.question_id
                        ORDER BY a.occurred_at_utc DESC, a.created_at_utc DESC, a.id DESC
                    ) AS position
                    FROM attempts a JOIN questions q ON q.id = a.question_id
                    WHERE q.question_set_id IN ({parameters})
                ) WHERE position = 1""", batch,
            ))
        return tuple(questions), tuple(attempts)

    def update_question_set_order(self, question_set_id: str, position: int) -> None:
        self.connection.execute(
            "UPDATE question_sets SET display_order = ? WHERE id = ?",
            (position, question_set_id),
        )

    def update_question_set_state(
        self, question_set_id: str, state: SetupState, now: str
    ) -> None:
        self.connection.execute(
            "UPDATE question_sets SET setup_state = ?, updated_at_utc = ? WHERE id = ?",
            (state.value, now, question_set_id),
        )

    def update_question_set_note(
        self, question_set_id: str, note: str, now: str
    ) -> None:
        self.connection.execute(
            "UPDATE question_sets SET general_note = ?, updated_at_utc = ? WHERE id = ?",
            (note, now, question_set_id),
        )

    def insert_question(self, question: Question, now: str) -> None:
        self.connection.execute(
            """
            INSERT INTO questions (
                id, question_set_id, parent_id, local_label, sort_order,
                note, bookmarked, created_at_utc, updated_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                question.id,
                question.question_set_id,
                question.parent_id,
                question.local_label,
                question.sort_order,
                question.note,
                int(question.bookmarked),
                now,
                now,
            ),
        )

    def get_question(self, question_id: str) -> Question | None:
        row = self.connection.execute(
            "SELECT * FROM questions WHERE id = ?", (question_id,)
        ).fetchone()
        return _question_from_row(row) if row is not None else None

    def list_questions(self, question_set_id: str) -> tuple[Question, ...]:
        rows = self.connection.execute(
            """
            SELECT * FROM questions
            WHERE question_set_id = ?
            ORDER BY sort_order, id
            """,
            (question_set_id,),
        )
        return tuple(_question_from_row(row) for row in rows)

    def update_question_label(self, question_id: str, label: str, now: str) -> None:
        self.connection.execute(
            "UPDATE questions SET local_label = ?, updated_at_utc = ? WHERE id = ?",
            (label, now, question_id),
        )

    def update_question_position(
        self, question_id: str, parent_id: str | None, sort_order: int, now: str
    ) -> None:
        self.connection.execute(
            """
            UPDATE questions
            SET parent_id = ?, sort_order = ?, updated_at_utc = ?
            WHERE id = ?
            """,
            (parent_id, sort_order, now, question_id),
        )

    def update_question_note(self, question_id: str, note: str, now: str) -> None:
        self.connection.execute(
            "UPDATE questions SET note = ?, updated_at_utc = ? WHERE id = ?",
            (note, now, question_id),
        )

    def update_question_bookmark(
        self, question_id: str, bookmarked: bool, now: str
    ) -> None:
        self.connection.execute(
            "UPDATE questions SET bookmarked = ?, updated_at_utc = ? WHERE id = ?",
            (int(bookmarked), now, question_id),
        )

    def delete_question(self, question_id: str) -> None:
        self.connection.execute("DELETE FROM questions WHERE id = ?", (question_id,))

    def insert_topic(self, topic: Topic) -> None:
        self.connection.execute(
            """
            INSERT INTO topics (id, question_set_id, title, sort_order, colour)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                topic.id,
                topic.question_set_id,
                topic.title,
                topic.sort_order,
                topic.colour,
            ),
        )

    def get_topic(self, topic_id: str) -> Topic | None:
        row = self.connection.execute(
            "SELECT * FROM topics WHERE id = ?", (topic_id,)
        ).fetchone()
        return _topic_from_row(row) if row is not None else None

    def list_topics(self, question_set_id: str) -> tuple[Topic, ...]:
        rows = self.connection.execute(
            """
            SELECT * FROM topics
            WHERE question_set_id = ?
            ORDER BY sort_order, id
            """,
            (question_set_id,),
        )
        return tuple(_topic_from_row(row) for row in rows)

    def update_topic_title(self, topic_id: str, title: str) -> None:
        self.connection.execute(
            "UPDATE topics SET title = ? WHERE id = ?", (title, topic_id)
        )

    def update_topic_order(self, topic_id: str, sort_order: int) -> None:
        self.connection.execute(
            "UPDATE topics SET sort_order = ? WHERE id = ?", (sort_order, topic_id)
        )

    def update_topic_colour(self, topic_id: str, colour: str) -> None:
        self.connection.execute(
            "UPDATE topics SET colour = ? WHERE id = ?", (colour, topic_id)
        )

    def delete_topic(self, topic_id: str) -> None:
        self.connection.execute("DELETE FROM topics WHERE id = ?", (topic_id,))

    def assign_topic(
        self, question_id: str, topic_id: str, question_set_id: str
    ) -> None:
        self.connection.execute(
            """
            INSERT OR IGNORE INTO question_topics (
                question_id, topic_id, question_set_id
            ) VALUES (?, ?, ?)
            """,
            (question_id, topic_id, question_set_id),
        )

    def remove_topic_assignment(self, question_id: str, topic_id: str) -> None:
        self.connection.execute(
            "DELETE FROM question_topics WHERE question_id = ? AND topic_id = ?",
            (question_id, topic_id),
        )

    def topic_ids_for_question(self, question_id: str) -> tuple[str, ...]:
        rows = self.connection.execute(
            """
            SELECT topic_id FROM question_topics
            WHERE question_id = ?
            ORDER BY topic_id
            """,
            (question_id,),
        )
        return tuple(row["topic_id"] for row in rows)

    def transfer_topic_assignments(
        self, source_ids: tuple[str, ...], target_id: str, question_set_id: str
    ) -> None:
        if not source_ids:
            return
        placeholders = ", ".join("?" for _ in source_ids)
        self.connection.execute(
            f"""
            INSERT OR IGNORE INTO question_topics (
                question_id, topic_id, question_set_id
            )
            SELECT ?, topic_id, ? FROM question_topics
            WHERE question_id IN ({placeholders})
            """,
            (target_id, question_set_id, *source_ids),
        )
        self.connection.execute(
            f"DELETE FROM question_topics WHERE question_id IN ({placeholders})",
            source_ids,
        )

    def insert_attempt(self, attempt: Attempt) -> None:
        self.connection.execute(
            """
            INSERT INTO attempts (
                id, question_id, status, occurred_at_utc, note,
                created_at_utc, updated_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                attempt.id,
                attempt.question_id,
                attempt.status.value,
                _datetime_to_text(attempt.timestamp),
                attempt.note,
                _datetime_to_text(attempt.created_at),
                _datetime_to_text(attempt.created_at),
            ),
        )

    def get_attempt(self, attempt_id: str) -> Attempt | None:
        row = self.connection.execute(
            "SELECT * FROM attempts WHERE id = ?", (attempt_id,)
        ).fetchone()
        return _attempt_from_row(row) if row is not None else None

    def list_attempts_for_question(self, question_id: str) -> tuple[Attempt, ...]:
        rows = self.connection.execute(
            """
            SELECT * FROM attempts
            WHERE question_id = ?
            ORDER BY occurred_at_utc, created_at_utc, id
            """,
            (question_id,),
        )
        return tuple(_attempt_from_row(row) for row in rows)

    def list_attempts_for_set(self, question_set_id: str) -> tuple[Attempt, ...]:
        rows = self.connection.execute(
            """
            SELECT attempts.* FROM attempts
            JOIN questions ON questions.id = attempts.question_id
            WHERE questions.question_set_id = ?
            ORDER BY attempts.occurred_at_utc, attempts.created_at_utc, attempts.id
            """,
            (question_set_id,),
        )
        return tuple(_attempt_from_row(row) for row in rows)

    def update_attempt(
        self,
        attempt_id: str,
        status: AttemptStatus,
        timestamp: datetime,
        note: str,
        updated_at: datetime,
    ) -> None:
        self.connection.execute(
            """
            UPDATE attempts
            SET status = ?, occurred_at_utc = ?, note = ?, updated_at_utc = ?
            WHERE id = ?
            """,
            (
                status.value,
                _datetime_to_text(timestamp),
                note,
                _datetime_to_text(updated_at),
                attempt_id,
            ),
        )

    def delete_attempt(self, attempt_id: str) -> None:
        self.connection.execute("DELETE FROM attempts WHERE id = ?", (attempt_id,))

    def delete_attempts_for_set(self, question_set_id: str) -> None:
        self.connection.execute(
            """
            DELETE FROM attempts
            WHERE question_id IN (
                SELECT id FROM questions WHERE question_set_id = ?
            )
            """,
            (question_set_id,),
        )

    def delete_questions_for_set(self, question_set_id: str) -> None:
        self.connection.execute(
            "DELETE FROM questions WHERE question_set_id = ?",
            (question_set_id,),
        )

    def transfer_attempts(
        self, source_ids: tuple[str, ...], target_id: str, updated_at: str
    ) -> None:
        if not source_ids:
            return
        placeholders = ", ".join("?" for _ in source_ids)
        self.connection.execute(
            f"""
            UPDATE attempts
            SET question_id = ?, updated_at_utc = ?
            WHERE question_id IN ({placeholders})
            """,
            (target_id, updated_at, *source_ids),
        )


def _question_from_row(row: sqlite3.Row) -> Question:
    return Question(
        id=row["id"],
        question_set_id=row["question_set_id"],
        parent_id=row["parent_id"],
        local_label=row["local_label"],
        sort_order=row["sort_order"],
        note=row["note"],
        bookmarked=bool(row["bookmarked"]),
    )


def _question_set_from_row(row: sqlite3.Row) -> QuestionSet:
    return QuestionSet(
        id=row["id"],
        kind=QuestionSetKind(row["kind"]),
        display_name=row["display_name"],
        setup_state=SetupState(row["setup_state"]),
        general_note=row["general_note"],
        display_order=row["display_order"],
    )


def _topic_from_row(row: sqlite3.Row) -> Topic:
    return Topic(
        id=row["id"],
        question_set_id=row["question_set_id"],
        title=row["title"],
        sort_order=row["sort_order"],
        colour=row["colour"],
    )


def _attempt_from_row(row: sqlite3.Row) -> Attempt:
    return Attempt(
        id=row["id"],
        question_id=row["question_id"],
        status=AttemptStatus(row["status"]),
        timestamp=_datetime_from_text(row["occurred_at_utc"]),
        note=row["note"],
        created_at=_datetime_from_text(row["created_at_utc"]),
    )


def _datetime_to_text(value: datetime) -> str:
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _datetime_from_text(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
