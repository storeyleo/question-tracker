from datetime import UTC, datetime, timedelta

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QPushButton

from question_tracker.app import MainWindow
from question_tracker.application.review_service import ReviewFilter, ReviewService
from question_tracker.domain.library import DocumentType, FileRole, LibraryModule
from question_tracker.domain.tracking import AttemptStatus, QuestionSetKind, SetupState, latest_attempt
from question_tracker.ui.marking import MarkingWorkspace
from question_tracker.ui.review import ReviewScreen
from question_tracker.ui.tracker import TrackerScreen


@pytest.fixture
def review_data(tracking_service, library_service, application_paths, pdf_factory):
    library_service.create_year("Year 2")
    library_service.create_module("Year 2", "Algebra")
    sets = []
    for name, kind in (("Sheet", DocumentType.PROBLEM_SHEET), ("Paper", DocumentType.PAST_PAPER), ("Setup", DocumentType.PROBLEM_SHEET)):
        source = pdf_factory(application_paths.data_dir / f"{name}.pdf")
        file = library_service.import_pdf(source, "Year 2", "Algebra", kind)
        sets.append(library_service.question_sets_for_file(file.id, FileRole.MAIN)[0])
    primary, paper, setup = sets
    root = tracking_service.create_question(primary.id, "1")
    first = tracking_service.add_child(root.id, "a")
    branch = tracking_service.add_child(root.id, "b")
    second = tracking_service.add_child(branch.id, "i")
    leaves = [first, second] + [tracking_service.create_question(primary.id, str(i)) for i in range(2, 5)]
    base = datetime(2026, 1, 1, tzinfo=UTC)
    for leaf, status in zip(leaves, (AttemptStatus.PARTIAL, AttemptStatus.INCORRECT, AttemptStatus.NO_START, AttemptStatus.CORRECT)):
        tracking_service.add_attempt(leaf.id, AttemptStatus.INCORRECT, timestamp=base)
        tracking_service.add_attempt(leaf.id, status, timestamp=base + timedelta(days=1), note="Latest note")
    for leaf in leaves:
        tracking_service.toggle_question_bookmark(leaf.id)
    tracking_service.toggle_question_bookmark(root.id)  # Non-leaf is excluded.
    tracking_service.set_question_note(first.id, "Question note")
    tracking_service.finish_question_set_setup(primary.id)
    paper_leaf = tracking_service.create_question(paper.id, "1")
    tracking_service.add_attempt(paper_leaf.id, AttemptStatus.CORRECT)
    tracking_service.finish_question_set_setup(paper.id)
    setup_leaf = tracking_service.create_question(setup.id, "1")
    tracking_service.add_attempt(setup_leaf.id, AttemptStatus.INCORRECT)
    tracking_service.toggle_question_bookmark(setup_leaf.id)
    return primary, leaves, paper, setup


def test_progress_and_filters_use_latest_ready_leaves(tracking_service, review_data):
    primary, leaves, paper, setup = review_data
    summary, = ReviewService(tracking_service.database).snapshot()
    c = summary.counts
    assert summary.module == LibraryModule("Year 2", "Algebra")
    assert (c.total, c.attempted, c.unattempted, c.mastered) == (6, 5, 1, 2)
    assert (c.correct, c.partial, c.incorrect, c.no_start, c.bookmarked) == (2, 1, 1, 1, 5)
    assert summary.needs_setup == 1
    assert [item.question_set.id for item in summary.question_sets] == [primary.id, paper.id]
    for item in summary.question_sets:
        assert item.progress == tracking_service.question_set_progress(item.question_set.id)
    assert not summary.question_sets[0].progress.complete
    assert summary.question_sets[1].progress.complete and summary.question_sets[1].progress.mastered
    queue = summary.review_items()
    assert [item.question.id for item in queue] == [leaf.id for leaf in leaves[:3]]
    assert [item.label for item in queue] == ["1.a", "1.b)i", "2"]
    assert queue[0].question.note == "Question note"
    assert queue[0].latest.note == "Latest note"
    assert [item.question.id for item in summary.review_items(ReviewFilter.BOOKMARKED)] == [leaf.id for leaf in leaves]
    assert summary.review_items("Bookmarked") == summary.review_items(ReviewFilter.BOOKMARKED)
    for filter, leaf in zip((ReviewFilter.PARTIAL, ReviewFilter.INCORRECT, ReviewFilter.NO_START), leaves):
        assert [item.question.id for item in summary.review_items(filter)] == [leaf.id]


def test_reattempts_and_deletion_recompute_membership(tracking_service, review_data):
    _, leaves, _, _ = review_data
    service = ReviewService(tracking_service.database)
    tracking_service.add_attempt(leaves[0].id, AttemptStatus.PARTIAL)
    assert leaves[0].id in {item.question.id for item in service.snapshot()[0].review_items()}
    corrected = tracking_service.add_attempt(leaves[0].id, AttemptStatus.CORRECT)
    summary = service.snapshot()[0]
    assert leaves[0].id not in {item.question.id for item in summary.review_items()}
    assert leaves[0].id in {item.question.id for item in summary.review_items(ReviewFilter.BOOKMARKED)}
    tracking_service.delete_attempt(corrected.id)
    assert leaves[0].id in {item.question.id for item in service.snapshot()[0].review_items()}


def test_latest_ties_match_existing_domain_rule(tracking_service):
    question_set = tracking_service.create_question_set(QuestionSetKind.PROBLEM_SHEET, "Tie", setup_state=SetupState.READY)
    question = tracking_service.create_question(question_set.id, "1")
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    tracking_service.add_attempt(question.id, AttemptStatus.PARTIAL, timestamp=timestamp)
    tracking_service.add_attempt(question.id, AttemptStatus.CORRECT, timestamp=timestamp)
    expected = latest_attempt(tracking_service.attempts_for_question(question.id))
    item = ReviewService(tracking_service.database).snapshot()[0].items[0]
    assert item.latest == expected


def test_empty_ready_set_preserves_incomplete_semantics(tracking_service):
    tracking_service.create_question_set(QuestionSetKind.PROBLEM_SHEET, "Empty", setup_state=SetupState.READY)
    summary, = ReviewService(tracking_service.database).snapshot()
    assert summary.counts.total == 0
    assert not summary.question_sets[0].progress.complete
    assert not summary.question_sets[0].progress.mastered
    assert summary.review_items() == ()


def test_set_order_and_module_isolation(tracking_service, library_service, application_paths, pdf_factory, review_data):
    primary, _, _, _ = review_data
    library_service.create_module("Year 2", "Analysis")
    for name, module in (("Z Sheet", "Algebra"), ("Other", "Analysis")):
        file = library_service.import_pdf(pdf_factory(application_paths.data_dir / f"{name}.pdf"), "Year 2", module, DocumentType.PROBLEM_SHEET)
        question_set = library_service.question_sets_for_file(file.id, FileRole.MAIN)[0]
        leaf = tracking_service.create_question(question_set.id, "1")
        tracking_service.add_attempt(leaf.id, AttemptStatus.INCORRECT)
        tracking_service.finish_question_set_setup(question_set.id)
        if module == "Algebra":
            library_service.reorder_question_set(question_set.id, 0)
            first_id = question_set.id
    algebra, analysis = ReviewService(tracking_service.database).snapshot()
    assert algebra.question_sets[0].question_set.id == first_id
    assert algebra.review_items()[0].question_set.id == first_id
    assert analysis.counts.total == analysis.counts.incorrect == 1
    assert all(item.module.module_name == "Algebra" for item in algebra.items)


def test_snapshot_batches_queries_not_per_leaf(database, tracking_service, review_data, monkeypatch):
    statements = []
    connect = database.connect
    def traced():
        connection = connect()
        connection.set_trace_callback(statements.append)
        return connection
    monkeypatch.setattr(database, "connect", traced)
    ReviewService(database).snapshot()
    assert len([statement for statement in statements if statement.lstrip().upper().startswith("SELECT")]) == 3


@pytest.mark.parametrize("destination", ("tracker", "marking"))
def test_review_navigation_selects_leaf_and_return_refreshes_queue(
    qtbot, tracking_service, library_service, application_paths, review_data, destination,
):
    primary, leaves, _, _ = review_data
    window = MainWindow(application_paths, tracking_service, library_service)
    qtbot.addWidget(window)
    window.show()
    window.centralWidget().review_button.click()
    review = window.centralWidget()
    assert isinstance(review, ReviewScreen)
    review.filter_combo.setCurrentIndex(review.filter_combo.findData(ReviewFilter.INCORRECT))
    assert review.review_list.topLevelItemCount() == 1
    if destination == "tracker":
        review.tracker_button.click()
        target = window.centralWidget()
        assert isinstance(target, TrackerScreen)
        assert target.selected_question_id == leaves[1].id
        assert target.current_question_set_id == primary.id
        target.mark_selected(AttemptStatus.CORRECT)
        back = target.findChild(QPushButton, "backToHomeButton")
    else:
        review.marking_button.click()
        target = window.centralWidget()
        assert isinstance(target, MarkingWorkspace)
        assert target.compact_tracker.selected_question_id == leaves[1].id
        assert target.compact_tracker.active_question_set_id == primary.id
        target.compact_tracker.mark_selected(AttemptStatus.CORRECT)
        back = target.findChild(QPushButton, "markingBackButton")
    assert back.text() == "Back to Review"
    back.click()
    updated = window.centralWidget()
    assert isinstance(updated, ReviewScreen)
    assert updated.filter_combo.currentData() == ReviewFilter.INCORRECT
    assert updated.review_list.topLevelItemCount() == 0
    updated.filter_combo.setCurrentIndex(updated.filter_combo.findData(ReviewFilter.BOOKMARKED))
    assert leaves[1].id in {
        updated.review_list.topLevelItem(index).data(0, Qt.ItemDataRole.UserRole).question.id
        for index in range(updated.review_list.topLevelItemCount())
    }
