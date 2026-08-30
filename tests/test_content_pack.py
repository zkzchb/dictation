import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from shared.content_pack import ContentPackError, load_content_pack


class ContentPackTests(unittest.TestCase):
    def make_pack(self, root: Path, *, runtime=None) -> None:
        lessons = [
            {"lesson_seq": 100, "unit_id": 10, "unit_name": "演示单元", "lesson_name": "复习"},
            {"lesson_seq": 101, "unit_id": 10, "unit_name": "演示单元", "lesson_name": "第一课"},
        ]
        points = [
            {"lesson_seq": 101, "target": "春", "category": "生字", "options_json": [{"text": "春天", "pinyin": "chūn tiān"}]},
        ]
        text = "春天"
        studio = [{"text": text, "pinyin": "chūn tiān", "hash": hashlib.md5(text.encode()).hexdigest()[:12]}]
        files = {
            "lessons.json": lessons,
            "knowledge_points.json": points,
            "studio_manifest.json": studio,
        }
        root.mkdir(parents=True)
        for name, value in files.items():
            (root / name).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

        digest = hashlib.sha256()
        for name in files:
            digest.update(name.encode())
            digest.update(b"\0")
            digest.update((root / name).read_bytes())
            digest.update(b"\0")
        metadata = {
            "schema_version": 1,
            "id": "demo-pack",
            "display_name": "演示内容包",
            "language": "zh-CN",
            "subject": "chinese",
            "paths": {
                "lessons": "lessons.json",
                "knowledge_points": "knowledge_points.json",
                "studio_manifest": "studio_manifest.json",
            },
            "counts": {
                "lessons": 2,
                "knowledge_points": 1,
                "studio_words": 1,
                "tts_words": 1,
                "tts_system": 0,
                "categories": {"生字": 1},
            },
            "sha256": {
                "lessons": hashlib.sha256((root / "lessons.json").read_bytes()).hexdigest(),
                "knowledge_points": hashlib.sha256((root / "knowledge_points.json").read_bytes()).hexdigest(),
                "studio_manifest": hashlib.sha256((root / "studio_manifest.json").read_bytes()).hexdigest(),
                "dataset": digest.hexdigest(),
            },
        }
        if runtime is not None:
            metadata["runtime"] = runtime
        (root / "dataset.json").write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")

    def test_loads_legacy_runtime_defaults(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "pack"
            self.make_pack(root)
            pack = load_content_pack(root)
            self.assertEqual(pack.id, "demo-pack")
            self.assertEqual(pack.runtime.initial_lesson, 101)
            self.assertEqual(pack.runtime.review_lessons, frozenset({100}))
            self.assertIsNone(pack.runtime.cold_start_lesson)
            self.assertEqual(pack.runtime.daily_target, 30)

    def test_explicit_runtime_is_authoritative(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "pack"
            self.make_pack(root, runtime={
                "initial_lesson": 101,
                "review_lessons": [100],
                "daily_target": 12,
                "review_target": 20,
                "polyphonic_per_lesson": 0,
            })
            pack = load_content_pack(root)
            self.assertEqual(pack.runtime.daily_target, 12)
            self.assertEqual(pack.runtime.review_target, 20)
            self.assertEqual(pack.runtime.polyphonic_per_lesson, 0)

    def test_rejects_boolean_initial_lesson(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "pack"
            self.make_pack(root, runtime={
                "initial_lesson": True,
                "review_lessons": [100],
            })
            with self.assertRaisesRegex(ContentPackError, "initial_lesson"):
                load_content_pack(root)

    def test_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "pack"
            self.make_pack(root)
            metadata = json.loads((root / "dataset.json").read_text(encoding="utf-8"))
            metadata["paths"]["lessons"] = "../lessons.json"
            (root / "dataset.json").write_text(json.dumps(metadata), encoding="utf-8")
            with self.assertRaisesRegex(ContentPackError, "不能越出内容包"):
                load_content_pack(root, verify_hashes=False)

    def test_rejects_unknown_lesson_reference(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "pack"
            self.make_pack(root)
            points = json.loads((root / "knowledge_points.json").read_text(encoding="utf-8"))
            points[0]["lesson_seq"] = 999
            (root / "knowledge_points.json").write_text(json.dumps(points), encoding="utf-8")
            with self.assertRaisesRegex(ContentPackError, "不存在的 lesson_seq"):
                load_content_pack(root, verify_hashes=False)

    def test_rejects_mismatched_counts(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "pack"
            self.make_pack(root)
            metadata = json.loads((root / "dataset.json").read_text(encoding="utf-8"))
            metadata["counts"]["lessons"] = 99
            (root / "dataset.json").write_text(json.dumps(metadata), encoding="utf-8")
            with self.assertRaisesRegex(ContentPackError, "counts.lessons 不一致"):
                load_content_pack(root, verify_hashes=False)

    def test_legacy_pack_without_ids_gets_stable_positional_ids(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "pack"
            self.make_pack(root)
            pack = load_content_pack(root)
            self.assertEqual([item["id"] for item in pack.knowledge_points], [1])

    def test_database_uses_pack_initial_lesson(self):
        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)
            root = temp_path / "pack"
            self.make_pack(root, runtime={
                "initial_lesson": 101,
                "review_lessons": [100],
                "daily_target": 12,
                "review_target": 20,
                "polyphonic_per_lesson": 0,
            })
            db = temp_path / "dictation.db"
            script = Path(__file__).resolve().parents[1] / "shared" / "init_db.py"
            subprocess.run(
                [sys.executable, str(script), "--db", str(db), "--content-root", str(root)],
                check=True,
                capture_output=True,
                text=True,
            )
            conn = sqlite3.connect(db)
            try:
                self.assertEqual(
                    conn.execute("SELECT current_lesson_seq FROM user_progress").fetchone()[0],
                    101,
                )
            finally:
                conn.close()

    def test_d1_export_uses_pack_runtime_and_initial_lesson(self):
        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)
            root = temp_path / "pack"
            self.make_pack(root, runtime={
                "initial_lesson": 101,
                "review_lessons": [100],
                "daily_target": 12,
                "review_target": 20,
                "polyphonic_per_lesson": 0,
            })
            seed = temp_path / "0002_seed.sql"
            runtime_sql = temp_path / "runtime.sql"
            script = Path(__file__).resolve().parents[1] / "shared" / "tools" / "export_d1.py"
            subprocess.run(
                [
                    sys.executable,
                    str(script),
                    "--content-root",
                    str(root),
                    "--output",
                    str(seed),
                    "--runtime-output",
                    str(runtime_sql),
                ],
                check=True,
                capture_output=True,
                text=True,
            )

            repo = Path(__file__).resolve().parents[1]
            conn = sqlite3.connect(":memory:")
            try:
                conn.executescript((repo / "v3" / "migrations" / "0001_initial.sql").read_text())
                conn.executescript(seed.read_text(encoding="utf-8"))
                conn.executescript((repo / "v3" / "migrations" / "0003_content_runtime.sql").read_text())
                conn.executescript(runtime_sql.read_text(encoding="utf-8"))
                self.assertEqual(
                    conn.execute("SELECT current_lesson_seq FROM user_progress").fetchone()[0],
                    101,
                )
                self.assertEqual(
                    conn.execute(
                        "SELECT pack_id, review_lessons_json, daily_target "
                        "FROM content_runtime"
                    ).fetchone(),
                    ("demo-pack", "[100]", 12),
                )
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
