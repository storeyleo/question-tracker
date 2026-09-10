"""Opt-in spacing for Home, the question editor and the marking workspace."""

from PySide6.QtWidgets import QLayout

PAGE_MARGIN = 8
SECTION_SPACING = 6
CONTROL_SPACING = 4


def compact_layout(layout: QLayout, *, margin: int = 0) -> None:
    layout.setContentsMargins(margin, margin, margin, margin)
    layout.setSpacing(CONTROL_SPACING)
