from __future__ import annotations

from contextlib import closing

from question_tracker.paths import ApplicationPaths
from question_tracker.persistence.database import Database


EXPECTED_TABLES = {
    "attempts",
    "managed_files",
    "question_set_files",
    "question_sets",
    "question_topics",
    "questions",
    "schema_migrations",
    "topics",
    "module_groups",
    "module_group_assignments",
}


def test_application_paths_create_data_and_managed_library_roots(
    application_paths: ApplicationPaths,
) -> None:
    application_paths.ensure_created()

    assert application_paths.data_dir.is_dir()
    assert application_paths.managed_library_root.is_dir()
    assert application_paths.managed_library_root.name == "files"


def test_database_initialization_creates_schema(
    application_paths: ApplicationPaths,
) -> None:
    application_paths.ensure_created()
    database = Database(application_paths.database_path)

    database.initialize()

    assert application_paths.database_path.is_file()
    with closing(database.connect()) as connection:
        tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        migrations = connection.execute(
            "SELECT version, name FROM schema_migrations ORDER BY version"
        ).fetchall()
        foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()[0]

    assert EXPECTED_TABLES <= tables
    assert [dict(migration) for migration in migrations] == [
        {"version": 1, "name": "001_initial.sql"},
        {"version": 2, "name": "002_question_set_order.sql"},
        {"version": 3, "name": "003_topic_colour.sql"},
        {"version": 4, "name": "004_file_hash.sql"},
        {"version": 5, "name": "005_module_groups.sql"},
    ]
    assert foreign_keys == 1


def test_database_initialization_is_idempotent(
    application_paths: ApplicationPaths,
) -> None:
    database = Database(application_paths.database_path)

    database.initialize()
    database.initialize()

    with closing(database.connect()) as connection:
        migration_count = connection.execute(
            "SELECT count(*) FROM schema_migrations"
        ).fetchone()[0]

    assert migration_count == 5
