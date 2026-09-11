from contextlib import closing
from datetime import datetime, timedelta
import json
from pathlib import Path
import sqlite3
import stat
import zipfile

import pytest

from question_tracker.application.backup_service import BackupError, BackupService
from question_tracker.application.library_service import LibraryService
from question_tracker.application.tracking_service import TrackingService
from question_tracker.domain.library import DocumentType, FileRole
from question_tracker.domain.tracking import AttemptStatus
from question_tracker.paths import ApplicationPaths
from question_tracker.persistence.database import Database


def seed(base):
    paths = ApplicationPaths.for_test(base)
    paths.ensure_created()
    database = Database(paths.database_path)
    database.initialize()
    library = LibraryService(database, paths.managed_library_root)
    tracking = TrackingService(database)
    library.create_year("Year 2")
    module = library.create_module("Year 2", "Linear Algebra")
    for relative in ("Problem Sheets/nested/Sheet.pdf", "Problem Sheet Solutions/Solution.pdf", "Past Papers/Missing.pdf"):
        path = module / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"%PDF-1.4\n" + relative.encode() + b"\n%%EOF")
    library.refresh()
    main = next(file for file in library.list_files() if file.document_type is DocumentType.PROBLEM_SHEET)
    reference = next(file for file in library.list_files() if file.document_type is DocumentType.SOLUTION)
    missing = next(file for file in library.list_files() if file.document_type is DocumentType.PAST_PAPER)
    library.path_for_file(missing.id).unlink()
    library.refresh()
    question_set = library.question_sets_for_file(main.id, FileRole.MAIN)[0]
    root = tracking.create_question(question_set.id, "1")
    leaf = tracking.add_child(root.id, "a")
    tracking.set_question_note(leaf.id, "Leaf note")
    tracking.set_question_set_note(question_set.id, "Set note")
    tracking.toggle_question_bookmark(leaf.id)
    topic = tracking.create_topic(question_set.id, "Matrices")
    tracking.assign_topic(leaf.id, topic.id)
    tracking.add_attempt(leaf.id, AttemptStatus.PARTIAL, note="First")
    tracking.add_attempt(leaf.id, AttemptStatus.CORRECT, note="Second")
    tracking.finish_question_set_setup(question_set.id)
    library.associate_reference(question_set.id, reference.id)
    return paths


def rows(path):
    with closing(sqlite3.connect(path)) as connection:
        tables = [row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )]
        return {table: connection.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall() for table in tables}


def files(root):
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def test_database_export_and_daily_backup_include_committed_wal(tmp_path):
    paths = seed(tmp_path / "live")
    service = BackupService(paths)
    with closing(sqlite3.connect(paths.database_path)) as writer:
        writer.execute("UPDATE question_sets SET general_note='Committed WAL note'")
        writer.commit()
        before = rows(paths.database_path)
        exported = tmp_path / "manual.sqlite3"
        service.export_database(exported)
        first = service.automatic_backup(datetime(2026, 9, 10, 10))
        second = service.automatic_backup(datetime(2026, 9, 10, 15))
        assert first == second
        assert rows(exported) == rows(first) == before == rows(paths.database_path)
        assert not (tmp_path / "files").exists()


def test_retention_only_prunes_automatic_namespace(tmp_path):
    paths = seed(tmp_path / "live")
    service = BackupService(paths)
    paths.backups_dir.mkdir()
    manual = paths.backups_dir / "manual.sqlite3"
    manual.write_bytes(b"keep")
    safety = paths.backups_dir / "before_import_old.sqlite3"
    safety.write_bytes(b"keep too")
    for day in range(17):
        assert service.automatic_backup(datetime(2026, 9, 1) + timedelta(days=day))
    automatic = sorted(paths.backups_dir.glob("auto_question_tracker_*.sqlite3"))
    assert len(automatic) == 14
    assert "2026-09-04" in automatic[0].name
    assert manual.read_bytes() == b"keep"
    assert safety.read_bytes() == b"keep too"


def test_backup_failure_is_nonfatal_and_logged(tmp_path, monkeypatch, caplog):
    paths = seed(tmp_path / "live")
    def fail(*args):
        raise PermissionError("locked")
    monkeypatch.setattr(Database, "backup_to", fail)
    assert BackupService(paths).automatic_backup() is None
    assert "Automatic database backup failed" in caplog.text


def test_bundle_round_trip_replaces_all_rows_files_and_keeps_safety_backup(tmp_path):
    source = seed(tmp_path / "source")
    archive_path = tmp_path / "portable.zip"
    BackupService(source).export_bundle(archive_path)
    with zipfile.ZipFile(archive_path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["format_version"] == 1
        assert manifest["database"] == "database.sqlite3"
        assert manifest["files_root"] == "files"
        assert str(source.data_dir) not in json.dumps(manifest)
        assert "database.sqlite3" in archive.namelist()
        assert all("files/" + name in archive.namelist() for name in files(source.managed_library_root))
    target = seed(tmp_path / "target")
    old_only = target.managed_library_root / "old-only.txt"
    old_only.write_text("old")
    old_rows = rows(target.database_path)
    service = BackupService(target)
    with service.prepare_import(archive_path) as staged:
        assert old_only.exists()  # Preparation alone does not change live data.
        service.replace_from_staging(staged)
    assert rows(target.database_path) == rows(source.database_path)
    assert files(target.managed_library_root) == files(source.managed_library_root)
    assert not old_only.exists()
    assert rows(next(target.backups_dir.glob("before_import_*.sqlite3"))) == old_rows


def rewrite_bundle(source, target, *, extra=None, manifest=None, database=None):
    with zipfile.ZipFile(source) as original, zipfile.ZipFile(target, "w") as output:
        for item in original.infolist():
            content = original.read(item)
            if item.filename == "manifest.json" and manifest is not None:
                content = json.dumps(manifest).encode()
            if item.filename == "database.sqlite3" and database is not None:
                content = database
            output.writestr(item, content)
        if extra is not None:
            output.writestr(extra, b"unsafe")


@pytest.mark.parametrize("unsafe", (
    "../escaped", "/absolute", "C:/drive.pdf", "files/../escape",
    r"files\escape", r"nested\path\file.pdf", r"mixed/separator\file.pdf",
    "files/name:stream", "files/CON.pdf",
))
def test_unsafe_archive_paths_leave_live_library_unchanged(tmp_path, unsafe):
    paths = seed(tmp_path / "live")
    service = BackupService(paths)
    valid = tmp_path / "valid.zip"
    service.export_bundle(valid)
    invalid = tmp_path / "invalid.zip"
    # ZipInfo's constructor normalizes backslashes on Windows. Override the
    # filename afterward so the test ZIP really contains the malicious name.
    member = zipfile.ZipInfo("placeholder")
    member.filename = member.orig_filename = unsafe
    rewrite_bundle(valid, invalid, extra=member)
    with zipfile.ZipFile(invalid) as archive:
        assert archive.infolist()[-1].orig_filename == unsafe
    before = rows(paths.database_path), files(paths.managed_library_root)
    with pytest.raises(BackupError):
        with service.prepare_import(invalid):
            pytest.fail("Unsafe bundle accepted")
    assert (rows(paths.database_path), files(paths.managed_library_root)) == before


@pytest.mark.parametrize("fault", ("manifest", "database", "arbitrary_db", "future_schema", "symlink", "missing_pdf"))
def test_invalid_bundle_does_not_change_live_library(tmp_path, fault):
    paths = seed(tmp_path / "live")
    service = BackupService(paths)
    valid = tmp_path / "valid.zip"
    invalid = tmp_path / "invalid.zip"
    service.export_bundle(valid)
    with zipfile.ZipFile(valid) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        database_bytes = archive.read("database.sqlite3")
    extra = None
    if fault == "manifest":
        manifest["format_version"] = 999
    elif fault == "database":
        database_bytes = b"not SQLite"
    elif fault in ("arbitrary_db", "future_schema"):
        temporary = tmp_path / "modified.sqlite3"
        if fault == "future_schema":
            temporary.write_bytes(database_bytes)
        with closing(sqlite3.connect(temporary)) as connection:
            if fault == "future_schema":
                connection.execute("INSERT INTO schema_migrations VALUES (999, '999_future.sql', 'today')")
                manifest["schema_version"] = 999
            else:
                connection.execute("CREATE TABLE unrelated (value TEXT)")
            connection.commit()
        database_bytes = temporary.read_bytes()
    elif fault == "symlink":
        extra = zipfile.ZipInfo("files/link")
        extra.create_system = 3
        extra.external_attr = (stat.S_IFLNK | 0o777) << 16
    rewrite_bundle(valid, invalid, manifest=manifest, database=database_bytes, extra=extra)
    if fault == "missing_pdf":
        with zipfile.ZipFile(valid) as original, zipfile.ZipFile(invalid, "w") as output:
            for item in original.infolist():
                if not item.filename.endswith("Sheet.pdf"):
                    output.writestr(item, original.read(item))
    before = rows(paths.database_path), files(paths.managed_library_root)
    with pytest.raises(BackupError):
        with service.prepare_import(invalid):
            pytest.fail("Invalid bundle accepted")
    assert (rows(paths.database_path), files(paths.managed_library_root)) == before


@pytest.mark.parametrize("failure", ("files", "database", "initialize"))
def test_replacement_failure_rolls_back_database_and_files(tmp_path, monkeypatch, failure):
    import question_tracker.application.backup_service as module
    paths = seed(tmp_path / "live")
    service = BackupService(paths)
    archive = tmp_path / "bundle.zip"
    service.export_bundle(archive)
    (paths.managed_library_root / "old-only.txt").write_text("keep")
    before = rows(paths.database_path), files(paths.managed_library_root)
    real_replace = module.os.replace
    def fail_install(source, destination):
        if (
            failure == "database" and Path(source).name == "database.sqlite3" and Path(destination) == paths.database_path
            or failure == "files" and Path(source).name == "incoming" and Path(destination) == paths.managed_library_root
        ):
            raise PermissionError("simulated install failure")
        return real_replace(source, destination)
    with service.prepare_import(archive) as staged:
        monkeypatch.setattr(module.os, "replace", fail_install)
        if failure == "initialize":
            def fail_initialize(*args):
                raise sqlite3.OperationalError("simulated initialization failure")
            monkeypatch.setattr(Database, "initialize", fail_initialize)
        with pytest.raises(BackupError):
            service.replace_from_staging(staged)
    assert (rows(paths.database_path), files(paths.managed_library_root)) == before


def test_rollback_failure_retains_original_files_outside_staging(tmp_path, monkeypatch):
    import question_tracker.application.backup_service as module
    paths = seed(tmp_path / "live")
    service = BackupService(paths)
    archive = tmp_path / "bundle.zip"
    service.export_bundle(archive)
    (paths.managed_library_root / "old-only.txt").write_text("keep")
    old_files = files(paths.managed_library_root)
    real_replace = module.os.replace
    def fail_twice(source, destination):
        if Path(source).name in ("database.sqlite3", "previous"):
            raise PermissionError("locked")
        return real_replace(source, destination)
    with pytest.raises(module.RecoveryError):
        with service.prepare_import(archive) as staged:
            monkeypatch.setattr(module.os, "replace", fail_twice)
            service.replace_from_staging(staged)
    retained = next(paths.managed_library_root.parent.glob(".qt-files-*/previous"))
    assert files(retained) == old_files
    assert list(paths.backups_dir.glob("before_import_*.sqlite3"))


def test_export_fails_if_present_pdf_disappears(tmp_path):
    paths = seed(tmp_path / "live")
    next(paths.managed_library_root.rglob("Sheet.pdf")).unlink()
    destination = tmp_path / "bundle.zip"
    destination.write_bytes(b"previous export")
    with pytest.raises(BackupError, match="absent"):
        BackupService(paths).export_bundle(destination)
    assert destination.read_bytes() == b"previous export"


@pytest.mark.parametrize("confirm", (False, True))
def test_import_action_cancel_or_rebuild_home(tmp_path, qtbot, monkeypatch, confirm):
    from PySide6.QtGui import QUndoCommand
    from PySide6.QtWidgets import QFileDialog, QMessageBox
    from shiboken6 import isValid
    from question_tracker.app import MainWindow
    from question_tracker.ui.home import HomeScreen

    source = seed(tmp_path / "source")
    archive = tmp_path / "bundle.zip"
    BackupService(source).export_bundle(archive)
    target = seed(tmp_path / "target")
    database = Database(target.database_path)
    window = MainWindow(target, TrackingService(database), LibraryService(database, target.managed_library_root))
    qtbot.addWidget(window)
    old_home = window.centralWidget()
    old_service = window.tracking_service
    window.undo_stack.push(QUndoCommand("Old command"))
    before = rows(target.database_path), files(target.managed_library_root)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (str(archive), ""))
    def confirmation(*args):
        assert "replace the current Question Tracker database" in args[2]
        assert isValid(old_home)
        assert (rows(target.database_path), files(target.managed_library_root)) == before
        return QMessageBox.StandardButton.Yes if confirm else QMessageBox.StandardButton.Cancel
    monkeypatch.setattr(QMessageBox, "question", confirmation)
    monkeypatch.setattr(QMessageBox, "information", lambda *args: None)
    window.import_bundle_action.trigger()
    if confirm:
        assert not isValid(old_home)
        assert isinstance(window.centralWidget(), HomeScreen)
        assert window.tracking_service is not old_service
        assert window.undo_stack.count() == 0
        assert files(target.managed_library_root) == files(source.managed_library_root)
        assert window.tracking_service.list_question_sets() == TrackingService(Database(source.database_path)).list_question_sets()
    else:
        assert window.centralWidget() is old_home
        assert window.undo_stack.count() == 1
        assert (rows(target.database_path), files(target.managed_library_root)) == before
        assert not target.backups_dir.exists()


def test_backup_failure_does_not_prevent_startup(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import question_tracker.app as app

    paths = seed(tmp_path / "live")
    monkeypatch.setattr(app.ApplicationPaths, "default", lambda: paths)
    monkeypatch.setattr(app.QCoreApplication, "setOrganizationName", lambda *args: None)
    monkeypatch.setattr(app.QCoreApplication, "setApplicationName", lambda *args: None)
    monkeypatch.setattr(app, "QApplication", lambda *args: SimpleNamespace(exec=lambda: 0))
    launched = []
    monkeypatch.setattr(app, "MainWindow", lambda *args: SimpleNamespace(show=lambda: launched.append(True)))
    def fail(*args):
        raise PermissionError("backup folder unavailable")
    monkeypatch.setattr(Database, "backup_to", fail)
    assert app.main([]) == 0
    assert launched == [True]


def test_older_bundle_is_migrated_on_staged_copy(tmp_path):
    paths = seed(tmp_path / "live")
    service = BackupService(paths)
    valid = tmp_path / "valid.zip"
    service.export_bundle(valid)
    with zipfile.ZipFile(valid) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        older = tmp_path / "older.sqlite3"
        older.write_bytes(archive.read("database.sqlite3"))
    with closing(sqlite3.connect(older)) as connection:
        connection.execute("ALTER TABLE managed_files DROP COLUMN sha256")
        connection.execute("DROP TABLE module_group_assignments")
        connection.execute("DROP TABLE module_groups")
        connection.execute("DELETE FROM schema_migrations WHERE version>=4")
        connection.commit()
    manifest["schema_version"] = 3
    bundle = tmp_path / "older.zip"
    rewrite_bundle(valid, bundle, manifest=manifest, database=older.read_bytes())
    before = rows(paths.database_path)
    with service.prepare_import(bundle) as staged:
        with closing(sqlite3.connect(staged / "database.sqlite3")) as connection:
            assert connection.execute("SELECT max(version) FROM schema_migrations").fetchone()[0] == 5
            assert "sha256" in {row[1] for row in connection.execute("PRAGMA table_info(managed_files)")}
    assert rows(paths.database_path) == before
