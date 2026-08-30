import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from shared.content_pack import load_content_pack


ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "content" / "demo-zh-cn"


class DemoContentPackTests(unittest.TestCase):
    def test_original_demo_pack_is_self_contained_and_valid(self):
        pack = load_content_pack(PACK)
        self.assertEqual(pack.id, "demo-zh-cn")
        self.assertEqual(len(pack.lessons), 5)
        self.assertEqual(len(pack.knowledge_points), 17)
        self.assertEqual(len(pack.studio_manifest), 18)
        self.assertEqual(pack.runtime.initial_lesson, 9101)
        self.assertEqual(pack.runtime.review_lessons, frozenset({9100}))
        self.assertNotIn("tts", pack.paths)

    def test_demo_pack_builds_a_clean_sqlite_database(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "dictation.db"
            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "shared" / "init_db.py"),
                    "--content-root",
                    str(PACK),
                    "--db",
                    str(database),
                ],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            connection = sqlite3.connect(database)
            try:
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM lessons").fetchone()[0], 5
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT current_lesson_seq FROM user_progress WHERE user_id = 1"
                    ).fetchone()[0],
                    9101,
                )
                self.assertEqual(
                    connection.execute("PRAGMA integrity_check").fetchone()[0], "ok"
                )
            finally:
                connection.close()


if __name__ == "__main__":
    unittest.main()
