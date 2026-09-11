from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QDropEvent
from PySide6.QtWidgets import QInputDialog, QLabel, QMessageBox, QPushButton, QSplitter, QWidget

from question_tracker.application.module_group_service import ModuleGroupService
from question_tracker.domain.library import LibraryModule
from question_tracker.ui.flow_layout import FlowLayout
from question_tracker.ui.home import HomeScreen
from question_tracker.ui.module_organisation import module_mime


def test_flow_wraps_without_squeezing_cards(qtbot):
    parent = QWidget()
    qtbot.addWidget(parent)
    flow = FlowLayout(parent)
    cards = [QPushButton(str(i)) for i in range(4)]
    for card in cards:
        card.setFixedSize(270, 90)
        flow.addWidget(card)
    parent.show()
    parent.resize(1200, 400)
    qtbot.waitUntil(lambda: len({card.y() for card in cards}) == 1)
    parent.resize(580, 400)
    qtbot.waitUntil(lambda: len({card.y() for card in cards}) == 2)
    assert flow.heightForWidth(580) > flow.heightForWidth(1200)
    assert all(card.width() >= 270 for card in cards)
    parent.resize(1200, 400)
    qtbot.waitUntil(lambda: len({card.y() for card in cards}) == 1)


def test_home_years_groups_and_real_drop_path(qtbot, monkeypatch, tracking_service, library_service, application_paths):
    for year in ("Year 1", "Year 2"):
        library_service.create_year(year)
        for module in ("Analysis", "Physics"):
            library_service.create_module(year, module)
    service = ModuleGroupService(tracking_service.database)
    group = service.create("Year 2", "Maths")
    second = service.create("Year 2", "Optional")
    home = HomeScreen(tracking_service, application_paths, library_service=library_service)
    qtbot.addWidget(home)
    home.resize(1200, 900)
    home.show()
    assert {label.text() for label in home.findChildren(QLabel, "moduleYearHeading")} == {"Year 1", "Year 2"}
    module = LibraryModule("Year 2", "Analysis")
    for target_id in (group.id, second.id, None, group.id):
        area = home.module_sections[("Year 2", target_id)]
        mime = module_mime(module)
        assert area.accepts(mime)
        assert not home.module_sections[("Year 1", None)].accepts(mime)
        event = QDropEvent(QPointF(5, 5), Qt.DropAction.MoveAction, mime,
                           Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        area.dropEvent(event)
        assert event.isAccepted()
        qtbot.waitUntil(lambda: home.module_sections[("Year 2", target_id)] is not area)
        assert home.module_sections[("Year 2", target_id)].isAncestorOf(home.module_cards[("Year 2", "Analysis")])
        assert home.module_sections[("Year 2", None)].isAncestorOf(home.module_cards[("Year 2", "Physics")])
    home.refresh()
    assert home.module_sections[("Year 2", group.id)].isAncestorOf(home.module_cards[("Year 2", "Analysis")])
    assert home.library_browser.folder_item(("Year 2", "Maths")) is None
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    home._delete_module_group(group)
    qtbot.waitUntil(lambda: ("Year 2", group.id) not in home.module_sections)
    assert len(home.module_cards) == 4
    assert home.module_sections[("Year 2", None)].isAncestorOf(home.module_cards[("Year 2", "Analysis")])


def test_home_group_dialogs_and_splitter_restore(qtbot, monkeypatch, tracking_service, library_service, application_paths):
    library_service.create_year("Year 2")
    for name in ("A", "B", "C", "D"):
        library_service.create_module("Year 2", name)
    home = HomeScreen(tracking_service, application_paths, library_service=library_service)
    qtbot.addWidget(home)
    home.resize(1200, 900)
    home.show()
    monkeypatch.setattr(QInputDialog, "getText", lambda *args, **kwargs: ("  Maths  ", True))
    home._create_module_group("Year 2")
    groups, _ = home.module_groups.snapshot()
    group = groups[0]
    assert group.name == "Maths"
    qtbot.waitUntil(lambda: ("Year 2", group.id) in home.module_sections)
    monkeypatch.setattr(QInputDialog, "getText", lambda *args, **kwargs: ("Pure Maths", True))
    home._rename_module_group(group)
    assert home.module_groups.snapshot()[0][0].name == "Pure Maths"
    qtbot.waitUntil(lambda: any(label.text() == "Pure Maths" for label in home.findChildren(QLabel)))
    splitter = home.findChild(QSplitter, "homePanelsSplitter")
    splitter.setSizes((400, 300))
    splitter.splitterMoved.emit(splitter.sizes()[0], 1)
    saved = home.preferences.settings.value("ui/v1/splitters/home/panels")
    other = HomeScreen(tracking_service, application_paths, library_service=library_service, preferences=home.preferences)
    qtbot.addWidget(other)
    other.resize(900, 900)
    other.show()
    restored = other.findChild(QSplitter, "homePanelsSplitter")
    assert not restored.childrenCollapsible()
    assert all(size > 0 for size in restored.sizes())
    assert home.preferences.settings.value("ui/v1/splitters/home/panels") == saved
    assert other.module_scroll.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    wrappers = [card.parentWidget() for card in other.module_cards.values()]
    qtbot.waitUntil(lambda: len({wrapper.y() for wrapper in wrappers}) > 1)
    assert all(wrapper.width() >= 270 for wrapper in wrappers)


def test_empty_year_remains_visible_under_other(qtbot, tracking_service, library_service, application_paths):
    from question_tracker.application.home_view import HomeSnapshot, ModuleSummary, CategorySummary
    home = HomeScreen(tracking_service, application_paths, library_service=library_service)
    qtbot.addWidget(home)
    empty = CategorySummary(0, 0, 0, 0)
    summary = ModuleSummary(LibraryModule("", "Unclassified"), empty, empty, None)
    home.snapshot = HomeSnapshot((), (summary,), ())
    home._populate_module_cards()
    assert ("", "Unclassified") in home.module_cards
    assert "Other" in {label.text() for label in home.findChildren(QLabel, "moduleYearHeading")}
