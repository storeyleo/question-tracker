CREATE TABLE managed_files (
    id TEXT PRIMARY KEY NOT NULL CHECK (id <> ''),
    relative_path TEXT NOT NULL CHECK (relative_path <> ''),
    path_key TEXT NOT NULL UNIQUE CHECK (path_key <> ''),
    document_type TEXT NOT NULL DEFAULT 'unknown'
        CHECK (document_type IN ('unknown', 'problem_sheet', 'solution', 'past_paper')),
    availability TEXT NOT NULL DEFAULT 'present'
        CHECK (availability IN ('present', 'missing')),
    size_bytes INTEGER CHECK (size_bytes IS NULL OR size_bytes >= 0),
    modified_ns INTEGER CHECK (modified_ns IS NULL OR modified_ns >= 0),
    first_seen_at_utc TEXT NOT NULL,
    last_seen_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
);

CREATE TABLE question_sets (
    id TEXT PRIMARY KEY NOT NULL CHECK (id <> ''),
    kind TEXT NOT NULL CHECK (kind IN ('problem_sheet', 'past_paper')),
    display_name TEXT NOT NULL CHECK (trim(display_name) <> ''),
    general_note TEXT NOT NULL DEFAULT '',
    setup_state TEXT NOT NULL DEFAULT 'needs_setup'
        CHECK (setup_state IN ('needs_setup', 'ready')),
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
);

CREATE TABLE question_set_files (
    question_set_id TEXT NOT NULL
        REFERENCES question_sets(id) ON DELETE CASCADE,
    file_id TEXT NOT NULL
        REFERENCES managed_files(id) ON DELETE RESTRICT,
    role TEXT NOT NULL CHECK (role IN ('main', 'reference')),
    sort_order INTEGER NOT NULL DEFAULT 0 CHECK (sort_order >= 0),
    PRIMARY KEY (question_set_id, file_id)
);

CREATE UNIQUE INDEX uq_question_set_main_file
    ON question_set_files(question_set_id)
    WHERE role = 'main';

CREATE UNIQUE INDEX uq_file_used_as_main
    ON question_set_files(file_id)
    WHERE role = 'main';

CREATE INDEX ix_question_set_files_order
    ON question_set_files(question_set_id, role, sort_order);

CREATE TABLE questions (
    id TEXT PRIMARY KEY NOT NULL CHECK (id <> ''),
    question_set_id TEXT NOT NULL
        REFERENCES question_sets(id) ON DELETE CASCADE,
    parent_id TEXT,
    local_label TEXT NOT NULL CHECK (trim(local_label) <> ''),
    sort_order INTEGER NOT NULL CHECK (sort_order >= 0),
    note TEXT NOT NULL DEFAULT '',
    bookmarked INTEGER NOT NULL DEFAULT 0 CHECK (bookmarked IN (0, 1)),
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL,
    UNIQUE (id, question_set_id),
    FOREIGN KEY (parent_id, question_set_id)
        REFERENCES questions(id, question_set_id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX uq_question_sibling_order
    ON questions(question_set_id, ifnull(parent_id, ''), sort_order);

CREATE INDEX ix_questions_parent
    ON questions(question_set_id, parent_id, sort_order);

CREATE TABLE topics (
    id TEXT PRIMARY KEY NOT NULL CHECK (id <> ''),
    question_set_id TEXT NOT NULL
        REFERENCES question_sets(id) ON DELETE CASCADE,
    title TEXT NOT NULL CHECK (trim(title) <> ''),
    sort_order INTEGER NOT NULL CHECK (sort_order >= 0),
    UNIQUE (id, question_set_id),
    UNIQUE (question_set_id, sort_order)
);

CREATE UNIQUE INDEX uq_topic_title
    ON topics(question_set_id, title COLLATE NOCASE);

CREATE TABLE question_topics (
    question_id TEXT NOT NULL,
    topic_id TEXT NOT NULL,
    question_set_id TEXT NOT NULL,
    PRIMARY KEY (question_id, topic_id),
    FOREIGN KEY (question_id, question_set_id)
        REFERENCES questions(id, question_set_id) ON DELETE CASCADE,
    FOREIGN KEY (topic_id, question_set_id)
        REFERENCES topics(id, question_set_id) ON DELETE CASCADE
);

CREATE INDEX ix_question_topics_topic
    ON question_topics(topic_id, question_id);

CREATE TABLE attempts (
    id TEXT PRIMARY KEY NOT NULL CHECK (id <> ''),
    question_id TEXT NOT NULL
        REFERENCES questions(id) ON DELETE CASCADE,
    status TEXT NOT NULL
        CHECK (status IN ('correct', 'partial', 'incorrect', 'no_start')),
    occurred_at_utc TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
);

CREATE INDEX ix_attempts_question_time
    ON attempts(question_id, occurred_at_utc DESC, created_at_utc DESC, id);

