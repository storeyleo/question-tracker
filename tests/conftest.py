from __future__ import annotations

from pathlib import Path

import pytest

from question_tracker.paths import ApplicationPaths
from question_tracker.application.tracking_service import TrackingService
from question_tracker.application.library_service import LibraryService
from question_tracker.persistence.database import Database


@pytest.fixture(autouse=True)
def isolated_ui_preferences(request, monkeypatch, tmp_path):
    if "qtbot" in request.fixturenames:
        from PySide6.QtCore import QSettings
        from question_tracker.ui import preferences

        monkeypatch.setattr(
            preferences, "default_settings",
            lambda: QSettings(str(tmp_path / "ui.ini"), QSettings.Format.IniFormat),
        )


def _fixture_pdf_bytes(page_texts: tuple[str, ...]) -> bytes:
    """Build a tiny standards-compliant PDF without adding a test dependency."""
    page_count = len(page_texts)
    font_id = 3 + page_count * 2
    page_ids = [3 + index * 2 for index in range(page_count)]
    objects: dict[int, bytes] = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: (
            f"<< /Type /Pages /Kids [{' '.join(f'{item} 0 R' for item in page_ids)}] "
            f"/Count {page_count} >>"
        ).encode("ascii"),
        font_id: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    }
    for index, (page_id, text) in enumerate(zip(page_ids, page_texts)):
        content_id = page_id + 1
        escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream = f"BT /F1 18 Tf 72 720 Td ({escaped}) Tj ET\n".encode("ascii")
        objects[page_id] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> "
            f"/Contents {content_id} 0 R >>"
        ).encode("ascii")
        objects[content_id] = (
            f"<< /Length {len(stream)} >>\nstream\n".encode("ascii")
            + stream
            + b"endstream"
        )

    payload = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for object_id in range(1, font_id + 1):
        offsets.append(len(payload))
        payload.extend(f"{object_id} 0 obj\n".encode("ascii"))
        payload.extend(objects[object_id])
        payload.extend(b"\nendobj\n")
    xref_offset = len(payload)
    payload.extend(f"xref\n0 {font_id + 1}\n".encode("ascii"))
    payload.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        payload.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    payload.extend(
        (
            f"trailer\n<< /Size {font_id + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    return bytes(payload)


@pytest.fixture
def application_paths(tmp_path: Path) -> ApplicationPaths:
    return ApplicationPaths.for_test(tmp_path)


@pytest.fixture
def database(application_paths: ApplicationPaths) -> Database:
    database = Database(application_paths.database_path)
    database.initialize()
    return database


@pytest.fixture
def tracking_service(database: Database) -> TrackingService:
    return TrackingService(database)


@pytest.fixture
def library_service(
    database: Database, application_paths: ApplicationPaths
) -> LibraryService:
    application_paths.ensure_created()
    return LibraryService(database, application_paths.managed_library_root)


@pytest.fixture
def pdf_factory():
    def write_pdf(
        path: Path, page_texts: tuple[str, ...] = ("Question Tracker fixture",)
    ) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_fixture_pdf_bytes(page_texts))
        return path

    return write_pdf
