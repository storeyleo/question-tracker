ALTER TABLE question_sets ADD COLUMN display_order INTEGER
    CHECK (display_order IS NULL OR display_order >= 0);
