from dataclasses import asdict

import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QUndoStack
from PySide6.QtWidgets import QDialog, QDialogButtonBox

from question_tracker.application import tracker_appearance
from question_tracker.application.tracker_appearance import (
    DEFAULT_APPEARANCE, TrackerAppearance, load_tracker_appearance, save_tracker_appearance,
)
from question_tracker.application.tracker_view import ModuleTrackerSnapshot, build_tracker_snapshot
from question_tracker.domain.tracking import AttemptStatus, QuestionSetKind, SetupState
from question_tracker.ui.tracker import TrackerScreen
from question_tracker.ui.tracker_appearance_dialog import TrackerAppearanceDialog


@pytest.fixture
def store(tmp_path, monkeypatch):
    settings = QSettings(str(tmp_path / "appearance.ini"), QSettings.Format.IniFormat)
    monkeypatch.setattr(tracker_appearance, "_settings", lambda supplied: settings if supplied is None else supplied)
    return settings


def test_dialog_apply_cancel_reset_and_ok(qtbot, store):
    saved = TrackerAppearance(64, 8, 6, 12, 2)
    save_tracker_appearance(saved, store)
    dialog = TrackerAppearanceDialog(settings=store)
    qtbot.addWidget(dialog)
    applied = []
    dialog.appearance_applied.connect(applied.append)
    assert {name: control.value() for name, control in dialog.controls.items()} == asdict(saved)
    dialog.controls["question_box_size"].setValue(80)
    qtbot.mouseClick(dialog.buttons.button(QDialogButtonBox.StandardButton.Apply), Qt.MouseButton.LeftButton)
    assert load_tracker_appearance(store).question_box_size == 80
    assert applied[-1].question_box_size == 80
    dialog.controls["question_box_size"].setValue(90)
    qtbot.mouseClick(dialog.buttons.button(QDialogButtonBox.StandardButton.Cancel), Qt.MouseButton.LeftButton)
    assert load_tracker_appearance(store).question_box_size == 80
    reopened = TrackerAppearanceDialog(settings=QSettings(store.fileName(), QSettings.Format.IniFormat))
    qtbot.addWidget(reopened)
    assert reopened.controls["question_box_size"].value() == 80
    qtbot.mouseClick(reopened.buttons.button(QDialogButtonBox.StandardButton.RestoreDefaults), Qt.MouseButton.LeftButton)
    assert {name: control.value() for name, control in reopened.controls.items()} == asdict(DEFAULT_APPEARANCE)
    assert load_tracker_appearance(store).question_box_size == 80
    qtbot.mouseClick(reopened.buttons.button(QDialogButtonBox.StandardButton.Ok), Qt.MouseButton.LeftButton)
    assert reopened.result() == QDialog.DialogCode.Accepted
    assert load_tracker_appearance(store) == DEFAULT_APPEARANCE


def test_dialog_uses_validated_values_and_cancelled_reset_does_not_save(qtbot, store):
    store.setValue("trackerAppearance/question_box_size", "invalid")
    store.setValue("trackerAppearance/hierarchy_line_thickness", 999)
    dialog = TrackerAppearanceDialog(settings=store)
    qtbot.addWidget(dialog)
    loaded = load_tracker_appearance(store)
    assert {name: control.value() for name, control in dialog.controls.items()} == asdict(loaded)
    for name, control in dialog.controls.items():
        assert (control.minimum(), control.maximum()) == tracker_appearance.tracker_appearance_bounds(name)
    qtbot.mouseClick(dialog.buttons.button(QDialogButtonBox.StandardButton.RestoreDefaults), Qt.MouseButton.LeftButton)
    qtbot.mouseClick(dialog.buttons.button(QDialogButtonBox.StandardButton.Cancel), Qt.MouseButton.LeftButton)
    assert load_tracker_appearance(store) == loaded


def test_apply_from_tracker_preserves_data_selection_module_and_scroll(
    qtbot, qapp, monkeypatch, store, tracking_service, library_service
):
    sets = []
    leaves = []
    for index, count in enumerate((30, 1, 1)):
        question_set = tracking_service.create_question_set(
            QuestionSetKind.PROBLEM_SHEET, f"Sheet {index}", setup_state=SetupState.READY
        )
        sets.append(question_set)
        leaves.append([tracking_service.create_question(question_set.id, str(i + 1)) for i in range(count)])
    tracking_service.add_attempt(leaves[0][0].id, AttemptStatus.PARTIAL, note="Preserve me")
    topic = tracking_service.create_topic(sets[0].id, "Topic", colour="#123456")
    tracking_service.assign_topic(leaves[0][0].id, topic.id)
    before = tuple(build_tracker_snapshot(tracking_service, item.id) for item in sets)
    screen = TrackerScreen(tracking_service, library_service, QUndoStack(), initial_question_set_id=sets[0].id)
    qtbot.addWidget(screen)
    screen.module_snapshot = ModuleTrackerSnapshot(None, before)
    module = screen.module_snapshot
    screen._render_snapshot(module)
    screen.resize(1000, 400)
    screen.show()
    qapp.processEvents()
    selected = leaves[0][5].id
    screen.select_question(selected)
    horizontal = screen.tracker_scroll.horizontalScrollBar()
    vertical = screen.tracker_scroll.verticalScrollBar()
    assert horizontal.maximum() > 0 and vertical.maximum() > 0
    horizontal.setValue(horizontal.maximum() // 2)
    vertical.setValue(vertical.maximum() // 2)
    position = horizontal.value(), vertical.value()
    changed = TrackerAppearance(64, 8, 6, 12, 2)

    def apply_and_close(dialog):
        for name, value in asdict(changed).items():
            dialog.controls[name].setValue(value)
        dialog.apply()
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(TrackerAppearanceDialog, "exec", apply_and_close)
    qtbot.mouseClick(screen.appearance_button, Qt.MouseButton.LeftButton)
    qapp.processEvents()
    assert screen.appearance == changed == load_tracker_appearance(store)
    assert screen.selected_question_id == selected
    assert screen.current_question_set_id == sets[0].id
    assert screen.module_snapshot is module
    assert screen.zoom_percent == 60
    assert horizontal.value() == min(position[0], horizontal.maximum())
    assert vertical.value() == min(position[1], vertical.maximum())
    assert tuple(build_tracker_snapshot(tracking_service, item.id) for item in sets) == before
    reopened = TrackerScreen(tracking_service, library_service, QUndoStack(), initial_question_set_id=sets[0].id)
    qtbot.addWidget(reopened)
    assert reopened.appearance == changed
