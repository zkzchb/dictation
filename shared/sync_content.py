#!/usr/bin/env python3
"""Synchronize an additive content-pack update into an existing V2 database.

Lesson and knowledge-point IDs are stable identities. Existing learning history
is preserved; removing IDs or switching to another pack is rejected so a
content update cannot silently reinterpret prior results.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import sys

try:
    from .content_pack import ContentPackError, DEFAULT_CONTENT_ROOT, load_content_pack
except ImportError:  # direct execution
    from content_pack import ContentPackError, DEFAULT_CONTENT_ROOT, load_content_pack


CONTENT_RUNTIME_SCHEMA = """
CREATE TABLE IF NOT EXISTS content_runtime (
    singleton       INTEGER PRIMARY KEY CHECK (singleton = 1),
    pack_id         TEXT    NOT NULL,
    display_name    TEXT    NOT NULL,
    content_version TEXT,
    dataset_sha256  TEXT    NOT NULL,
    synced_at       TEXT    NOT NULL
);
"""


class ContentSyncError(RuntimeError):
    pass


def _ids(conn: sqlite3.Connection, table: str, column: str) -> set[int]:
    return {int(row[0]) for row in conn.execute(f"SELECT {column} FROM {table}")}


def sync_content(database: Path, content_root: Path) -> dict[str, object]:
    if not database.is_file():
        raise ContentSyncError(f"数据库不存在: {database}")
    pack = load_content_pack(content_root)
    conn = sqlite3.connect(database, timeout=30)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.executescript(CONTENT_RUNTIME_SCHEMA)
        conn.execute("BEGIN IMMEDIATE")

        runtime = conn.execute(
            "SELECT pack_id FROM content_runtime WHERE singleton = 1"
        ).fetchone()
        if runtime is not None and runtime[0] != pack.id:
            raise ContentSyncError(
                f"数据库属于内容包 {runtime[0]!r}，不能切换为 {pack.id!r}；"
                "请为不同 pack 使用独立数据库"
            )

        pack_lessons = {item["lesson_seq"] for item in pack.lessons}
        pack_points = {item["id"] for item in pack.knowledge_points}
        existing_lessons = _ids(conn, "lessons", "lesson_seq")
        existing_points = _ids(conn, "knowledge_points", "id")
        removed_lessons = sorted(existing_lessons - pack_lessons)
        removed_points = sorted(existing_points - pack_points)
        if removed_lessons or removed_points:
            raise ContentSyncError(
                "content-pack v1 只允许追加或原 ID 修订；"
                f"本次将移除 {len(removed_lessons)} 门课程、{len(removed_points)} 个知识点"
            )

        conn.executemany(
            "INSERT INTO lessons "
            "(lesson_seq, unit_id, unit_name, lesson_title, lesson_name) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(lesson_seq) DO UPDATE SET "
            "unit_id=excluded.unit_id, unit_name=excluded.unit_name, "
            "lesson_title=excluded.lesson_title, lesson_name=excluded.lesson_name",
            [
                (
                    item["lesson_seq"],
                    item["unit_id"],
                    item["unit_name"],
                    item.get("lesson_title", ""),
                    item["lesson_name"],
                )
                for item in pack.lessons
            ],
        )
        conn.executemany(
            "INSERT INTO knowledge_points "
            "(id, lesson_seq, target, category, options_json) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET "
            "lesson_seq=excluded.lesson_seq, target=excluded.target, "
            "category=excluded.category, options_json=excluded.options_json",
            [
                (
                    item["id"],
                    item["lesson_seq"],
                    item["target"],
                    item["category"],
                    json.dumps(item["options_json"], ensure_ascii=False),
                )
                for item in pack.knowledge_points
            ],
        )
        if conn.execute("SELECT 1 FROM user_progress WHERE user_id = 1").fetchone() is None:
            conn.execute(
                "INSERT INTO user_progress (user_id, current_lesson_seq) VALUES (1, ?)",
                (pack.runtime.initial_lesson,),
            )
        conn.execute(
            "INSERT INTO content_runtime "
            "(singleton, pack_id, display_name, content_version, dataset_sha256, synced_at) "
            "VALUES (1, ?, ?, ?, ?, datetime('now')) "
            "ON CONFLICT(singleton) DO UPDATE SET "
            "pack_id=excluded.pack_id, display_name=excluded.display_name, "
            "content_version=excluded.content_version, "
            "dataset_sha256=excluded.dataset_sha256, synced_at=excluded.synced_at",
            (
                pack.id,
                pack.display_name,
                pack.metadata.get("version"),
                pack.dataset_sha256,
            ),
        )

        foreign_key_error = conn.execute("PRAGMA foreign_key_check").fetchone()
        if foreign_key_error is not None:
            raise ContentSyncError(f"foreign_key_check 失败: {foreign_key_error}")
        conn.commit()
        result = {
            "pack_id": pack.id,
            "content_version": pack.metadata.get("version"),
            "dataset_sha256": pack.dataset_sha256,
            "lessons_added": len(pack_lessons - existing_lessons),
            "knowledge_points_added": len(pack_points - existing_points),
            "lessons": len(pack_lessons),
            "knowledge_points": len(pack_points),
        }
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--content-root", type=Path, default=DEFAULT_CONTENT_ROOT)
    args = parser.parse_args()
    try:
        result = sync_content(args.db.resolve(), args.content_root.resolve())
    except (ContentPackError, ContentSyncError, OSError, sqlite3.Error) as exc:
        print(f"[X] 内容同步失败: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
