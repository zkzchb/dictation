import os
from pathlib import Path
import random
import sqlite3
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault(
    "DICTATION_CONTENT_ROOT", str(ROOT / "tests" / "fixtures" / "demo-content-pack")
)

from shared import init_db, selector


class SelectorContentUnitTests(unittest.TestCase):
    def test_review_uses_unit_id_not_lesson_number_shape(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        try:
            conn.executescript(init_db.SCHEMA)
            conn.executemany(
                "INSERT INTO lessons "
                "(lesson_seq, unit_id, unit_name, lesson_title, lesson_name) "
                "VALUES (?, ?, ?, '', ?)",
                [
                    (6, 41, "上一单元", "上一课"),
                    (7, 42, "本单元", "本单元复习"),
                    (9001, 1, "冷启动", "填充池"),
                    (88001, 42, "本单元", "第一课"),
                    (88002, 42, "本单元", "第二课"),
                ],
            )
            conn.executemany(
                "INSERT INTO knowledge_points "
                "(lesson_seq, target, category, options_json) VALUES (?, ?, '词语', ?)",
                [
                    (6, "旧词", '[{"text":"旧词"}]'),
                    (88001, "新词甲", '[{"text":"新词甲"}]'),
                    (88002, "新词乙", '[{"text":"新词乙"}]'),
                ],
            )
            conn.commit()

            picker = selector.Picker(2, random.Random(0))
            with (
                mock.patch.object(selector, "COLD_START_LESSON", 9001),
                mock.patch.object(selector, "REVIEW_LESSONS", frozenset({7})),
            ):
                selector._fill_review(conn, picker, 7, user_id=1)

            self.assertEqual({item["target"] for item in picker.items}, {"新词甲", "新词乙"})
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
