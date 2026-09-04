from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path


_MIGRATION_NAME = re.compile(r"^(?P<version>\d{3})_[a-z0-9_]+\.sql$")


class MigrationError(RuntimeError):
    """Raised when database migrations cannot be applied safely."""


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    path: Path


def apply_migrations(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            applied_at_utc TEXT NOT NULL
        )
        """
    )
    connection.commit()

    migrations = _discover_migrations()
    applied = {
        row["version"]: row["name"]
        for row in connection.execute(
            "SELECT version, name FROM schema_migrations ORDER BY version"
        )
    }

    known_versions = {migration.version for migration in migrations}
    unknown_versions = set(applied) - known_versions
    if unknown_versions:
        raise MigrationError(
            f"Database contains unknown migration versions: {sorted(unknown_versions)}"
        )

    for migration in migrations:
        applied_name = applied.get(migration.version)
        if applied_name is not None:
            if applied_name != migration.name:
                raise MigrationError(
                    f"Migration {migration.version} is recorded as {applied_name!r}, "
                    f"not {migration.name!r}"
                )
            continue

        script = migration.path.read_text(encoding="utf-8")
        transaction = f"""
        BEGIN IMMEDIATE;
        {script}
        INSERT INTO schema_migrations (version, name, applied_at_utc)
        VALUES (
            {migration.version},
            '{migration.name}',
            strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
        );
        COMMIT;
        """
        try:
            connection.executescript(transaction)
        except sqlite3.Error as error:
            if connection.in_transaction:
                connection.rollback()
            raise MigrationError(
                f"Failed to apply migration {migration.name}"
            ) from error


def _discover_migrations() -> tuple[Migration, ...]:
    directory = Path(__file__).with_name("sql_migrations")
    migrations: list[Migration] = []
    for path in directory.glob("*.sql"):
        match = _MIGRATION_NAME.fullmatch(path.name)
        if match is None:
            raise MigrationError(f"Invalid migration filename: {path.name}")
        migrations.append(
            Migration(version=int(match.group("version")), name=path.name, path=path)
        )

    migrations.sort(key=lambda migration: migration.version)
    versions = [migration.version for migration in migrations]
    if versions != list(range(1, len(migrations) + 1)):
        raise MigrationError("Migration versions must be consecutive and start at 001")
    return tuple(migrations)

