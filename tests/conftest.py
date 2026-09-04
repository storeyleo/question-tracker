from __future__ import annotations

from pathlib import Path

import pytest

from question_tracker.paths import ApplicationPaths
from question_tracker.application.tracking_service import TrackingService
from question_tracker.persistence.database import Database


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
