"""Editor for the five persisted main-tracker appearance settings."""

from PySide6.QtCore import QSettings, Signal
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QLabel, QSpinBox, QVBoxLayout, QWidget

from question_tracker.application.tracker_appearance import (
    DEFAULT_APPEARANCE,
    TrackerAppearance,
    load_tracker_appearance,
    save_tracker_appearance,
    tracker_appearance_bounds,
)


class TrackerAppearanceDialog(QDialog):
    appearance_applied = Signal(object)

    def __init__(self, parent: QWidget | None = None, *, settings: QSettings | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Tracker Appearance")
        self._settings = settings
        self.controls: dict[str, QSpinBox] = {}
        form = QFormLayout()
        for name, label in (
            ("question_box_size", "Question box size"),
            ("horizontal_question_spacing", "Horizontal question spacing"),
            ("attempt_stack_offset", "Attempt stack offset"),
            ("hierarchy_heading_text_size", "Hierarchy heading text size"),
            ("hierarchy_line_thickness", "Hierarchy line thickness"),
        ):
            control = QSpinBox()
            control.setObjectName(name)
            control.setRange(*tracker_appearance_bounds(name))
            control.setSuffix(" px")
            self.controls[name] = control
            form.addRow(label, control)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Apply
            | QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.RestoreDefaults
        )
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)
        self.buttons.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(self.apply)
        self.buttons.button(QDialogButtonBox.StandardButton.RestoreDefaults).setText("Reset to Defaults")
        self.buttons.button(QDialogButtonBox.StandardButton.RestoreDefaults).clicked.connect(
            lambda: self._populate(DEFAULT_APPEARANCE)
        )
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        hint = QLabel("Changes, including Reset to Defaults, take effect with Apply or OK.")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addWidget(self.buttons)
        self._populate(load_tracker_appearance(settings))

    def _populate(self, appearance: TrackerAppearance) -> None:
        for name, control in self.controls.items():
            control.setValue(getattr(appearance, name))

    def apply(self) -> None:
        appearance = TrackerAppearance(**{name: control.value() for name, control in self.controls.items()})
        save_tracker_appearance(appearance, self._settings)
        appearance = load_tracker_appearance(self._settings)
        self._populate(appearance)
        self.appearance_applied.emit(appearance)

    def _accept(self) -> None:
        self.apply()
        self.accept()
