from __future__ import annotations

import sqlite3
from pathlib import PurePosixPath

from question_tracker.domain.library import (
    DocumentType,
    FileAvailability,
    FileRole,
    ManagedFile,
)


class LibraryRepository:
    """SQLite mapping for managed files and question-set file associations."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def insert_file(self, managed_file: ManagedFile, path_key: str, now: str) -> None:
        self.connection.execute(
            """
            INSERT INTO managed_files (
                id, relative_path, path_key, document_type, availability,
                size_bytes, modified_ns, first_seen_at_utc, last_seen_at_utc,
                updated_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                managed_file.id,
                managed_file.relative_path.as_posix(),
                path_key,
                managed_file.document_type.value,
                managed_file.availability.value,
                managed_file.size_bytes,
                managed_file.modified_ns,
                now,
                now,
                now,
            ),
        )

    def get_file(self, file_id: str) -> ManagedFile | None:
        row = self.connection.execute(
            "SELECT * FROM managed_files WHERE id = ?", (file_id,)
        ).fetchone()
        return _managed_file_from_row(row) if row is not None else None

    def get_file_by_path_key(self, path_key: str) -> ManagedFile | None:
        row = self.connection.execute(
            "SELECT * FROM managed_files WHERE path_key = ?", (path_key,)
        ).fetchone()
        return _managed_file_from_row(row) if row is not None else None

    def list_files(self) -> tuple[ManagedFile, ...]:
        rows = self.connection.execute(
            "SELECT * FROM managed_files ORDER BY path_key, id"
        )
        return tuple(_managed_file_from_row(row) for row in rows)

    def mark_present(
        self,
        file_id: str,
        document_type: DocumentType,
        size_bytes: int,
        modified_ns: int,
        now: str,
    ) -> None:
        self.connection.execute(
            """
            UPDATE managed_files
            SET document_type = ?, availability = 'present', size_bytes = ?,
                modified_ns = ?, last_seen_at_utc = ?, updated_at_utc = ?
            WHERE id = ?
            """,
            (document_type.value, size_bytes, modified_ns, now, now, file_id),
        )

    def mark_missing(self, file_id: str, now: str) -> None:
        self.connection.execute(
            """
            UPDATE managed_files
            SET availability = 'missing', updated_at_utc = ?
            WHERE id = ?
            """,
            (now, file_id),
        )

    def add_association(
        self,
        question_set_id: str,
        file_id: str,
        role: FileRole,
        sort_order: int,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO question_set_files (
                question_set_id, file_id, role, sort_order
            ) VALUES (?, ?, ?, ?)
            """,
            (question_set_id, file_id, role.value, sort_order),
        )

    def remove_association(self, question_set_id: str, file_id: str) -> None:
        self.connection.execute(
            """
            DELETE FROM question_set_files
            WHERE question_set_id = ? AND file_id = ?
            """,
            (question_set_id, file_id),
        )

    def association_role(
        self, question_set_id: str, file_id: str
    ) -> FileRole | None:
        row = self.connection.execute(
            """
            SELECT role FROM question_set_files
            WHERE question_set_id = ? AND file_id = ?
            """,
            (question_set_id, file_id),
        ).fetchone()
        return FileRole(row["role"]) if row is not None else None

    def next_reference_order(self, question_set_id: str) -> int:
        row = self.connection.execute(
            """
            SELECT coalesce(max(sort_order), -1) + 1 AS next_order
            FROM question_set_files
            WHERE question_set_id = ? AND role = 'reference'
            """,
            (question_set_id,),
        ).fetchone()
        return row["next_order"]

    def file_ids_for_question_set(
        self, question_set_id: str, role: FileRole | None = None
    ) -> tuple[str, ...]:
        if role is None:
            rows = self.connection.execute(
                """
                SELECT file_id FROM question_set_files
                WHERE question_set_id = ?
                ORDER BY CASE role WHEN 'main' THEN 0 ELSE 1 END, sort_order, file_id
                """,
                (question_set_id,),
            )
        else:
            rows = self.connection.execute(
                """
                SELECT file_id FROM question_set_files
                WHERE question_set_id = ? AND role = ?
                ORDER BY sort_order, file_id
                """,
                (question_set_id, role.value),
            )
        return tuple(row["file_id"] for row in rows)

    def question_set_ids_for_file(
        self, file_id: str, role: FileRole | None = None
    ) -> tuple[str, ...]:
        if role is None:
            rows = self.connection.execute(
                """
                SELECT question_set_id FROM question_set_files
                WHERE file_id = ?
                ORDER BY question_set_id
                """,
                (file_id,),
            )
        else:
            rows = self.connection.execute(
                """
                SELECT question_set_id FROM question_set_files
                WHERE file_id = ? AND role = ?
                ORDER BY question_set_id
                """,
                (file_id, role.value),
            )
        return tuple(row["question_set_id"] for row in rows)


def _managed_file_from_row(row: sqlite3.Row) -> ManagedFile:
    return ManagedFile(
        id=row["id"],
        relative_path=PurePosixPath(row["relative_path"]),
        document_type=DocumentType(row["document_type"]),
        availability=FileAvailability(row["availability"]),
        size_bytes=row["size_bytes"],
        modified_ns=row["modified_ns"],
    )
