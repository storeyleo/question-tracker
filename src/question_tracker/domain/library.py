from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import PurePosixPath


class DocumentType(str, Enum):
    PROBLEM_SHEET = "problem_sheet"
    SOLUTION = "solution"
    PAST_PAPER = "past_paper"


class FileAvailability(str, Enum):
    PRESENT = "present"
    MISSING = "missing"


class FileRole(str, Enum):
    MAIN = "main"
    REFERENCE = "reference"


CATEGORY_FOR_DOCUMENT_TYPE = {
    DocumentType.PROBLEM_SHEET: "Problem Sheets",
    DocumentType.SOLUTION: "Problem Sheet Solutions",
    DocumentType.PAST_PAPER: "Past Papers",
}

DOCUMENT_TYPE_FOR_CATEGORY = {
    category: document_type
    for document_type, category in CATEGORY_FOR_DOCUMENT_TYPE.items()
}

STANDARD_CATEGORY_FOLDERS = tuple(DOCUMENT_TYPE_FOR_CATEGORY)


@dataclass(frozen=True, slots=True)
class ManagedFile:
    id: str
    relative_path: PurePosixPath
    document_type: DocumentType
    availability: FileAvailability
    size_bytes: int | None
    modified_ns: int | None


@dataclass(frozen=True, slots=True)
class LibraryModule:
    year_name: str
    module_name: str


@dataclass(frozen=True, slots=True)
class LibraryScanResult:
    years: tuple[str, ...]
    modules: tuple[LibraryModule, ...]
    new_files: tuple[ManagedFile, ...]
    present_files: tuple[ManagedFile, ...]
    missing_files: tuple[ManagedFile, ...]
    reappeared_files: tuple[ManagedFile, ...]
