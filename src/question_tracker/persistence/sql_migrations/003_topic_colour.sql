ALTER TABLE topics ADD COLUMN colour TEXT NOT NULL DEFAULT '#D9EAF7'
    CHECK (
        length(colour) = 7
        AND substr(colour, 1, 1) = '#'
    );
