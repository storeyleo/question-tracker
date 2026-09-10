"""Daily SQLite snapshots and validated, replace-only portable bundles."""
from __future__ import annotations

import json
import hashlib
import logging
import os
import re
import shutil
import sqlite3
import stat
import tempfile
import zipfile
import zlib
from contextlib import closing, contextmanager
from datetime import datetime
from pathlib import Path, PurePosixPath, PureWindowsPath

from question_tracker.paths import ApplicationPaths
from question_tracker.persistence.database import Database
from question_tracker.persistence.migrations import MigrationError

FORMAT = "question-tracker"
FORMAT_VERSION = 1
AUTOMATIC_RETENTION = 14
AUTO_NAME = re.compile(r"auto_question_tracker_\d{4}-\d{2}-\d{2}_\d{6}\.sqlite3")
LOG = logging.getLogger(__name__)


class BackupError(RuntimeError):
    pass


class RecoveryError(BackupError):
    """Rollback storage is retained; the UI must not reopen a mixed library."""


def _safe_member(name: str) -> PurePosixPath:
    if not isinstance(name, str):
        raise BackupError("Archive and managed-file paths must be text")
    parts = name.rstrip("/").split("/")
    if not name or "\\" in name or PureWindowsPath(name).drive:
        raise BackupError(f"Unsafe archive path: {name}")
    for part in parts:
        if (
            part in ("", ".", "..") or part.endswith((".", " "))
            or any(c in part for c in '<>:"|?*\0')
            or PureWindowsPath(part).is_reserved()
        ):
            raise BackupError(f"Unsafe archive path: {name}")
    return PurePosixPath(*parts)


def _schema(connection):
    return {
        (row[0], row[1]): re.sub(r"\s*([(),])\s*", r"\1", " ".join(row[2].split())).casefold()
        for row in connection.execute(
            "SELECT type, name, sql FROM sqlite_master WHERE sql IS NOT NULL "
            "AND name NOT LIKE 'sqlite_%'"
        )
    }


class BackupService:
    def __init__(self, paths: ApplicationPaths):
        self.paths = paths
        self.database = Database(paths.database_path)

    def automatic_backup(self, now: datetime | None = None) -> Path | None:
        """At most one per local calendar day; failure never blocks startup."""
        try:
            now = now or datetime.now().astimezone()
            self.paths.backups_dir.mkdir(parents=True, exist_ok=True)
            existing = sorted(
                path for path in self.paths.backups_dir.iterdir()
                if AUTO_NAME.fullmatch(path.name) and path.is_file() and not path.is_symlink()
            )
            prefix = f"auto_question_tracker_{now:%Y-%m-%d}_"
            today = next((path for path in existing if path.name.startswith(prefix)), None)
            if today is None:
                today = self.paths.backups_dir / f"{prefix}{now:%H%M%S}.sqlite3"
                self.database.backup_to(today)
                existing.append(today)
            for old in sorted(existing)[:-AUTOMATIC_RETENTION]:
                old.unlink()
            return today
        except (OSError, sqlite3.Error, ValueError):
            LOG.exception("Automatic database backup failed")
            return None

    def _export_destination(self, destination: Path) -> Path:
        destination = Path(destination).resolve()
        if (
            destination == self.paths.database_path.resolve()
            or destination.name in (self.paths.database_path.name + "-wal", self.paths.database_path.name + "-shm")
            or destination.is_relative_to(self.paths.managed_library_root.resolve())
        ):
            raise BackupError("Choose an export destination outside the live database and managed files")
        return destination

    def export_database(self, destination: Path) -> None:
        try:
            self.database.backup_to(self._export_destination(destination))
        except (OSError, sqlite3.Error, ValueError) as error:
            raise BackupError(f"Could not export database: {error}") from error

    def export_bundle(self, destination: Path) -> None:
        destination = self._export_destination(destination)
        try:
            with tempfile.TemporaryDirectory(prefix=".qt-export-", dir=destination.parent) as name:
                staging = Path(name)
                snapshot = staging / "database.sqlite3"
                self.database.backup_to(snapshot)
                root = self.paths.managed_library_root
                entries = sorted(root.rglob("*"))
                for path in entries:
                    _safe_member("files/" + path.relative_to(root).as_posix())
                    if path.is_symlink() or not (path.is_file() or path.is_dir()):
                        raise BackupError("The managed library contains a link or special file")
                self._check_files(snapshot, root)
                with closing(sqlite3.connect(snapshot)) as connection:
                    version = connection.execute("SELECT max(version) FROM schema_migrations").fetchone()[0]
                manifest = {
                    "format": FORMAT, "format_version": FORMAT_VERSION,
                    "schema_version": version,
                    "exported_at": datetime.now().astimezone().isoformat(),
                    "database": "database.sqlite3", "files_root": "files",
                }
                archive_path = staging / "bundle.zip"
                with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
                    archive.writestr("manifest.json", json.dumps(manifest))
                    archive.write(snapshot, "database.sqlite3")
                    archive.writestr("files/", b"")
                    for path in entries:
                        before = path.stat()
                        archive.write(path, "files/" + path.relative_to(root).as_posix())
                        after = path.stat()
                        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                            raise BackupError("Managed files changed during export; refresh and retry")
                if entries != sorted(root.rglob("*")):
                    raise BackupError("Managed files changed during export; refresh and retry")
                self._check_files(snapshot, root)
                os.replace(archive_path, destination)
        except (OSError, sqlite3.Error, zipfile.BadZipFile) as error:
            raise BackupError(f"Could not export bundle: {error}") from error

    @staticmethod
    def _check_files(database_path: Path, root: Path) -> None:
        with closing(sqlite3.connect(database_path)) as connection:
            for relative, availability, digest in connection.execute(
                "SELECT relative_path, availability, sha256 FROM managed_files"
            ):
                path = root.joinpath(*_safe_member(relative).parts)
                if availability == "present" and not path.is_file():
                    raise BackupError(f"Present managed file is absent: {relative}")
                if availability == "missing" and path.exists():
                    raise BackupError(f"Missing-file state differs from contents: {relative}. Refresh and retry.")
                if availability == "present" and digest:
                    with path.open("rb") as stream:
                        if hashlib.file_digest(stream, "sha256").hexdigest() != digest:
                            raise BackupError(f"Managed file contents differ from metadata: {relative}. Refresh and retry.")

    @contextmanager
    def prepare_import(self, source: Path):
        """Validate/extract before UI confirmation; cancellation only discards staging."""
        try:
            with tempfile.TemporaryDirectory(prefix=".qt-import-", dir=self.paths.data_dir) as name:
                staging = Path(name)
                with zipfile.ZipFile(source) as archive:
                    seen = set()
                    spelling = {}
                    for member in archive.infolist():
                        # filename is normalized by zipfile on Windows; validate
                        # the original archive name before filesystem conversion.
                        relative = _safe_member(member.orig_filename)
                        for length in range(1, len(relative.parts) + 1):
                            prefix = "/".join(relative.parts[:length])
                            previous = spelling.setdefault(prefix.casefold(), prefix)
                            if previous != prefix:
                                raise BackupError("Archive paths differ only by letter case")
                        key = relative.as_posix().casefold()
                        if key in seen:
                            raise BackupError("Duplicate archive path")
                        seen.add(key)
                        mode = stat.S_IFMT(member.external_attr >> 16)
                        if mode not in (0, stat.S_IFREG, stat.S_IFDIR):
                            raise BackupError("Archive contains a link or special file")
                        if relative.parts[0] not in ("manifest.json", "database.sqlite3", "files"):
                            raise BackupError("Unexpected bundle member")
                        target = staging.joinpath(*relative.parts)
                        if member.is_dir():
                            target.mkdir(parents=True, exist_ok=True)
                        else:
                            target.parent.mkdir(parents=True, exist_ok=True)
                            with archive.open(member) as incoming, target.open("xb") as output:
                                shutil.copyfileobj(incoming, output)
                manifest = json.loads((staging / "manifest.json").read_text(encoding="utf-8"))
                if not isinstance(manifest, dict) or any(
                    manifest.get(key) != value for key, value in {
                        "format": FORMAT, "format_version": FORMAT_VERSION,
                        "database": "database.sqlite3", "files_root": "files",
                    }.items()
                ):
                    raise BackupError("Unsupported or invalid bundle manifest")
                if not isinstance(manifest.get("schema_version"), int) or not isinstance(manifest.get("exported_at"), str):
                    raise BackupError("Missing manifest metadata")
                if not (staging / "database.sqlite3").is_file() or not (staging / "files").is_dir():
                    raise BackupError("Bundle must contain a database and files directory")
                self._validate_database(staging, manifest["schema_version"])
                self._check_files(staging / "database.sqlite3", staging / "files")
                yield staging
        except (OSError, sqlite3.Error, MigrationError, ValueError, RuntimeError, zipfile.BadZipFile, EOFError, zlib.error) as error:
            if isinstance(error, BackupError):
                raise
            raise BackupError(f"Could not import bundle: {error}") from error

    @staticmethod
    def _validate_database(staging: Path, declared_version: int) -> None:
        expected = Database(staging / "expected.sqlite3")
        expected.initialize()
        with expected.read() as connection:
            migrations = [tuple(row) for row in connection.execute("SELECT version, name FROM schema_migrations ORDER BY version")]
            schema = _schema(connection)
        imported = Database(staging / "database.sqlite3")
        with imported.read() as connection:
            connection.execute("PRAGMA trusted_schema = OFF")
            applied = [tuple(row) for row in connection.execute("SELECT version, name FROM schema_migrations ORDER BY version")]
            if not applied or applied != migrations[:len(applied)] or declared_version != applied[-1][0]:
                raise BackupError("Unsupported or invalid Question Tracker schema version")
            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise BackupError("Invalid SQLite database")
        imported.initialize()
        with imported.read() as connection:
            if _schema(connection) != schema or connection.execute("PRAGMA foreign_key_check").fetchone():
                raise BackupError("Invalid Question Tracker database schema or associations")

    def replace_from_staging(self, staging: Path) -> None:
        """Call only after confirmation and releasing UI resources. Never merge."""
        root = self.paths.managed_library_root
        database_path = self.paths.database_path
        safety = self.paths.backups_dir / f"before_import_{datetime.now():%Y-%m-%d_%H%M%S_%f}.sqlite3"
        work = None
        preserve_rollback = False
        try:
            self.paths.backups_dir.mkdir(parents=True, exist_ok=True)
            self.database.backup_to(safety)
            # Stage on each destination's filesystem so each rename stays atomic.
            work = Path(tempfile.mkdtemp(prefix=".qt-files-", dir=root.parent))
            incoming = work / "incoming"
            shutil.copytree(staging / "files", incoming)
            old_files = work / "previous"
            old_database = safety.with_suffix(".rollback.sqlite3")
            with self.database.read() as connection:
                if connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0]:
                    raise BackupError("Database is busy; close other app instances and retry")
                connection.execute("PRAGMA journal_mode = DELETE")
            files_moved = database_moved = installed_files = installed_database = False
            preserve_rollback = True
            try:
                if root.exists():
                    os.replace(root, old_files)
                    files_moved = True
                os.replace(database_path, old_database)
                database_moved = True
                os.replace(incoming, root)
                installed_files = True
                os.replace(staging / "database.sqlite3", database_path)
                installed_database = True
                self.database.initialize()
            except Exception as original:
                try:
                    if installed_database:
                        database_path.unlink()
                        for suffix in ("-wal", "-shm"):
                            Path(str(database_path) + suffix).unlink(missing_ok=True)
                    if database_moved:
                        os.replace(old_database, database_path)
                    if installed_files:
                        shutil.rmtree(root)
                    if files_moved:
                        os.replace(old_files, root)
                except OSError as rollback_error:
                    raise RecoveryError(
                        f"Import failed and rollback could not finish. Original files are retained at {old_files}; "
                        f"database safety backup: {safety}. Close the app before recovery."
                    ) from rollback_error
                preserve_rollback = False
                raise original
            preserve_rollback = False
            try:
                old_database.unlink(missing_ok=True)
            except OSError:
                LOG.warning("Import succeeded; old database retained at %s", old_database)
        except (OSError, sqlite3.Error, MigrationError) as error:
            raise BackupError(f"Could not replace local library: {error}") from error
        finally:
            if work is not None and not preserve_rollback:
                shutil.rmtree(work, ignore_errors=True)
