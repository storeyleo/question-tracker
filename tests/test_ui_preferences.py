from pathlib import PurePosixPath

import pytest
from PySide6.QtCore import QByteArray, QCoreApplication, QEvent, QPointF, QSettings, Qt
from PySide6.QtGui import QMouseEvent, QUndoStack
from PySide6.QtWidgets import QApplication, QSplitter, QWidget

from question_tracker.app import MainWindow
from question_tracker.domain.tracking import AttemptStatus, QuestionSetKind, SetupState
from question_tracker.ui.marking import MarkingWorkspace
from question_tracker.ui.navigation import Destination, NavigationHistory
from question_tracker.ui.pdf_pane import ManagedPdfBrowser
from question_tracker.ui.preferences import UiPreferences
from question_tracker.ui.tracker import TrackerScreen


@pytest.fixture
def preferences(tmp_path):
    return UiPreferences(QSettings(str(tmp_path / "prefs.ini"), QSettings.Format.IniFormat))


@pytest.mark.parametrize("value,expected", [(None, 60), ("bad", 60), (10, 50), (999, 160), (90, 90)])
def test_zoom_defaults_and_clamping(preferences, value, expected):
    if value is not None:
        preferences.settings.setValue("ui/v1/tracker/zoom", value)
    assert preferences.tracker_zoom() == expected


def test_zoom_round_trip_and_tracker_restore(qtbot, preferences, tracking_service, library_service):
    preferences.save_tracker_zoom(90)
    preferences.settings.sync()
    reloaded = UiPreferences(QSettings(preferences.settings.fileName(), QSettings.Format.IniFormat))
    tracker = TrackerScreen(tracking_service, library_service, QUndoStack(), preferences=reloaded)
    qtbot.addWidget(tracker)
    assert tracker.zoom_percent == 90
    tracker.change_zoom(10)
    assert preferences.tracker_zoom() == 100


def make_splitter(qtbot):
    splitter = QSplitter(Qt.Orientation.Horizontal)
    qtbot.addWidget(splitter)
    for _ in range(2):
        panel = QWidget()
        panel.setMinimumSize(30, 30)
        splitter.addWidget(panel)
    splitter.resize(600, 200)
    splitter.show()
    return splitter


@pytest.mark.parametrize("state", [None, "old value", QByteArray(b"invalid")])
def test_splitter_missing_invalid_state(qtbot, preferences, state):
    preferences.settings.setValue("ui/v1/splitters/test", state)
    splitter = make_splitter(qtbot)
    preferences.bind_splitter(splitter, "test", (400, 200))
    assert all(size > 0 for size in splitter.sizes())
    assert not splitter.childrenCollapsible()


def test_splitter_round_trip_and_collapsed_state(qtbot, preferences):
    first = make_splitter(qtbot)
    preferences.bind_splitter(first, "test", (400, 200))
    first.setSizes((200, 400))
    first.splitterMoved.emit(200, 1)
    second = make_splitter(qtbot)
    preferences.bind_splitter(second, "test", (400, 200))
    assert second.sizes() == first.sizes()
    first.setChildrenCollapsible(True)
    first.setSizes((0, 600))
    preferences.settings.setValue("ui/v1/splitters/test", first.saveState())
    preferences.bind_splitter(second, "test", (400, 200))
    assert all(size > 0 for size in second.sizes())


def test_expand_all_session_rebuild_includes_new_descendants(qtbot, library_service):
    roots = set()
    browser = ManagedPdfBrowser(library_service, expanded_roots=roots)
    qtbot.addWidget(browser)
    browser.populate(folders=[PurePosixPath("Year 2/Analysis")], years=["Year 3"])
    browser._item_clicked(browser.folder_item(("Year 2",)), 1)
    for target in (browser, ManagedPdfBrowser(library_service, expanded_roots=roots)):
        if target is not browser:
            qtbot.addWidget(target)
        target.populate(folders=[PurePosixPath("Year 2/Analysis/New/Deep")], years=["Year 3"])
        assert target.folder_item(("Year 2", "Analysis", "New")).isExpanded()
        assert not target.folder_item(("Year 3",)).isExpanded()
    fresh = ManagedPdfBrowser(library_service)
    qtbot.addWidget(fresh)
    fresh.populate(years=["Year 2"])
    assert not fresh.folder_item(("Year 2",)).isExpanded()


def test_history_back_forward_and_new_branch():
    history = NavigationHistory()
    home, tracker, review = (Destination(name) for name in ("home", "tracker", "review"))
    assert history.move(-1) is None
    history.visit(home)
    history.visit(tracker)
    history.visit(tracker)
    assert history.move(-1) == home
    assert history.move(-1) is None
    assert history.move(1) == tracker
    assert history.move(1) is None
    history.move(-1)
    history.visit(review)
    assert history.move(1) is None
    assert history.entries == [home, review]


def test_window_history_reuses_screen_navigation(qtbot, application_paths, tracking_service, library_service):
    window = MainWindow(application_paths, tracking_service, library_service)
    qtbot.addWidget(window)
    window.show_review()
    window.navigate(-1)
    assert window.history.current.screen == "home"
    window.navigate(1)
    assert window.history.current.screen == "review"
    assert window.centralWidget().filter_combo is not None


@pytest.mark.parametrize("consumed", [False, True])
def test_mouse_navigation_respects_child_consumption(
    qtbot, application_paths, tracking_service, library_service, monkeypatch, consumed,
):
    window = MainWindow(application_paths, tracking_service, library_service)
    qtbot.addWidget(window)
    navigated = []
    monkeypatch.setattr(window, "navigate", navigated.append)

    class Child(QWidget):
        def mouseReleaseEvent(self, event):
            event.setAccepted(consumed)

    child = Child(window)
    window.setCentralWidget(child)
    for button in (Qt.MouseButton.BackButton, Qt.MouseButton.ForwardButton, Qt.MouseButton.MiddleButton):
        event = QMouseEvent(
            QEvent.Type.MouseButtonRelease, QPointF(10, 10), QPointF(10, 10),
            button, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
        )
        QApplication.sendEvent(child, event)
    assert navigated == ([] if consumed else [-1, 1])


def test_marking_history_captures_context_and_preserves_explicit_return(
    qtbot, application_paths, tracking_service, library_service,
):
    question_set = tracking_service.create_question_set(
        QuestionSetKind.PROBLEM_SHEET, "Navigation", setup_state=SetupState.READY,
    )
    leaves = [tracking_service.create_question(question_set.id, str(i)) for i in (1, 2)]
    window = MainWindow(application_paths, tracking_service, library_service)
    qtbot.addWidget(window)
    window.show_marking()
    assert window.centralWidget().active_question_set_id is None
    assert window.centralWidget().selected_question_id is None
    window.show_home()
    window.navigate(-1)
    assert isinstance(window.centralWidget(), MarkingWorkspace)
    assert window.centralWidget().active_question_set_id is None
    window.navigate(1)

    returned = []
    window.show_marking(question_set.id, leaves[0].id, on_back=lambda: returned.append(True))
    window.centralWidget().compact_tracker.select_question(leaves[1].id)
    window.centralWidget().compact_tracker.mark_selected(AttemptStatus.CORRECT)
    window.show_home()
    window.navigate(-1)
    workspace = window.centralWidget()
    assert workspace.active_question_set_id == question_set.id
    assert workspace.selected_question_id == leaves[1].id
    from PySide6.QtWidgets import QPushButton
    workspace.findChild(QPushButton, "markingBackButton").click()
    assert returned == [True]
    assert len(tracking_service.attempts_for_question(leaves[1].id)) == 1
    window.navigate(1)
    window.navigate(-1)
    assert window.centralWidget().selected_question_id == leaves[1].id
    window.centralWidget().compact_tracker.active_selector.setCurrentIndex(0)
    window.show_home()
    window.navigate(-1)
    assert window.centralWidget().active_question_set_id is None
    assert window.centralWidget().selected_question_id is None


@pytest.mark.parametrize("screen", ["tracker", "marking"])
@pytest.mark.parametrize("replace", [False, True])
@pytest.mark.parametrize("with_history", [False, True])
def test_window_destruction_and_screen_replacement_disconnect_undo_refresh(
    qtbot, request, monkeypatch, application_paths, tracking_service, library_service,
    screen, replace, with_history,
):
    from shiboken6 import isValid

    question_set = tracking_service.create_question_set(
        QuestionSetKind.PROBLEM_SHEET, "Lifetime", setup_state=SetupState.READY,
    )
    question = tracking_service.create_question(question_set.id, "1")
    window = MainWindow(application_paths, tracking_service, library_service)

    def destroy_window():
        if isValid(window):
            window.close()
            window.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    # This test owns explicit destruction. Registering with qtbot as well
    # would make its teardown call close() on an already-deleted C++ object.
    # The finalizer also cleans up if an assertion fails before destruction.
    request.addfinalizer(destroy_window)
    window.show()
    getattr(window, f"show_{screen}")(question_set.id)
    target = window.centralWidget()
    tracker = target.compact_tracker if screen == "marking" else target
    stack = window.undo_stack
    refresh_name = "refresh" if screen == "marking" else "refresh_current"
    original_refresh = getattr(tracker, refresh_name)
    refreshed = []

    def refresh():
        assert isValid(stack)
        refreshed.append(True)
        original_refresh()

    monkeypatch.setattr(tracker, refresh_name, refresh)
    if with_history:
        tracker.mark_selected(AttemptStatus.CORRECT)
        assert refreshed
    else:
        assert stack.count() == 0
    if replace:
        window.show_home()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        assert not isValid(target)
        count = len(refreshed)
        stack.undo()
        stack.redo()
        if not with_history:
            # An empty stack cannot change index through undo/redo. Exercise
            # all connected notifications to check receiver disconnection.
            stack.indexChanged.emit(stack.index())
            stack.canUndoChanged.emit(stack.canUndo())
            stack.canRedoChanged.emit(stack.canRedo())
        assert len(refreshed) == count
    count = len(refreshed)
    destroy_window()
    assert not isValid(window)
    assert not isValid(stack)
    assert len(refreshed) == count
    assert len(tracking_service.attempts_for_question(question.id)) == int(with_history)
