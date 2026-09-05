from __future__ import annotations

from dataclasses import dataclass

from question_tracker.application.library_service import LibraryService
from question_tracker.application.tracking_service import TrackingService
from question_tracker.domain.library import (
    FileAvailability,
    FileRole,
    LibraryModule,
    LibraryScanResult,
    ManagedFile,
)
from question_tracker.domain.tracking import QuestionSet, QuestionSetKind, SetupState


@dataclass(frozen=True, slots=True)
class CategorySummary:
    total: int
    complete: int
    mastered: int
    needs_setup: int


@dataclass(frozen=True, slots=True)
class ModuleSummary:
    module: LibraryModule
    problem_sheets: CategorySummary
    past_papers: CategorySummary
    first_ready_question_set_id: str | None

    @property
    def needs_setup_count(self) -> int:
        return self.problem_sheets.needs_setup + self.past_papers.needs_setup


@dataclass(frozen=True, slots=True)
class NeedsSetupItem:
    question_set: QuestionSet
    module: LibraryModule
    main_file: ManagedFile

    @property
    def is_missing(self) -> bool:
        return self.main_file.availability is FileAvailability.MISSING


@dataclass(frozen=True, slots=True)
class HomeSnapshot:
    years: tuple[str, ...]
    modules: tuple[ModuleSummary, ...]
    needs_setup: tuple[NeedsSetupItem, ...]


def build_home_snapshot(
    service: TrackingService,
    library_service: LibraryService,
    scan_result: LibraryScanResult,
) -> HomeSnapshot:
    """Derive Home summaries from managed files and current tracking state."""
    managed_files = library_service.list_files()
    years = set(scan_result.years)
    modules = set(scan_result.modules)
    for managed_file in managed_files:
        parts = managed_file.relative_path.parts
        if len(parts) >= 2:
            years.add(parts[0])
            modules.add(LibraryModule(parts[0], parts[1]))

    module_summaries: list[ModuleSummary] = []
    needs_setup: list[NeedsSetupItem] = []
    for module in sorted(
        modules,
        key=lambda item: (item.year_name.casefold(), item.module_name.casefold()),
    ):
        question_sets = library_service.question_sets_for_module(
            module.year_name, module.module_name
        )
        problem_sheets = _category_summary(
            service,
            tuple(
                item
                for item in question_sets
                if item.kind is QuestionSetKind.PROBLEM_SHEET
            ),
        )
        past_papers = _category_summary(
            service,
            tuple(
                item
                for item in question_sets
                if item.kind is QuestionSetKind.PAST_PAPER
            ),
        )
        first_ready = next(
            (
                item.id
                for item in question_sets
                if item.setup_state is SetupState.READY
            ),
            None,
        )
        module_summaries.append(
            ModuleSummary(
                module=module,
                problem_sheets=problem_sheets,
                past_papers=past_papers,
                first_ready_question_set_id=first_ready,
            )
        )
        for question_set in question_sets:
            if question_set.setup_state is not SetupState.NEEDS_SETUP:
                continue
            main_files = library_service.files_for_question_set(
                question_set.id, FileRole.MAIN
            )
            if main_files:
                needs_setup.append(
                    NeedsSetupItem(question_set, module, main_files[0])
                )

    return HomeSnapshot(
        years=tuple(sorted(years, key=str.casefold)),
        modules=tuple(module_summaries),
        needs_setup=tuple(needs_setup),
    )


def _category_summary(
    service: TrackingService, question_sets: tuple[QuestionSet, ...]
) -> CategorySummary:
    complete = 0
    mastered = 0
    needs_setup = 0
    for question_set in question_sets:
        if question_set.setup_state is SetupState.NEEDS_SETUP:
            needs_setup += 1
            continue
        progress = service.question_set_progress(question_set.id)
        complete += int(progress.complete)
        mastered += int(progress.mastered)
    return CategorySummary(
        total=len(question_sets),
        complete=complete,
        mastered=mastered,
        needs_setup=needs_setup,
    )
