from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from uuid import uuid4

from question_tracker.domain.library import (
    CATEGORY_FOR_DOCUMENT_TYPE,
    DOCUMENT_TYPE_FOR_CATEGORY,
    DocumentType,
    FileAvailability,
    FileRole,
    LibraryModule,
    LibraryScanResult,
    ManagedFile,
    STANDARD_CATEGORY_FOLDERS,
)
from question_tracker.domain.tracking import QuestionSet, QuestionSetKind, SetupState
from question_tracker.persistence.database import Database
from question_tracker.persistence.library_repository import LibraryRepository
from question_tracker.persistence.tracking_repository import TrackingRepository


class LibraryError(RuntimeError):
    """Base error for expected managed-library failures."""


class InvalidFolderNameError(LibraryError):
    pass


class InvalidSourceError(LibraryError):
    pass


class DestinationConflictError(LibraryError):
    pass


class InvalidTargetError(LibraryError):
    pass


class PathEscapeError(LibraryError):
    pass


class AssociationError(LibraryError):
    pass


class ManagedFileNotFoundError(LibraryError):
    pass


@dataclass(frozen=True, slots=True)
class _DiscoveredPdf:
    relative_path: PurePosixPath
    path_key: str
    document_type: DocumentType
    size_bytes: int
    modified_ns: int


class LibraryService:
    """Application API for the managed folder structure and its database records."""

    def __init__(self, database: Database, managed_library_root: Path) -> None:
        self.database = database
        self.root = managed_library_root.resolve(strict=False)

    def create_year(self, year_name: str) -> Path:
        name = _validate_folder_name(year_name)
        self.root.mkdir(parents=True, exist_ok=True)
        destination = self._safe_path(name)
        if destination.exists():
            raise DestinationConflictError(f"Year folder already exists: {name}")
        try:
            destination.mkdir()
        except OSError as error:
            raise LibraryError(f"Could not create year folder: {name}") from error
        return destination

    def create_module(self, year_name: str, module_name: str) -> Path:
        year = _validate_folder_name(year_name)
        module = _validate_folder_name(module_name)
        year_path = self._require_directory(year, description="year")
        destination = self._safe_path(year, module)
        if destination.exists():
            raise DestinationConflictError(
                f"Module folder already exists: {year}/{module}"
            )
        try:
            destination.mkdir()
            for category in STANDARD_CATEGORY_FOLDERS:
                self._safe_path(year, module, category).mkdir()
        except OSError as error:
            raise LibraryError(
                f"Could not create module folder: {year}/{module}"
            ) from error
        return destination

    def scan_library(self) -> LibraryScanResult:
        self.root.mkdir(parents=True, exist_ok=True)
        years, modules, discovered = self._discover_filesystem()
        discovered_by_key = {item.path_key: item for item in discovered}
        if len(discovered_by_key) != len(discovered):
            raise LibraryError(
                "The library contains paths that differ only by letter case"
            )

        now = _time_text()
        new_ids: set[str] = set()
        reappeared_ids: set[str] = set()
        with self.database.transaction() as connection:
            repository = LibraryRepository(connection)
            tracking_repository = TrackingRepository(connection)
            known_files = repository.list_files()
            known_by_key = {_path_key(item.relative_path): item for item in known_files}

            for path_key, item in discovered_by_key.items():
                known = known_by_key.get(path_key)
                if known is None:
                    managed_file = self._insert_new_file(
                        repository, tracking_repository, item, now
                    )
                    new_ids.add(managed_file.id)
                    continue
                if known.availability is FileAvailability.MISSING:
                    reappeared_ids.add(known.id)
                repository.mark_present(
                    known.id,
                    item.document_type,
                    item.size_bytes,
                    item.modified_ns,
                    now,
                )

            for known in known_files:
                if _path_key(known.relative_path) not in discovered_by_key:
                    repository.mark_missing(known.id, now)

            current_files = repository.list_files()

        present = tuple(
            item
            for item in current_files
            if item.availability is FileAvailability.PRESENT
        )
        return LibraryScanResult(
            years=years,
            modules=modules,
            new_files=tuple(item for item in current_files if item.id in new_ids),
            present_files=present,
            missing_files=tuple(
                item
                for item in current_files
                if item.availability is FileAvailability.MISSING
            ),
            reappeared_files=tuple(
                item for item in current_files if item.id in reappeared_ids
            ),
        )

    def refresh(self) -> LibraryScanResult:
        return self.scan_library()

    def import_pdf(
        self,
        source_path: Path,
        year_name: str,
        module_name: str,
        document_type: DocumentType | str,
    ) -> ManagedFile:
        source = _validate_pdf_source(source_path)
        kind = _document_type(document_type)
        year, module, module_path = self._require_module(year_name, module_name)
        category = CATEGORY_FOR_DOCUMENT_TYPE[kind]
        category_path = self._safe_path(year, module, category)
        try:
            category_path.mkdir(exist_ok=True)
        except OSError as error:
            raise LibraryError(f"Could not create category folder: {category}") from error
        destination = self._safe_path(year, module, category, source.name)
        if destination.exists():
            raise DestinationConflictError(
                f"A file named {source.name!r} already exists in {module_path.name}"
            )

        _copy_new_file(source, destination)
        try:
            return self._register_present_file(destination, kind)
        except Exception:
            destination.unlink(missing_ok=True)
            raise

    def replace_file(self, file_id: str, source_path: Path) -> ManagedFile:
        source = _validate_pdf_source(source_path)
        with self.database.read() as connection:
            managed_file = LibraryRepository(connection).get_file(file_id)
        if managed_file is None:
            raise ManagedFileNotFoundError(f"Managed file not found: {file_id}")

        destination = self._path_from_relative(managed_file.relative_path)
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise LibraryError("Could not recreate the managed file folder") from error
        _copy_atomic_replace(source, destination)
        stat = destination.stat()
        now = _time_text()
        with self.database.transaction() as connection:
            repository = LibraryRepository(connection)
            if repository.get_file(file_id) is None:
                raise ManagedFileNotFoundError(f"Managed file not found: {file_id}")
            repository.mark_present(
                file_id,
                managed_file.document_type,
                stat.st_size,
                stat.st_mtime_ns,
                now,
            )
            updated = repository.get_file(file_id)
        assert updated is not None
        return updated

    def get_file(self, file_id: str) -> ManagedFile:
        with self.database.read() as connection:
            managed_file = LibraryRepository(connection).get_file(file_id)
        if managed_file is None:
            raise ManagedFileNotFoundError(f"Managed file not found: {file_id}")
        return managed_file

    def list_files(self) -> tuple[ManagedFile, ...]:
        with self.database.read() as connection:
            return LibraryRepository(connection).list_files()

    def associate_reference(self, question_set_id: str, file_id: str) -> None:
        with self.database.transaction() as connection:
            repository = LibraryRepository(connection)
            tracking_repository = TrackingRepository(connection)
            question_set = tracking_repository.get_question_set(question_set_id)
            managed_file = repository.get_file(file_id)
            if question_set is None:
                raise AssociationError(f"Question set not found: {question_set_id}")
            if managed_file is None:
                raise AssociationError(f"Managed file not found: {file_id}")
            if managed_file.document_type is not DocumentType.SOLUTION:
                raise AssociationError(
                    "Only solution PDFs can be associated as references"
                )
            existing_role = repository.association_role(question_set_id, file_id)
            if existing_role is FileRole.REFERENCE:
                return
            if existing_role is not None:
                raise AssociationError("The file already has another role in this set")
            repository.add_association(
                question_set_id,
                file_id,
                FileRole.REFERENCE,
                repository.next_reference_order(question_set_id),
            )

    def remove_reference(self, question_set_id: str, file_id: str) -> None:
        with self.database.transaction() as connection:
            repository = LibraryRepository(connection)
            role = repository.association_role(question_set_id, file_id)
            if role is None:
                return
            if role is not FileRole.REFERENCE:
                raise AssociationError("The main PDF cannot be removed as a reference")
            repository.remove_association(question_set_id, file_id)

    def associate_main_file(self, question_set_id: str, file_id: str) -> None:
        with self.database.transaction() as connection:
            repository = LibraryRepository(connection)
            tracking_repository = TrackingRepository(connection)
            question_set = tracking_repository.get_question_set(question_set_id)
            managed_file = repository.get_file(file_id)
            if question_set is None:
                raise AssociationError(f"Question set not found: {question_set_id}")
            if managed_file is None:
                raise AssociationError(f"Managed file not found: {file_id}")
            if managed_file.document_type.value != question_set.kind.value:
                raise AssociationError(
                    "Main PDF type must match the question-set type"
                )
            existing_role = repository.association_role(question_set_id, file_id)
            if existing_role is FileRole.MAIN:
                return
            if existing_role is not None:
                raise AssociationError("The file already has another role in this set")
            if repository.file_ids_for_question_set(question_set_id, FileRole.MAIN):
                raise AssociationError("A question set can have at most one main PDF")
            if repository.question_set_ids_for_file(file_id, FileRole.MAIN):
                raise AssociationError("The PDF is already the main file for another set")
            try:
                repository.add_association(
                    question_set_id, file_id, FileRole.MAIN, 0
                )
            except sqlite3.IntegrityError as error:
                raise AssociationError("Could not associate the main PDF") from error

    def files_for_question_set(
        self, question_set_id: str, role: FileRole | None = None
    ) -> tuple[ManagedFile, ...]:
        with self.database.read() as connection:
            library_repository = LibraryRepository(connection)
            if TrackingRepository(connection).get_question_set(question_set_id) is None:
                raise AssociationError(f"Question set not found: {question_set_id}")
            file_ids = library_repository.file_ids_for_question_set(
                question_set_id, role
            )
            return tuple(
                managed_file
                for file_id in file_ids
                if (managed_file := library_repository.get_file(file_id)) is not None
            )

    def question_sets_for_file(
        self, file_id: str, role: FileRole | None = None
    ) -> tuple[QuestionSet, ...]:
        with self.database.read() as connection:
            library_repository = LibraryRepository(connection)
            if library_repository.get_file(file_id) is None:
                raise ManagedFileNotFoundError(f"Managed file not found: {file_id}")
            tracking_repository = TrackingRepository(connection)
            question_set_ids = library_repository.question_set_ids_for_file(
                file_id, role
            )
            return tuple(
                question_set
                for question_set_id in question_set_ids
                if (
                    question_set := tracking_repository.get_question_set(
                        question_set_id
                    )
                )
                is not None
            )

    def _discover_filesystem(
        self,
    ) -> tuple[tuple[str, ...], tuple[LibraryModule, ...], tuple[_DiscoveredPdf, ...]]:
        years: list[str] = []
        modules: list[LibraryModule] = []
        files: list[_DiscoveredPdf] = []
        try:
            year_paths = sorted(
                (path for path in self.root.iterdir() if path.is_dir()),
                key=lambda path: path.name.casefold(),
            )
            for year_path in year_paths:
                self._ensure_inside_root(year_path)
                years.append(year_path.name)
                module_paths = sorted(
                    (path for path in year_path.iterdir() if path.is_dir()),
                    key=lambda path: path.name.casefold(),
                )
                for module_path in module_paths:
                    self._ensure_inside_root(module_path)
                    modules.append(LibraryModule(year_path.name, module_path.name))
                    for category, document_type in DOCUMENT_TYPE_FOR_CATEGORY.items():
                        category_path = module_path / category
                        if not category_path.is_dir():
                            continue
                        self._ensure_inside_root(category_path)
                        pdf_paths = sorted(
                            (
                                path
                                for path in category_path.rglob("*")
                                if path.is_file() and path.suffix.casefold() == ".pdf"
                            ),
                            key=lambda path: path.relative_to(category_path)
                            .as_posix()
                            .casefold(),
                        )
                        for pdf_path in pdf_paths:
                            self._ensure_inside_root(pdf_path)
                            relative = PurePosixPath(
                                pdf_path.relative_to(self.root).as_posix()
                            )
                            stat = pdf_path.stat()
                            files.append(
                                _DiscoveredPdf(
                                    relative_path=relative,
                                    path_key=_path_key(relative),
                                    document_type=document_type,
                                    size_bytes=stat.st_size,
                                    modified_ns=stat.st_mtime_ns,
                                )
                            )
        except OSError as error:
            raise LibraryError("Could not scan the managed library") from error
        return tuple(years), tuple(modules), tuple(files)

    def _register_present_file(
        self, path: Path, document_type: DocumentType
    ) -> ManagedFile:
        self._ensure_inside_root(path)
        relative = PurePosixPath(path.relative_to(self.root).as_posix())
        stat = path.stat()
        discovered = _DiscoveredPdf(
            relative_path=relative,
            path_key=_path_key(relative),
            document_type=document_type,
            size_bytes=stat.st_size,
            modified_ns=stat.st_mtime_ns,
        )
        now = _time_text()
        with self.database.transaction() as connection:
            repository = LibraryRepository(connection)
            existing = repository.get_file_by_path_key(discovered.path_key)
            if existing is None:
                return self._insert_new_file(
                    repository, TrackingRepository(connection), discovered, now
                )
            repository.mark_present(
                existing.id,
                document_type,
                stat.st_size,
                stat.st_mtime_ns,
                now,
            )
            updated = repository.get_file(existing.id)
            assert updated is not None
            return updated

    def _insert_new_file(
        self,
        repository: LibraryRepository,
        tracking_repository: TrackingRepository,
        discovered: _DiscoveredPdf,
        now: str,
    ) -> ManagedFile:
        managed_file = ManagedFile(
            id=str(uuid4()),
            relative_path=discovered.relative_path,
            document_type=discovered.document_type,
            availability=FileAvailability.PRESENT,
            size_bytes=discovered.size_bytes,
            modified_ns=discovered.modified_ns,
        )
        repository.insert_file(managed_file, discovered.path_key, now)
        if discovered.document_type is not DocumentType.SOLUTION:
            question_set = QuestionSet(
                id=str(uuid4()),
                kind=QuestionSetKind(discovered.document_type.value),
                display_name=Path(discovered.relative_path.name).stem,
                setup_state=SetupState.NEEDS_SETUP,
                general_note="",
            )
            tracking_repository.insert_question_set(question_set, now)
            repository.add_association(
                question_set.id, managed_file.id, FileRole.MAIN, 0
            )
        return managed_file

    def _require_module(
        self, year_name: str, module_name: str
    ) -> tuple[str, str, Path]:
        year = _validate_folder_name(year_name)
        module = _validate_folder_name(module_name)
        self._require_directory(year, description="year")
        module_path = self._require_directory(year, module, description="module")
        return year, module, module_path

    def _require_directory(self, *parts: str, description: str) -> Path:
        path = self._safe_path(*parts)
        if not path.is_dir():
            raise InvalidTargetError(
                f"Target {description} folder does not exist: {'/'.join(parts)}"
            )
        return path

    def _safe_path(self, *parts: str) -> Path:
        candidate = self.root.joinpath(*parts)
        self._ensure_inside_root(candidate)
        return candidate

    def _path_from_relative(self, relative_path: PurePosixPath) -> Path:
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise PathEscapeError("Stored path escapes the managed library root")
        return self._safe_path(*relative_path.parts)

    def _ensure_inside_root(self, path: Path) -> None:
        resolved = path.resolve(strict=False)
        if not resolved.is_relative_to(self.root):
            raise PathEscapeError("Path escapes the managed library root")


def _validate_folder_name(value: str) -> str:
    if not isinstance(value, str):
        raise InvalidFolderNameError("Folder name must be text")
    if value != value.strip() or not value:
        raise InvalidFolderNameError("Folder name cannot be empty or padded")
    if value in {".", ".."} or any(character in value for character in '<>:"/\\|?*\0'):
        raise InvalidFolderNameError(f"Invalid folder name: {value!r}")
    if value.endswith((".", " ")):
        raise InvalidFolderNameError("Folder name cannot end with a dot or space")
    reserved = {"CON", "PRN", "AUX", "NUL"}
    reserved.update(f"COM{number}" for number in range(1, 10))
    reserved.update(f"LPT{number}" for number in range(1, 10))
    if value.split(".", 1)[0].upper() in reserved:
        raise InvalidFolderNameError(f"Reserved folder name: {value!r}")
    return value


def _validate_pdf_source(source_path: Path) -> Path:
    source = Path(source_path)
    if not source.exists() or not source.is_file():
        raise InvalidSourceError(f"Source file does not exist: {source}")
    if source.suffix.casefold() != ".pdf":
        raise InvalidSourceError("Source file must have a .pdf extension")
    try:
        with source.open("rb") as stream:
            header = stream.read(1024)
    except OSError as error:
        raise InvalidSourceError(f"Could not read source PDF: {source}") from error
    if b"%PDF-" not in header:
        raise InvalidSourceError("Source file does not contain a PDF header")
    return source


def _document_type(value: DocumentType | str) -> DocumentType:
    try:
        return DocumentType(value)
    except ValueError as error:
        raise InvalidTargetError(f"Invalid document type: {value}") from error


def _copy_new_file(source: Path, destination: Path) -> None:
    try:
        with source.open("rb") as source_stream, destination.open("xb") as target_stream:
            shutil.copyfileobj(source_stream, target_stream)
            target_stream.flush()
            os.fsync(target_stream.fileno())
    except FileExistsError as error:
        raise DestinationConflictError(
            f"Destination file already exists: {destination.name}"
        ) from error
    except OSError as error:
        destination.unlink(missing_ok=True)
        raise LibraryError(f"Could not copy PDF: {source.name}") from error


def _copy_atomic_replace(source: Path, destination: Path) -> None:
    temporary_path: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".question-tracker-", suffix=".tmp", dir=destination.parent
        )
        temporary_path = Path(temporary_name)
        with source.open("rb") as source_stream, os.fdopen(
            descriptor, "wb"
        ) as target_stream:
            shutil.copyfileobj(source_stream, target_stream)
            target_stream.flush()
            os.fsync(target_stream.fileno())
        os.replace(temporary_path, destination)
        temporary_path = None
    except OSError as error:
        raise LibraryError(f"Could not replace PDF: {destination.name}") from error
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _path_key(relative_path: PurePosixPath) -> str:
    return relative_path.as_posix().casefold()


def _time_text() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
