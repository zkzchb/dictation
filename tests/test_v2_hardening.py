import asyncio
import base64
import json
import os
import sqlite3
import subprocess
import tempfile
import threading
import unittest

from fastapi import HTTPException

import v2.main as main


SCHEMA = """
CREATE TABLE lessons (lesson_seq INTEGER PRIMARY KEY);
CREATE TABLE knowledge_points (
    id INTEGER PRIMARY KEY, lesson_seq INTEGER, category TEXT
);
CREATE TABLE dictation_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
    dictation_type TEXT NOT NULL, scope_id INTEGER NOT NULL,
    score REAL DEFAULT 0, poly_ids TEXT DEFAULT '', created_at TEXT
);
CREATE TABLE dictation_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT, history_id INTEGER NOT NULL,
    kp_id INTEGER NOT NULL, is_correct INTEGER NOT NULL
);
CREATE TABLE user_memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
    kp_id INTEGER NOT NULL, status INTEGER DEFAULT 0,
    error_count INTEGER DEFAULT 0, correct_streak INTEGER DEFAULT 0,
    last_tested_date TEXT, next_review_date TEXT, UNIQUE(user_id, kp_id)
);
"""


class V2HardeningTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        self.originals = {
            name: getattr(main, name) for name in (
                "DB_PATH", "STUDIO_AUDIO_DIR", "STUDIO_LEDGER",
                "STUDIO_CHECK_LEDGER", "STUDIO_RERECORD_LIST", "SYS_AUDIO_DIR",
                "SYS_LEDGER",
            )
        }
        main.DB_PATH = os.path.join(root, "dictation.db")
        main.STUDIO_AUDIO_DIR = os.path.join(root, "audio", "w")
        main.STUDIO_LEDGER = os.path.join(root, "audio", ".recorded.json")
        main.STUDIO_CHECK_LEDGER = os.path.join(root, "audio", ".checked.json")
        main.STUDIO_RERECORD_LIST = os.path.join(root, "audio", ".rerecord.json")
        main.SYS_AUDIO_DIR = os.path.join(root, "audio", "sys")
        main.SYS_LEDGER = os.path.join(root, "audio", ".recorded_sys.json")

        conn = sqlite3.connect(main.DB_PATH)
        conn.executescript(SCHEMA)
        conn.execute("INSERT INTO lessons VALUES (3111)")
        conn.execute("INSERT INTO knowledge_points VALUES (1,3111,'词语')")
        conn.execute(
            "INSERT INTO knowledge_points VALUES (2,3111,?)", (main.selector.CAT_POLY,)
        )
        conn.commit()
        conn.close()

    def tearDown(self):
        for name, value in self.originals.items():
            setattr(main, name, value)
        self.tmp.cleanup()

    def test_recording_ledger_concurrent_updates_are_not_lost(self):
        errors = []

        def record(i):
            try:
                text = f"并发词{i}"
                main._mark_recorded([(main.word_hash(text), text)])
            except Exception as exc:  # pragma: no cover - assertion reports details
                errors.append(exc)

        threads = [threading.Thread(target=record, args=(i,)) for i in range(64)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(errors, [])
        self.assertEqual(len(main._load_ledger()), 64)

    def test_corrupt_primary_ledger_uses_last_valid_backup(self):
        main._save_json(main.STUDIO_LEDGER, {"first": 1})
        main._save_json(main.STUDIO_LEDGER, {"second": 2})
        with open(main.STUDIO_LEDGER, "w", encoding="utf-8") as stream:
            stream.write("{")
        self.assertEqual(main._load_json(main.STUDIO_LEDGER, {}), {"first": 1})

    def test_save_rejects_hash_that_does_not_match_word(self):
        payload = {
            "items": [{
                "hash": main.word_hash("另一个词"), "text": "当前词",
                "audio": base64.b64encode(b"ID3-test").decode(),
            }]
        }
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(main.studio_save(payload))
        self.assertEqual(ctx.exception.status_code, 400)

    def test_system_webm_is_transcoded_to_real_mp3(self):
        source = os.path.join(self.tmp.name, "prompt.webm")
        subprocess.run(
            [
                "ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i",
                "sine=frequency=440:duration=0.2", "-c:a", "libopus", "-y", source,
            ],
            check=True,
        )
        with open(source, "rb") as stream:
            encoded = base64.b64encode(stream.read()).decode()
        result = asyncio.run(main.studio_save_sys({"key": "intro", "audio": encoded}))
        self.assertEqual(result["saved"], 1)

        dest = os.path.join(main.SYS_AUDIO_DIR, "intro.mp3")
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=format_name",
             "-of", "default=nw=1:nk=1", dest],
            check=True, capture_output=True, text=True,
        )
        self.assertIn("mp3", probe.stdout)

    def test_submission_retry_is_idempotent(self):
        payload = main.SubmitPayload(
            user_id=1, submission_id="test_submission_001",
            dictation_type="daily", scope_id=3111,
            results=[main.WordResult(kp_id=1, is_correct=True)], poly_ids=[2],
        )
        first = main.submit_dictation(payload)
        second = main.submit_dictation(payload)
        self.assertEqual(first, second)

        conn = sqlite3.connect(main.DB_PATH)
        history_count = conn.execute("SELECT COUNT(*) FROM dictation_history").fetchone()[0]
        streak = conn.execute("SELECT correct_streak FROM user_memory").fetchone()[0]
        conn.close()
        self.assertEqual(history_count, 1)
        self.assertEqual(streak, 1)

    def test_submission_rejects_client_controlled_user(self):
        payload = main.SubmitPayload(
            user_id=99, submission_id="test_submission_002",
            dictation_type="daily", scope_id=3111,
            results=[main.WordResult(kp_id=1, is_correct=True)],
        )
        with self.assertRaises(HTTPException) as ctx:
            main.submit_dictation(payload)
        self.assertEqual(ctx.exception.status_code, 400)

    def test_health_reports_static_database_counts(self):
        result = main.health()
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["database"], {"lessons": 1, "knowledge_points": 2})


if __name__ == "__main__":
    unittest.main()
