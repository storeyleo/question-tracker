from __future__ import annotations

from pathlib import Path

import pytest

from question_tracker.paths import ApplicationPaths


@pytest.fixture
def application_paths(tmp_path: Path) -> ApplicationPaths:
    return ApplicationPaths.for_test(tmp_path)

