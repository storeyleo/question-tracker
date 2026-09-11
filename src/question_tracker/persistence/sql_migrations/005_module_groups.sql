-- Home organisation only. Module identity remains the existing year/module pair.
CREATE TABLE module_groups (
    id TEXT PRIMARY KEY NOT NULL,
    year_key TEXT NOT NULL,
    name TEXT NOT NULL CHECK (trim(name) <> ''),
    name_key TEXT NOT NULL,
    UNIQUE (year_key, name_key),
    UNIQUE (id, year_key)
);

CREATE TABLE module_group_assignments (
    year_key TEXT NOT NULL,
    module_key TEXT NOT NULL,
    group_id TEXT NOT NULL,
    PRIMARY KEY (year_key, module_key),
    FOREIGN KEY (group_id, year_key)
        REFERENCES module_groups(id, year_key) ON DELETE CASCADE
);
