import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from shared.sync_content import ContentSyncError, sync_content


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "demo-content-pack"


def refresh_metadata(pack: Path) -> None:
    lessons = json.loads((pack / "lessons.json").read_text(encoding="utf-8"))
    points = json.loads((pack / "knowledge_points.json").read_text(encoding="utf-8"))
    studio = json.loads((pack / "studio_manifest.json").read_text(encoding="utf-8"))
    metadata = json.loads((pack / "dataset.json").read_text(encoding="utf-8"))
    categories = {}
    for point in points:
        categories[point["category"]] = categories.get(point["category"], 0) + 1
    metadata["version"] = "1.1.0"
    metadata["counts"].update(
        {
            "lessons": len(lessons),
            "knowledge_points": len(points),
            "studio_words": len(studio),
            "tts_words": len(studio),
            "categories": dict(sorted(categories.items())),
        }
    )
    digest = hashlib.sha256()
    for key, name in (
        ("lessons", "lessons.json"),
        ("knowledge_points", "knowledge_points.json"),
        ("studio_manifest", "studio_manifest.json"),
    ):
        data = (pack / name).read_bytes()
        metadata["sha256"][key] = hashlib.sha256(data).hexdigest()
        digest.update(name.encode())
        digest.update(b"\0")
        digest.update(data)
        digest.update(b"\0")
    metadata["sha256"]["dataset"] = digest.hexdigest()
    (pack / "dataset.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


class ContentSyncTests(unittest.TestCase):
    def prepare_pack(self, destination: Path) -> None:
        shutil.copytree(FIXTURE, destination)
        points_path = destination / "knowledge_points.json"
        points = json.loads(points_path.read_text(encoding="utf-8"))
        for point_id, point in enumerate(points, 1):
            point["id"] = point_id
        points_path.write_text(
            json.dumps(points, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        refresh_metadata(destination)

    def build_database(self, pack: Path, database: Path) -> None:
        subprocess.run(
            [
                sys.executable,
                str(ROOT / "shared" / "init_db.py"),
                "--db",
                str(database),
                "--content-root",
                str(pack),
            ],
            check=True,
            capture_output=True,
            text=True,
        )

    def test_additive_update_preserves_learning_history(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pack = root / "pack"
            database = root / "dictation.db"
            self.prepare_pack(pack)
            self.build_database(pack, database)

            conn = sqlite3.connect(database)
            conn.execute(
                "INSERT INTO dictation_history "
                "(user_id, dictation_type, scope_id, score, poly_ids) "
                "VALUES (1, 'daily', 9101, 100, '')"
            )
            conn.commit()
            conn.close()

            lessons = json.loads((pack / "lessons.json").read_text(encoding="utf-8"))
            lessons.append(
                {
                    "lesson_seq": 9301,
                    "unit_id": 3,
                    "unit_name": "第三单元",
                    "lesson_title": "第四课",
                    "lesson_name": "海边散步",
                }
            )
            (pack / "lessons.json").write_text(
                json.dumps(lessons, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            points = json.loads((pack / "knowledge_points.json").read_text(encoding="utf-8"))
            points.append(
                {
                    "id": 18,
                    "lesson_seq": 9301,
                    "target": "海风",
                    "category": "词语",
                    "options_json": [{"text": "海风", "pinyin": "hǎi fēng"}],
                }
            )
            (pack / "knowledge_points.json").write_text(
                json.dumps(points, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            studio = json.loads((pack / "studio_manifest.json").read_text(encoding="utf-8"))
            studio.append(
                {
                    "text": "海风",
                    "pinyin": "hǎi fēng",
                    "hash": hashlib.md5("海风".encode()).hexdigest()[:12],
                }
            )
            (pack / "studio_manifest.json").write_text(
                json.dumps(studio, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            refresh_metadata(pack)

            result = sync_content(database, pack)
            self.assertEqual(result["lessons_added"], 1)
            self.assertEqual(result["knowledge_points_added"], 1)
            conn = sqlite3.connect(database)
            try:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM lessons").fetchone()[0], 6)
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM knowledge_points").fetchone()[0], 18
                )
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM dictation_history").fetchone()[0], 1
                )
            finally:
                conn.close()

    def test_removing_a_stable_id_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pack = root / "pack"
            database = root / "dictation.db"
            self.prepare_pack(pack)
            self.build_database(pack, database)
            points_path = pack / "knowledge_points.json"
            points = json.loads(points_path.read_text(encoding="utf-8"))
            points_path.write_text(
                json.dumps(points[:-1], ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            studio_path = pack / "studio_manifest.json"
            studio = json.loads(studio_path.read_text(encoding="utf-8"))
            studio_path.write_text(
                json.dumps(studio[:-1], ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            refresh_metadata(pack)
            with self.assertRaisesRegex(ContentSyncError, "只允许追加"):
                sync_content(database, pack)


if __name__ == "__main__":
    unittest.main()
