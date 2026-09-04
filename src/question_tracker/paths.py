from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ApplicationPaths:
    """Filesystem locations used by Question Tracker."""

    data_dir: Path
    managed_library_root: Path

    @property
    def database_path(self) -> Path:
        return self.data_dir / "question-tracker.db"

    @classmethod
    def default(cls) -> ApplicationPaths:
        # Imported lazily so domain and persistence tests do not require Qt.
        from PySide6.QtCore import QStandardPaths

        data_location = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.AppDataLocation
        )
        documents_location = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.DocumentsLocation
        )
        if not data_location or not documents_location:
            raise RuntimeError("The operating system did not provide application paths")

        return cls(
            data_dir=Path(data_location),
            managed_library_root=Path(documents_location)
            / "Question Tracker"
            / "files",
        )

    @classmethod
    def for_test(cls, base_dir: Path) -> ApplicationPaths:
        return cls(
            data_dir=base_dir / "application-data",
            managed_library_root=base_dir / "files",
        )

    def ensure_created(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.managed_library_root.mkdir(parents=True, exist_ok=True)

