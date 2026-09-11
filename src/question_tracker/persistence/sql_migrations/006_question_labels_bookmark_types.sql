-- Keep topic identities/colours. Retain the first definition in display order.
DELETE FROM question_topics
WHERE topic_id <> (
    SELECT candidate.topic_id FROM question_topics candidate
    JOIN topics ON topics.id = candidate.topic_id
    WHERE candidate.question_id = question_topics.question_id
    ORDER BY topics.sort_order, topics.id LIMIT 1
);
CREATE UNIQUE INDEX uq_question_single_label ON question_topics(question_id);

CREATE TABLE bookmark_types (
    id TEXT PRIMARY KEY NOT NULL,
    name TEXT NOT NULL CHECK (trim(name) <> ''),
    name_key TEXT NOT NULL UNIQUE,
    sort_order INTEGER NOT NULL CHECK (sort_order >= 0)
);
INSERT INTO bookmark_types VALUES
 ('15189d1a-65c2-4daf-b5c1-3b4802165001','Bookmark','bookmark',0),
 ('15189d1a-65c2-4daf-b5c1-3b4802165002','Revisit','revisit',1),
 ('15189d1a-65c2-4daf-b5c1-3b4802165003','Important','important',2);
CREATE TABLE question_bookmarks (
    question_id TEXT NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    bookmark_type_id TEXT NOT NULL REFERENCES bookmark_types(id) ON DELETE CASCADE,
    PRIMARY KEY(question_id, bookmark_type_id)
);
INSERT INTO question_bookmarks
SELECT id, '15189d1a-65c2-4daf-b5c1-3b4802165001' FROM questions WHERE bookmarked=1;

-- The legacy boolean is now a read-only compatibility summary for old views.
-- All application writes go through question_bookmarks.
CREATE TRIGGER bookmark_summary_insert AFTER INSERT ON question_bookmarks BEGIN
 UPDATE questions SET bookmarked=1 WHERE id=NEW.question_id;
END;
CREATE TRIGGER bookmark_summary_delete AFTER DELETE ON question_bookmarks BEGIN
 UPDATE questions SET bookmarked=EXISTS(
   SELECT 1 FROM question_bookmarks WHERE question_id=OLD.question_id
 ) WHERE id=OLD.question_id;
END;
