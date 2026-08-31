import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "selector_d1", ROOT / "v3" / "src" / "selector_d1.py"
)
selector_d1 = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(selector_d1)


RUNTIME_ROW = {
    "pack_id": "demo-pack",
    "cold_start_lesson": 90,
    "initial_lesson": 101,
    "review_lessons_json": "[100,200]",
    "daily_target": 12,
    "review_target": 20,
    "polyphonic_per_lesson": 0,
}


class FakeStatement:
    def __init__(self, results):
        self.results = results

    def bind(self, *params):
        self.params = params
        return self

    async def run(self):
        return SimpleNamespace(results=self.results)


class FakeDB:
    def prepare(self, sql):
        if "FROM content_runtime" in sql:
            return FakeStatement([RUNTIME_ROW])
        if "FROM lessons" in sql:
            return FakeStatement([
                {"lesson_seq": 90, "unit_id": 9},
                {"lesson_seq": 100, "unit_id": 10},
                {"lesson_seq": 101, "unit_id": 10},
                {"lesson_seq": 102, "unit_id": 10},
                {"lesson_seq": 200, "unit_id": 20},
            ])
        raise AssertionError(sql)


class V3ContentRuntimeTests(unittest.TestCase):
    def test_loads_explicit_runtime_from_d1(self):
        runtime = asyncio.run(selector_d1.load_runtime(FakeDB()))
        self.assertEqual(runtime["pack_id"], "demo-pack")
        self.assertEqual(runtime["review_lessons"], frozenset({100, 200}))
        self.assertEqual(runtime["daily_target"], 12)
        self.assertFalse(selector_d1.is_review_lesson(90, runtime))
        self.assertTrue(selector_d1.is_review_lesson(100, runtime))

    def test_regular_lessons_exclude_declared_cold_and_reviews(self):
        runtime = selector_d1.runtime_from_row(RUNTIME_ROW)
        lessons = asyncio.run(selector_d1.regular_lessons(FakeDB(), runtime))
        self.assertEqual(lessons, [101, 102])

    def test_rejects_invalid_review_list(self):
        row = dict(RUNTIME_ROW, review_lessons_json='{"not":"a list"}')
        with self.assertRaisesRegex(RuntimeError, "content_runtime 配置无效"):
            selector_d1.runtime_from_row(row)


if __name__ == "__main__":
    unittest.main()
