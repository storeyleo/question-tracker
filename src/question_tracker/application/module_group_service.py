import sqlite3
from uuid import uuid4

from question_tracker.domain.library import LibraryModule
from question_tracker.domain.module_group import ModuleGroup
from question_tracker.persistence.database import Database
from question_tracker.persistence.module_group_repository import ModuleGroupRepository


class ModuleGroupError(ValueError):
    pass


class ModuleGroupService:
    """Organisation metadata only; never changes library paths or tracking rows."""
    def __init__(self, database: Database) -> None:
        self.database = database

    def snapshot(self) -> tuple[tuple[ModuleGroup, ...], dict[tuple[str, str], str]]:
        with self.database.read() as connection:
            repo = ModuleGroupRepository(connection)
            return repo.groups(), repo.assignments()

    def create(self, year: str, name: str) -> ModuleGroup:
        group = ModuleGroup(str(uuid4()), year.casefold(), self._name(name))
        self._save(group)
        return group

    def rename(self, group_id: str, name: str) -> ModuleGroup:
        with self.database.transaction() as connection:
            repo = ModuleGroupRepository(connection)
            old = self._require(repo, group_id)
            group = ModuleGroup(old.id, old.year_key, self._name(name))
            self._write(repo, group)
            return group

    def delete(self, group_id: str) -> None:
        with self.database.transaction() as connection:
            repo = ModuleGroupRepository(connection)
            self._require(repo, group_id)
            repo.delete(group_id)

    def assign(self, module: LibraryModule, group_id: str | None) -> None:
        with self.database.transaction() as connection:
            repo = ModuleGroupRepository(connection)
            if group_id is not None:
                group = self._require(repo, group_id)
                if group.year_key != module.year_name.casefold():
                    raise ModuleGroupError("Modules can only be grouped within their own year.")
            repo.assign(module.year_name.casefold(), module.module_name.casefold(), group_id)

    def _save(self, group: ModuleGroup) -> None:
        with self.database.transaction() as connection:
            self._write(ModuleGroupRepository(connection), group)

    @staticmethod
    def _write(repo: ModuleGroupRepository, group: ModuleGroup) -> None:
        try:
            repo.save(group)
        except sqlite3.IntegrityError as error:
            raise ModuleGroupError("A group with that name already exists in this year.") from error

    @staticmethod
    def _require(repo: ModuleGroupRepository, group_id: str) -> ModuleGroup:
        group = repo.get(group_id)
        if group is None:
            raise ModuleGroupError("This group no longer exists. Refresh Home and try again.")
        return group

    @staticmethod
    def _name(name: str) -> str:
        name = name.strip()
        if not name:
            raise ModuleGroupError("Enter a group name.")
        return name
