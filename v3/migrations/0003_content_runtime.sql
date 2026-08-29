-- Content-pack runtime metadata. The singleton row is refreshed on every
-- deployment from the validated dataset.json; this migration only owns schema.
CREATE TABLE IF NOT EXISTS content_runtime (
    singleton              INTEGER PRIMARY KEY CHECK (singleton = 1),
    pack_id                TEXT    NOT NULL,
    display_name           TEXT    NOT NULL,
    dataset_sha256         TEXT    NOT NULL,
    cold_start_lesson      INTEGER,
    initial_lesson         INTEGER NOT NULL,
    review_lessons_json    TEXT    NOT NULL,
    daily_target           INTEGER NOT NULL CHECK (daily_target > 0),
    review_target          INTEGER NOT NULL CHECK (review_target > 0),
    polyphonic_per_lesson  INTEGER NOT NULL CHECK (polyphonic_per_lesson >= 0)
);
