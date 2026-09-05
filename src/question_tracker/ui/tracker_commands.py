from __future__ import annotations

from PySide6.QtGui import QUndoCommand

from question_tracker.application.tracking_service import TrackingService
from question_tracker.domain.tracking import Attempt, AttemptStatus


class AddAttemptCommand(QUndoCommand):
    def __init__(
        self,
        service: TrackingService,
        question_id: str,
        status: AttemptStatus,
    ) -> None:
        super().__init__(f"Mark question {status.value.replace('_', ' ')}")
        self.service = service
        self.question_id = question_id
        self.status = status
        self.attempt: Attempt | None = None

    def redo(self) -> None:
        if self.attempt is None:
            self.attempt = self.service.add_attempt(self.question_id, self.status)
        else:
            self.service.restore_attempt(self.attempt)

    def undo(self) -> None:
        assert self.attempt is not None
        self.service.delete_attempt(self.attempt.id)


class EditAttemptCommand(QUndoCommand):
    def __init__(
        self,
        service: TrackingService,
        before: Attempt,
        status: AttemptStatus,
        note: str,
    ) -> None:
        super().__init__("Edit attempt")
        self.service = service
        self.before = before
        self.status = status
        self.note = note

    def redo(self) -> None:
        self.service.edit_attempt(
            self.before.id,
            status=self.status,
            timestamp=self.before.timestamp,
            note=self.note,
        )

    def undo(self) -> None:
        self.service.edit_attempt(
            self.before.id,
            status=self.before.status,
            timestamp=self.before.timestamp,
            note=self.before.note,
        )


class DeleteAttemptCommand(QUndoCommand):
    def __init__(self, service: TrackingService, attempt: Attempt) -> None:
        super().__init__("Delete attempt")
        self.service = service
        self.attempt = attempt

    def redo(self) -> None:
        self.service.delete_attempt(self.attempt.id)

    def undo(self) -> None:
        self.service.restore_attempt(self.attempt)


class DeleteAttemptsCommand(QUndoCommand):
    def __init__(
        self, service: TrackingService, attempts: tuple[Attempt, ...]
    ) -> None:
        super().__init__(f"Delete {len(attempts)} attempts")
        self.service = service
        self.attempts = attempts

    def redo(self) -> None:
        for attempt in self.attempts:
            self.service.delete_attempt(attempt.id)

    def undo(self) -> None:
        for attempt in self.attempts:
            self.service.restore_attempt(attempt)


class SetBookmarkCommand(QUndoCommand):
    def __init__(
        self,
        service: TrackingService,
        question_id: str,
        before: bool,
        after: bool,
    ) -> None:
        super().__init__("Toggle question bookmark")
        self.service = service
        self.question_id = question_id
        self.before = before
        self.after = after

    def redo(self) -> None:
        self.service.set_question_bookmark(self.question_id, self.after)

    def undo(self) -> None:
        self.service.set_question_bookmark(self.question_id, self.before)


class EditQuestionNoteCommand(QUndoCommand):
    def __init__(
        self,
        service: TrackingService,
        question_id: str,
        before: str,
        after: str,
    ) -> None:
        super().__init__("Edit question note")
        self.service = service
        self.question_id = question_id
        self.before = before
        self.after = after

    def redo(self) -> None:
        self.service.set_question_note(self.question_id, self.after)

    def undo(self) -> None:
        self.service.set_question_note(self.question_id, self.before)
