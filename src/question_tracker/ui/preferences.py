"""Machine-local UI preferences; no library or tracking data belongs here."""
from PySide6.QtCore import QByteArray, QSettings
from PySide6.QtWidgets import QSplitter

MIN_ZOOM_PERCENT = 50
MAX_ZOOM_PERCENT = 160
DEFAULT_ZOOM_PERCENT = 60


def default_settings() -> QSettings:
    return QSettings("Question Tracker", "Question Tracker")


class UiPreferences:
    def __init__(self, settings: QSettings | None = None) -> None:
        self.settings = settings if settings is not None else default_settings()

    def tracker_zoom(self) -> int:
        try:
            value = int(self.settings.value("ui/v1/tracker/zoom", DEFAULT_ZOOM_PERCENT))
        except (TypeError, ValueError, OverflowError):
            value = DEFAULT_ZOOM_PERCENT
        return max(MIN_ZOOM_PERCENT, min(MAX_ZOOM_PERCENT, value))

    def save_tracker_zoom(self, value: int) -> None:
        value = max(MIN_ZOOM_PERCENT, min(MAX_ZOOM_PERCENT, value))
        self.settings.setValue("ui/v1/tracker/zoom", value)

    def bind_splitter(self, splitter: QSplitter, name: str, defaults: tuple[int, ...]) -> None:
        key = f"ui/v1/splitters/{name}"
        state = self.settings.value(key)
        orientation = splitter.orientation()
        handle_width = splitter.handleWidth()
        restored = isinstance(state, QByteArray) and splitter.restoreState(state)
        splitter.setOrientation(orientation)
        splitter.setHandleWidth(max(1, handle_width))
        # Old states may have collapsed children or enabled collapsing.
        splitter.setChildrenCollapsible(False)
        for index in range(splitter.count()):
            splitter.setCollapsible(index, False)
        if not restored or len(splitter.sizes()) != len(defaults) or any(
            size <= 0 for size in splitter.sizes()
        ):
            splitter.setSizes(defaults)
        splitter.splitterMoved.connect(
            lambda *_: self.settings.setValue(key, splitter.saveState())
        )
