-- V2/V3 共用 schema（V3 用 wrangler d1 migrations apply 执行，V2 用 init_db.py）
-- 相对 V1 的改动：score 列改为 REAL（修正 V1 中 INTEGER 类型偏差）

CREATE TABLE IF NOT EXISTS lessons (
    lesson_seq INTEGER PRIMARY KEY,
    unit_id    INTEGER NOT NULL,
    unit_name  TEXT    NOT NULL,
    lesson_name TEXT   NOT NULL
);

CREATE TABLE IF NOT EXISTS knowledge_points (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    lesson_seq  INTEGER NOT NULL,
    target      TEXT    NOT NULL,
    category    TEXT    NOT NULL,
    options_json TEXT   NOT NULL,
    FOREIGN KEY(lesson_seq) REFERENCES lessons(lesson_seq)
);

CREATE TABLE IF NOT EXISTS user_progress (
    user_id            INTEGER PRIMARY KEY,
    current_lesson_seq INTEGER DEFAULT 1,
    updated_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS user_memory (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id          INTEGER NOT NULL,
    kp_id            INTEGER NOT NULL,
    status           INTEGER DEFAULT 0,   -- 0 学习中 / 1 已掌握
    error_count      INTEGER DEFAULT 0,
    correct_streak   INTEGER DEFAULT 0,
    last_tested_date DATE,
    next_review_date DATE,
    UNIQUE(user_id, kp_id)
);

CREATE TABLE IF NOT EXISTS dictation_history (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id        INTEGER NOT NULL,
    dictation_type TEXT    NOT NULL,  -- 'daily' | 'unit'
    scope_id       INTEGER NOT NULL,
    score          REAL    DEFAULT 0,
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS dictation_items (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    history_id INTEGER NOT NULL,
    kp_id      INTEGER NOT NULL,
    is_correct BOOLEAN NOT NULL,
    FOREIGN KEY(history_id) REFERENCES dictation_history(id)
);

-- 种子用户（单用户 MVP）
INSERT OR IGNORE INTO user_progress (user_id, current_lesson_seq) VALUES (1, 1);
