from question_tracker.domain.bookmarks import BookmarkType


class BookmarkRepository:
    def __init__(self, connection):
        self.connection = connection

    def types(self):
        return tuple(BookmarkType(row["id"], row["name"], row["sort_order"]) for row in
                     self.connection.execute("SELECT * FROM bookmark_types ORDER BY sort_order,id"))

    def save_type(self, bookmark_type):
        self.connection.execute(
            "INSERT INTO bookmark_types VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE "
            "SET name=excluded.name,name_key=excluded.name_key",
            (bookmark_type.id, bookmark_type.name, bookmark_type.name.casefold(), bookmark_type.sort_order),
        )

    def delete_type(self, type_id):
        self.connection.execute("DELETE FROM bookmark_types WHERE id=?", (type_id,))

    def ids_for_question(self, question_id):
        return {row[0] for row in self.connection.execute(
            "SELECT bookmark_type_id FROM question_bookmarks WHERE question_id=?", (question_id,),
        )}

    def set(self, question_id, type_id, enabled):
        if enabled:
            self.connection.execute("INSERT OR IGNORE INTO question_bookmarks VALUES (?,?)", (question_id, type_id))
        else:
            self.connection.execute("DELETE FROM question_bookmarks WHERE question_id=? AND bookmark_type_id=?", (question_id, type_id))

    def question_ids(self, type_id=None):
        if type_id is None:
            rows = self.connection.execute("SELECT DISTINCT question_id FROM question_bookmarks ORDER BY question_id")
        else:
            rows = self.connection.execute("SELECT question_id FROM question_bookmarks WHERE bookmark_type_id=? ORDER BY question_id", (type_id,))
        return tuple(row[0] for row in rows)

    def transfer(self, source_ids, target_id):
        for source_id in source_ids:
            self.connection.execute(
                "INSERT OR IGNORE INTO question_bookmarks SELECT ?,bookmark_type_id FROM question_bookmarks WHERE question_id=?",
                (target_id, source_id),
            )
            self.connection.execute("DELETE FROM question_bookmarks WHERE question_id=?", (source_id,))
