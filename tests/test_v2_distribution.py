import importlib.util
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "shared" / "tools" / "audio_bundle.py"
SPEC = importlib.util.spec_from_file_location("audio_bundle", MODULE_PATH)
audio_bundle = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(audio_bundle)

WHEELHOUSE_SPEC = importlib.util.spec_from_file_location(
    "verify_wheelhouse", ROOT / "shared" / "tools" / "verify_wheelhouse.py"
)
verify_wheelhouse = importlib.util.module_from_spec(WHEELHOUSE_SPEC)
assert WHEELHOUSE_SPEC.loader is not None
WHEELHOUSE_SPEC.loader.exec_module(verify_wheelhouse)

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

    def test_canonical_dataset_counts_are_frozen(self):
        self.assertEqual(len(audio_bundle.expected_word_files()), 869)
        self.assertEqual(len(audio_bundle.expected_system_files()), 25)
        self.assertEqual(len(audio_bundle.expected_baseline_files()), 894)

    def test_frozen_wheelhouse_file_set_and_hashes_are_valid(self):
        count = verify_wheelhouse.verify_wheelhouse(
            ROOT / "v2" / "wheelhouse", check_platform=False
        )
        self.assertEqual(count, 16)

    def test_frozen_wheelhouse_rejects_modified_wheel(self):
        with tempfile.TemporaryDirectory() as temp:
            wheelhouse = Path(temp) / "wheelhouse"
            shutil.copytree(ROOT / "v2" / "wheelhouse", wheelhouse)
            target = next(wheelhouse.glob("*.whl"))
            target.write_bytes(target.read_bytes() + b"tampered")
            with self.assertRaises(verify_wheelhouse.WheelhouseError):
                verify_wheelhouse.verify_wheelhouse(
                    wheelhouse, check_platform=False
                )

    def test_distribution_json_matches_legacy_shared_sources(self):
        pairs = (
            (ROOT / "chinese" / "3a" / "lessons.json", ROOT / "shared" / "data" / "lessons_grade3.json"),
            (ROOT / "chinese" / "3a" / "knowledge_points.json", ROOT / "shared" / "data" / "kp_grade3.json"),
            (ROOT / "chinese" / "3a" / "studio_manifest.json", ROOT / "shared" / "web" / "studio_manifest.json"),
        )
        for distribution, legacy in pairs:
            self.assertEqual(distribution.read_bytes(), legacy.read_bytes())

    def test_repository_dataset_manifest_and_audio_are_valid(self):
        audio_bundle.verify_dataset_manifest(ROOT / "chinese" / "3a")

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
            self.assertEqual(len(files), 894)

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
            content = Path(temp) / "3a"
            shutil.copytree(ROOT / "chinese" / "3a", content)
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
                    str(ROOT / "chinese" / "3a"),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            conn = sqlite3.connect(db)
            try:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM lessons").fetchone()[0], 43)
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM knowledge_points").fetchone()[0], 814
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
