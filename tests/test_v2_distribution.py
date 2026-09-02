import importlib.util
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "tests" / "fixtures" / "demo-content-pack"
os.environ.setdefault("DICTATION_CONTENT_ROOT", str(PACK))
MODULE_PATH = ROOT / "shared" / "tools" / "audio_bundle.py"
SPEC = importlib.util.spec_from_file_location("audio_bundle", MODULE_PATH)
audio_bundle = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(audio_bundle)

STAGE_SPEC = importlib.util.spec_from_file_location("stage", ROOT / "tools" / "stage.py")
stage = importlib.util.module_from_spec(STAGE_SPEC)
assert STAGE_SPEC.loader is not None
STAGE_SPEC.loader.exec_module(stage)


FAKE_MP3 = b"\xff\xfb\x90\x64" + b"test-audio" * 4


class V2DistributionTests(unittest.TestCase):
    def make_complete_audio(self, root: Path) -> None:
        for name in audio_bundle.expected_baseline_files():
            path = root / name.removeprefix("audio/")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(FAKE_MP3)

    def test_audio_inventory_is_derived_from_selected_pack(self):
        self.assertEqual(len(audio_bundle.expected_word_files()), 18)
        self.assertEqual(len(audio_bundle.expected_system_files()), 24)
        self.assertEqual(len(audio_bundle.expected_baseline_files()), 42)

    def test_synthetic_pack_can_build_and_verify_audio_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            content = Path(temp) / "pack"
            shutil.copytree(PACK, content)
            self.make_complete_audio(content / "tts")
            data_files = (
                content / "lessons.json",
                content / "knowledge_points.json",
                content / "studio_manifest.json",
            )
            with (
                mock.patch.object(audio_bundle, "CONTENT_ROOT", content),
                mock.patch.object(audio_bundle, "DATA_FILES", data_files),
            ):
                metadata_path = content / "dataset.json"
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                metadata["version"] = "1.2.3"
                metadata["author"] = "pack author"
                metadata_path.write_text(
                    json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                audio_bundle.build_dataset_manifest(content)
                refreshed = json.loads(metadata_path.read_text(encoding="utf-8"))
                self.assertEqual(refreshed["version"], "1.2.3")
                self.assertEqual(refreshed["author"], "pack author")
                audio_bundle.verify_dataset_manifest(content)

    def test_dataset_manifest_honors_pack_relative_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            content = Path(temp) / "pack"
            shutil.copytree(PACK, content)
            data_dir = content / "data"
            audio_dir = content / "audio-baseline"
            data_dir.mkdir()
            shutil.move(str(content / "lessons.json"), data_dir / "lessons.json")
            shutil.move(str(content / "knowledge_points.json"), data_dir / "knowledge_points.json")
            shutil.move(str(content / "studio_manifest.json"), data_dir / "studio_manifest.json")
            self.make_complete_audio(content / "tts")
            shutil.move(str(content / "tts"), audio_dir)

            metadata_path = content / "dataset.json"
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            metadata["paths"] = {
                "lessons": "data/lessons.json",
                "knowledge_points": "data/knowledge_points.json",
                "studio_manifest": "data/studio_manifest.json",
                "tts": "audio-baseline",
                "tts_checksums": "generated/tts.sha256",
            }
            metadata_path.write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            data_files = (
                data_dir / "lessons.json",
                data_dir / "knowledge_points.json",
                data_dir / "studio_manifest.json",
            )
            with (
                mock.patch.object(audio_bundle, "CONTENT_ROOT", content),
                mock.patch.object(audio_bundle, "DATA_FILES", data_files),
            ):
                audio_bundle.build_dataset_manifest(content)
                audio_bundle.verify_dataset_manifest(content)
            refreshed = json.loads(metadata_path.read_text(encoding="utf-8"))
            self.assertEqual(refreshed["paths"]["tts"], "audio-baseline")
            self.assertTrue((content / "generated" / "tts.sha256").is_file())
            with (
                mock.patch.object(audio_bundle, "CONTENT_ROOT", content),
                mock.patch.object(audio_bundle, "DATA_FILES", data_files),
                mock.patch.object(
                    sys,
                    "argv",
                    [
                        "audio_bundle.py",
                        "inventory",
                        "--audio-dir",
                        str(audio_dir),
                    ],
                ),
            ):
                self.assertEqual(audio_bundle.main(), 0)

    def test_baseline_bundle_round_trip_and_dataset_binding(self):
        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)
            source = temp_path / "source"
            target = temp_path / "target"
            bundle = temp_path / "baseline.tar.gz"
            self.make_complete_audio(source)

            audio_bundle.pack_baseline(source, bundle)
            manifest, files = audio_bundle.read_bundle(bundle, audio_bundle.BASELINE_KIND)
            self.assertEqual(manifest["dataset_sha256"], audio_bundle.dataset_sha256())
            self.assertEqual(len(files), 42)

            audio_bundle.install_bundle(
                bundle, target, audio_bundle.BASELINE_KIND, reset_review_state=False
            )
            self.assertEqual(
                (target / "sys" / "intro.mp3").read_bytes(), FAKE_MP3
            )
            audio_bundle.inventory(target)

    def test_human_bundle_only_contains_ledger_files_and_resets_review(self):
        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)
            source = temp_path / "source"
            target = temp_path / "target"
            bundle = temp_path / "human.tar.gz"
            self.make_complete_audio(source)
            self.make_complete_audio(target)

            word_path, text = next(iter(audio_bundle.expected_word_files().items()))
            word_hash = Path(word_path).stem
            (source / ".recorded.json").write_text(
                json.dumps({word_hash: {"text": text, "at": "2026-08-20 12:00:00"}}, ensure_ascii=False),
                encoding="utf-8",
            )
            (source / ".recorded_sys.json").write_text(
                json.dumps({"intro": {"text": "准备听写", "at": "2026-08-20 12:01:00"}}, ensure_ascii=False),
                encoding="utf-8",
            )
            (target / ".checked.json").write_text('{"old": {"status": "checked"}}', encoding="utf-8")
            (target / ".rerecord.json").write_text('{"words": [{"hash": "old"}]}', encoding="utf-8")

            audio_bundle.pack_human(source, bundle)
            manifest, files = audio_bundle.read_bundle(bundle, audio_bundle.HUMAN_KIND)
            self.assertEqual(set(files), {word_path, "audio/sys/intro.mp3"})
            self.assertEqual(set(manifest["state"]["recorded"]), {word_hash})

            audio_bundle.install_bundle(
                bundle, target, audio_bundle.HUMAN_KIND, reset_review_state=True
            )
            recorded = json.loads((target / ".recorded.json").read_text(encoding="utf-8"))
            checked = json.loads((target / ".checked.json").read_text(encoding="utf-8"))
            rerecord = json.loads((target / ".rerecord.json").read_text(encoding="utf-8"))
            self.assertIn(word_hash, recorded)
            self.assertEqual(checked, {})
            self.assertEqual(rerecord, {"created_at": "", "words": []})

    def test_baseline_pack_rejects_recorded_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source"
            bundle = Path(temp) / "baseline.tar.gz"
            self.make_complete_audio(source)
            word_path, text = next(iter(audio_bundle.expected_word_files().items()))
            word_hash = Path(word_path).stem
            (source / ".recorded.json").write_text(
                json.dumps({word_hash: {"text": text}}), encoding="utf-8"
            )
            with self.assertRaises(audio_bundle.BundleError):
                audio_bundle.pack_baseline(source, bundle)

    def test_baseline_install_rejects_existing_human_recordings(self):
        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)
            source = temp_path / "source"
            target = temp_path / "target"
            bundle = temp_path / "baseline.tar.gz"
            self.make_complete_audio(source)
            self.make_complete_audio(target)
            word_path, text = next(iter(audio_bundle.expected_word_files().items()))
            word_hash = Path(word_path).stem
            (target / ".recorded.json").write_text(
                json.dumps({word_hash: {"text": text}}), encoding="utf-8"
            )

            audio_bundle.pack_baseline(source, bundle)
            with self.assertRaises(audio_bundle.BundleError):
                audio_bundle.install_bundle(
                    bundle, target, audio_bundle.BASELINE_KIND, reset_review_state=False
                )

    def test_human_bundle_rejects_forged_recording_ledger(self):
        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)
            bundle = temp_path / "human.tar.gz"
            word_path, text = next(iter(audio_bundle.expected_word_files().items()))
            files = {word_path: FAKE_MP3}
            forged_state = {
                "recorded": {Path(word_path).stem: {"text": f"{text}（伪造）"}},
                "recorded_sys": {},
            }
            manifest = audio_bundle.manifest_for_files(
                audio_bundle.HUMAN_KIND, files, forged_state
            )
            audio_bundle.write_bundle(bundle, manifest, files)

            with self.assertRaises(audio_bundle.BundleError):
                audio_bundle.read_bundle(bundle, audio_bundle.HUMAN_KIND)

    def test_dataset_manifest_rejects_unlisted_mp3(self):
        with tempfile.TemporaryDirectory() as temp:
            content = Path(temp) / "pack"
            shutil.copytree(PACK, content)
            self.make_complete_audio(content / "tts")
            (content / "tts" / "sys" / "unlisted.mp3").write_bytes(FAKE_MP3)
            data_files = (
                content / "lessons.json",
                content / "knowledge_points.json",
                content / "studio_manifest.json",
            )
            with (
                mock.patch.object(audio_bundle, "CONTENT_ROOT", content),
                mock.patch.object(audio_bundle, "DATA_FILES", data_files),
            ):
                audio_bundle.build_dataset_manifest(content)
                with self.assertRaises(audio_bundle.BundleError):
                    audio_bundle.verify_dataset_manifest(content)

    def test_runtime_inventory_rejects_unlisted_mp3(self):
        with tempfile.TemporaryDirectory() as temp:
            audio = Path(temp) / "audio"
            self.make_complete_audio(audio)
            (audio / "w" / "unlisted.mp3").write_bytes(FAKE_MP3)
            with self.assertRaises(audio_bundle.BundleError):
                audio_bundle.inventory(audio)

    def test_public_stage_excludes_runtime_state_and_stale_files(self):
        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)
            source = temp_path / "shared" / "web" / "audio"
            target = temp_path / "v3" / "public" / "audio"
            (source / "w").mkdir(parents=True)
            (source / "sys").mkdir()
            (target / "w").mkdir(parents=True)
            (source / "w" / "word.mp3").write_bytes(FAKE_MP3)
            (source / "sys" / "intro.mp3").write_bytes(FAKE_MP3)
            (source / ".recorded.json").write_text('{"private":true}', encoding="utf-8")
            (source / "w" / "upload.part").write_bytes(b"private")
            (target / "w" / "stale.mp3").write_bytes(FAKE_MP3)

            copied = stage.stage_audio(str(source), str(target))

            self.assertEqual(copied, 2)
            self.assertEqual(
                sorted(path.relative_to(target).as_posix() for path in target.rglob("*")),
                ["sys", "sys/intro.mp3", "w", "w/word.mp3"],
            )

    def test_public_stage_keeps_previous_tree_if_copy_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source"
            target = root / "target"
            (source / "w").mkdir(parents=True)
            (source / "sys").mkdir(parents=True)
            (target / "w").mkdir(parents=True)
            (target / "w" / "old.mp3").write_bytes(FAKE_MP3)
            (source / "w" / "new.mp3").write_bytes(FAKE_MP3)

            with mock.patch.object(stage.shutil, "copy2", side_effect=OSError("copy failed")):
                with self.assertRaisesRegex(OSError, "copy failed"):
                    stage.stage_audio(str(source), str(target))
            self.assertEqual((target / "w" / "old.mp3").read_bytes(), FAKE_MP3)
            self.assertFalse((target / "w" / "new.mp3").exists())

    def test_runtime_stage_preserves_registered_recording_and_removes_stale_baseline(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source"
            target = root / "target"
            (source / "w").mkdir(parents=True)
            (source / "sys").mkdir(parents=True)
            (target / "w").mkdir(parents=True)
            (target / "sys").mkdir(parents=True)
            (source / "w" / "111111111111.mp3").write_bytes(b"baseline")
            (source / "sys" / "intro.mp3").write_bytes(b"prompt")
            (target / "w" / "111111111111.mp3").write_bytes(b"human")
            (target / "w" / "stale.mp3").write_bytes(b"stale")
            (target / ".recorded.json").write_text(
                '{"111111111111": {"text": "词"}}', encoding="utf-8"
            )
            (target / ".recorded_sys.json").write_text("{}", encoding="utf-8")

            copied = stage.install_runtime_audio(source, target)

            self.assertEqual(copied, 2)
            self.assertEqual((target / "w" / "111111111111.mp3").read_bytes(), b"human")
            self.assertEqual((target / "sys" / "intro.mp3").read_bytes(), b"prompt")
            self.assertFalse((target / "w" / "stale.mp3").exists())

    def test_runtime_stage_rejects_orphaned_registered_recording_before_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source"
            target = root / "target"
            (source / "w").mkdir(parents=True)
            (source / "sys").mkdir(parents=True)
            (source / "w" / "111111111111.mp3").write_bytes(b"baseline")
            (source / "w" / "222222222222.mp3").write_bytes(b"new baseline")
            (target / "w").mkdir(parents=True)
            (target / "w" / "stale.mp3").write_bytes(b"human")
            (target / ".recorded.json").write_text(
                '{"aaaaaaaaaaaa": {"text": "旧词"}}', encoding="utf-8"
            )
            (target / ".recorded_sys.json").write_text("{}", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "当前内容包之外"):
                stage.install_runtime_audio(source, target)
            self.assertTrue((target / "w" / "stale.mp3").exists())
            self.assertFalse((target / "w" / "222222222222.mp3").exists())

    def test_fresh_database_has_only_static_seed_data(self):
        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp) / "dictation.db"
            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "shared" / "init_db.py"),
                    "--db",
                    str(db),
                    "--content-root",
                    str(PACK),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            conn = sqlite3.connect(db)
            try:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM lessons").fetchone()[0], 5)
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM knowledge_points").fetchone()[0], 17
                )
                for table in (
                    "user_memory",
                    "dictation_history",
                    "dictation_items",
                    "submission_receipts",
                ):
                    self.assertEqual(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0)
            finally:
                conn.close()

    def test_failed_force_rebuild_preserves_original_database(self):
        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)
            db = temp_path / "dictation.db"
            conn = sqlite3.connect(db)
            conn.execute("CREATE TABLE sentinel (value TEXT)")
            conn.execute("INSERT INTO sentinel VALUES ('keep-me')")
            conn.commit()
            conn.close()

            content = temp_path / "broken-content"
            content.mkdir()
            (content / "lessons.json").write_text("[]", encoding="utf-8")
            (content / "knowledge_points.json").write_text(
                '[{"lesson_seq":9999,"target":"坏数据","category":"词语","options_json":[]}]',
                encoding="utf-8",
            )
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "shared" / "init_db.py"),
                    "--db",
                    str(db),
                    "--content-root",
                    str(content),
                    "--force",
                ],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            conn = sqlite3.connect(db)
            try:
                self.assertEqual(conn.execute("SELECT value FROM sentinel").fetchone()[0], "keep-me")
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
