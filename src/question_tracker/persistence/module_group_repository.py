import sqlite3

from question_tracker.domain.module_group import ModuleGroup


class ModuleGroupRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def groups(self) -> tuple[ModuleGroup, ...]:
        return tuple(ModuleGroup(row["id"], row["year_key"], row["name"]) for row in
                     self.connection.execute("SELECT * FROM module_groups ORDER BY year_key, name_key"))

    def get(self, group_id: str) -> ModuleGroup | None:
        row = self.connection.execute("SELECT * FROM module_groups WHERE id=?", (group_id,)).fetchone()
        return ModuleGroup(row["id"], row["year_key"], row["name"]) if row else None

    def save(self, group: ModuleGroup) -> None:
        self.connection.execute(
            "INSERT INTO module_groups (id,year_key,name,name_key) VALUES (?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET name=excluded.name, name_key=excluded.name_key",
            (group.id, group.year_key, group.name, group.name.casefold()),
        )

    def delete(self, group_id: str) -> None:
        self.connection.execute("DELETE FROM module_groups WHERE id=?", (group_id,))

    def assignments(self) -> dict[tuple[str, str], str]:
        return {(row["year_key"], row["module_key"]): row["group_id"] for row in
                self.connection.execute("SELECT * FROM module_group_assignments")}

    def assign(self, year: str, module: str, group_id: str | None) -> None:
        if group_id is None:
            self.connection.execute(
                "DELETE FROM module_group_assignments WHERE year_key=? AND module_key=?", (year, module),
            )
        else:
            self.connection.execute(
                "INSERT INTO module_group_assignments VALUES (?,?,?) "
                "ON CONFLICT(year_key,module_key) DO UPDATE SET group_id=excluded.group_id",
                (year, module, group_id),
            )
