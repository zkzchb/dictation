#!/usr/bin/env python3
"""shared/init_db.py —— 从教材包建库（V1/V2 共用）

用法:
    python shared/init_db.py --db v2/dictation.db            # 建新库
    python shared/init_db.py --db v2/dictation.db --force    # 已存在则重建

安全约束（见设计文档 §11）：
  * 目标库已存在时默认拒绝执行，需显式 --force
  * --force 会先自动备份为 <db>.bak_YYYYmmdd_HHMMSS
  * 只灌静态表（lessons / knowledge_points），动态表建空
"""
import os
import sys
import json
import shutil
import sqlite3
import argparse
import tempfile
from datetime import datetime

try:
    from .content_pack import ContentPackError, DEFAULT_CONTENT_ROOT, load_content_pack
except ImportError:  # direct execution: python shared/init_db.py
    from content_pack import ContentPackError, DEFAULT_CONTENT_ROOT, load_content_pack

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))

SCHEMA = """
CREATE TABLE IF NOT EXISTS lessons (
    lesson_seq   INTEGER PRIMARY KEY,
    unit_id      INTEGER NOT NULL,
    unit_name    TEXT    NOT NULL,
    lesson_title TEXT    DEFAULT '',
    lesson_name  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS knowledge_points (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    lesson_seq   INTEGER NOT NULL,
    target       TEXT    NOT NULL,
    category     TEXT    NOT NULL,
    options_json TEXT    NOT NULL,
    FOREIGN KEY (lesson_seq) REFERENCES lessons(lesson_seq)
);

CREATE TABLE IF NOT EXISTS user_progress (
    user_id            INTEGER PRIMARY KEY,
    current_lesson_seq INTEGER DEFAULT 0,
    updated_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS user_memory (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id          INTEGER NOT NULL,
    kp_id            INTEGER NOT NULL,
    status           INTEGER DEFAULT 0,
    error_count      INTEGER DEFAULT 0,
    correct_streak   INTEGER DEFAULT 0,
    last_tested_date DATE,
    next_review_date DATE,
    UNIQUE (user_id, kp_id)
);

CREATE TABLE IF NOT EXISTS dictation_history (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id        INTEGER NOT NULL,
    dictation_type TEXT    NOT NULL,
    scope_id       INTEGER NOT NULL,
    score          REAL    DEFAULT 0,
    -- 本次播报过的多音字 kp_id（逗号分隔）。多音字不判分、不入 dictation_items，
    -- 但要支持「连续两次出现则休息一轮」的轮换规则，所以单独记在这里。
    poly_ids       TEXT    DEFAULT '',
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS dictation_items (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    history_id INTEGER NOT NULL,
    kp_id      INTEGER NOT NULL,
    is_correct BOOLEAN NOT NULL,
    FOREIGN KEY (history_id) REFERENCES dictation_history(id)
);

-- V2 提交幂等回执：浏览器网络重试不能重复写历史或增加记忆连续答对次数。
CREATE TABLE IF NOT EXISTS submission_receipts (
    submission_id TEXT PRIMARY KEY,
    response_json TEXT NOT NULL,
    created_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_kp_lesson ON knowledge_points(lesson_seq, category);
CREATE INDEX IF NOT EXISTS idx_items_history ON dictation_items(history_id);
CREATE INDEX IF NOT EXISTS idx_items_kp ON dictation_items(kp_id, is_correct);
CREATE INDEX IF NOT EXISTS idx_history_user ON dictation_history(user_id, created_at DESC);
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True, help="目标数据库路径")
    ap.add_argument(
        "--content-root", default=str(DEFAULT_CONTENT_ROOT),
        help="教材包目录（默认 chinese/3a）",
    )
    ap.add_argument("--force", action="store_true", help="已存在则备份后重建")
    args = ap.parse_args()

    db = os.path.abspath(args.db)

    if os.path.exists(db):
        if not args.force:
            print(f"[X] 数据库已存在: {db}")
            print("    如需重建请加 --force（会先自动备份）")
            sys.exit(1)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        bak = f"{db}.bak_{stamp}"
        shutil.copy(db, bak)
        print(f"[OK] 已备份原库 -> {os.path.basename(bak)}")

    content_root = os.path.abspath(args.content_root)
    try:
        pack = load_content_pack(content_root)
    except ContentPackError as exc:
        print(f"[X] 内容包校验失败: {exc}")
        sys.exit(1)
    lessons = pack.lessons
    kps = pack.knowledge_points

    db_dir = os.path.dirname(db)
    os.makedirs(db_dir, exist_ok=True)
    fd, tmp_db = tempfile.mkstemp(
        prefix=f".{os.path.basename(db)}.", suffix=".part", dir=db_dir
    )
    os.close(fd)
    conn = None
    try:
        conn = sqlite3.connect(tmp_db)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(SCHEMA)

        conn.executemany(
            "INSERT INTO lessons (lesson_seq, unit_id, unit_name, lesson_title, lesson_name)"
            " VALUES (?,?,?,?,?)",
            [(l["lesson_seq"], l["unit_id"], l["unit_name"],
              l.get("lesson_title", ""), l["lesson_name"]) for l in lessons]
        )
        conn.executemany(
            "INSERT INTO knowledge_points (lesson_seq, target, category, options_json) VALUES (?,?,?,?)",
            [(k["lesson_seq"], k["target"], k["category"],
              json.dumps(k["options_json"], ensure_ascii=False)) for k in kps]
        )
        conn.execute(
            "INSERT INTO user_progress (user_id, current_lesson_seq) VALUES (1, ?)",
            (pack.runtime.initial_lesson,),
        )
        conn.commit()

        n_les = conn.execute("SELECT COUNT(*) FROM lessons").fetchone()[0]
        n_kp = conn.execute("SELECT COUNT(*) FROM knowledge_points").fetchone()[0]
        cats = conn.execute(
            "SELECT category, COUNT(*) FROM knowledge_points GROUP BY category"
        ).fetchall()
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("SQLite integrity_check 失败")
        if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise RuntimeError("SQLite foreign_key_check 失败")
        conn.close()
        conn = None
        os.replace(tmp_db, db)
    finally:
        if conn is not None:
            conn.close()
        if os.path.exists(tmp_db):
            os.remove(tmp_db)

    print(f"[OK] 建库完成: {db}")
    print(f"     内容包 {pack.display_name} ({pack.id})")
    print(f"     课程 {n_les} 门, 知识点 {n_kp} 条")
    for c, n in cats:
        print(f"       {c}: {n}")
    print("     动态表（听写历史/错词/记忆）为空，从零起算")


if __name__ == "__main__":
    main()
