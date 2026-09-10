from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path
import os
import tempfile

from question_tracker.persistence.migrations import apply_migrations


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            apply_migrations(connection)

    def backup_to(self, destination: Path) -> None:
        """Atomically publish a consistent snapshot, including committed WAL data."""
        destination = Path(destination)
        if destination.resolve() == self.path.resolve():
            raise ValueError("Cannot export over the live database")
        descriptor, name = tempfile.mkstemp(prefix=".qt-backup-", dir=destination.parent)
        os.close(descriptor)
        temporary = Path(name)
        try:
            with closing(sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True)) as source:
                with closing(sqlite3.connect(temporary)) as target:
                    source.backup(target)
                    target.execute("PRAGMA journal_mode = DELETE")
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        with closing(self.connect()) as connection:
            yield connection

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with closing(self.connect()) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise
