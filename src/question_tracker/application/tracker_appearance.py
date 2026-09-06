"""Tracker-only appearance values and local QSettings persistence."""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PySide6.QtCore import QSettings


@dataclass(frozen=True, slots=True)
class TrackerAppearance:
    question_box_size: int = 50
    horizontal_question_spacing: int = 4
    attempt_stack_offset: int = 4
    hierarchy_heading_text_size: int = 10
    hierarchy_line_thickness: int = 1


DEFAULT_APPEARANCE = TrackerAppearance()

# Safety bounds for persisted input, not additional appearance settings.
_BOUNDS = {
    "question_box_size": (1, 256),
    "horizontal_question_spacing": (0, 128),
    "attempt_stack_offset": (0, 64),
    "hierarchy_heading_text_size": (1, 96),
    "hierarchy_line_thickness": (1, 16),
}
_PREFIX = "trackerAppearance/"


def _validated(name: str, value: object) -> int:
    default = getattr(DEFAULT_APPEARANCE, name)
    # QSettings may return either integer values or strings from an INI file.
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return default
    try:
        number = int(value)
    except (ValueError, OverflowError):
        return default
    minimum, maximum = _BOUNDS[name]
    return max(minimum, min(maximum, number))


def _settings(settings: QSettings | None) -> QSettings:
    if settings is not None:
        return settings
    from PySide6.QtCore import QSettings

    return QSettings("Question Tracker", "Question Tracker")


def load_tracker_appearance(settings: QSettings | None = None) -> TrackerAppearance:
    settings = _settings(settings)
    return TrackerAppearance(**{
        field.name: _validated(
            field.name,
            settings.value(_PREFIX + field.name, getattr(DEFAULT_APPEARANCE, field.name)),
        )
        for field in fields(TrackerAppearance)
    })


def save_tracker_appearance(
    appearance: TrackerAppearance, settings: QSettings | None = None
) -> None:
    settings = _settings(settings)
    for field in fields(TrackerAppearance):
        settings.setValue(
            _PREFIX + field.name, _validated(field.name, getattr(appearance, field.name))
        )
    settings.sync()


def reset_tracker_appearance(settings: QSettings | None = None) -> TrackerAppearance:
    save_tracker_appearance(DEFAULT_APPEARANCE, settings)
    return DEFAULT_APPEARANCE
