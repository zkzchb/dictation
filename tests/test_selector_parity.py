import asyncio
import importlib.util
import os
from pathlib import Path
import random
import sqlite3
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "tests" / "fixtures" / "demo-content-pack"
os.environ.setdefault("DICTATION_CONTENT_ROOT", str(PACK))

from shared import selector


SPEC = importlib.util.spec_from_file_location(
    "selector_d1_parity", ROOT / "v3" / "src" / "selector_d1.py"
)
selector_d1 = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(selector_d1)


class SQLiteD1Statement:
    def __init__(self, conn, sql):
        self.conn = conn
        self.sql = sql
        self.params = ()

    def bind(self, *params):
        self.params = params
        return self

    async def run(self):
        cursor = self.conn.execute(self.sql, self.params)
        results = [dict(row) for row in cursor.fetchall()] if cursor.description else []
        return SimpleNamespace(results=results)


class SQLiteD1:
    def __init__(self, conn):
        self.conn = conn

    def prepare(self, sql):
        return SQLiteD1Statement(self.conn, sql)


class SelectorParityTests(unittest.TestCase):
    def test_sqlite_and_d1_selectors_match_for_daily_and_review(self):
        with tempfile.TemporaryDirectory() as temp:
            db_path = Path(temp) / "dictation.db"
            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "shared" / "init_db.py"),
                    "--db",
                    str(db_path),
                    "--content-root",
                    str(PACK),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            try:
                runtime = {
                    "pack_id": selector._CONTENT_PACK.id,
                    "cold_start_lesson": selector.COLD_START_LESSON,
                    "initial_lesson": selector._CONTENT_PACK.runtime.initial_lesson,
                    "review_lessons": selector.REVIEW_LESSONS,
                    "daily_target": selector.TARGET_DAILY,
                    "review_target": selector.TARGET_REVIEW,
                    "polyphonic_per_lesson": selector.POLY_PER_LESSON,
                }
                for lesson_seq in (9101, 9100):
                    local = selector.build_word_list(
                        conn, lesson_seq, rng=random.Random(20260829)
                    )
                    edge = asyncio.run(
                        selector_d1.build_word_list(
                            SQLiteD1(conn),
                            lesson_seq,
                            rng=random.Random(20260829),
                            runtime=runtime,
                        )
                    )
                    self.assertEqual(edge, local)
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
