import ast
import asyncio
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from urllib.parse import urljoin

from fastapi import FastAPI


ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = ROOT / "shared" / "web"
PACK = ROOT / "tests" / "fixtures" / "demo-content-pack"
os.environ.setdefault("DICTATION_CONTENT_ROOT", str(PACK))

import v2.main as v2_main


async def asgi_get(app, path: str):
    messages = []
    request_sent = False

    async def receive():
        nonlocal request_sent
        if not request_sent:
            request_sent = True
            return {"type": "http.request", "body": b"", "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message):
        messages.append(message)

    await app(
        {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": b"",
            "root_path": "",
            "headers": [(b"host", b"example.test")],
            "client": ("127.0.0.1", 12345),
            "server": ("example.test", 80),
        },
        receive,
        send,
    )
    status = next(
        message["status"] for message in messages
        if message["type"] == "http.response.start"
    )
    body = b"".join(
        message.get("body", b"") for message in messages
        if message["type"] == "http.response.body"
    )
    return status, body


class SubpathURLTests(unittest.TestCase):
    def test_frontend_app_urls_are_not_root_relative(self):
        pattern = re.compile(
            r'''["'`](?:/api(?:/|[?"'`])|/audio/|/playback_config\.json|'''
            r'''/studio(?:2)?(?:\.html)?|/check\.html)'''
        )
        for page in sorted(WEB_ROOT.glob("*.html")):
            with self.subTest(page=page.name):
                matches = pattern.findall(page.read_text(encoding="utf-8"))
                self.assertEqual(matches, [], f"{page.name}: {matches}")

    def test_backend_client_urls_are_not_root_relative(self):
        forbidden = ("/audio/", "/studio2.html", "/check.html")
        for relative in (
            "shared/audio_catalog.py",
            "v2/main.py",
            "v3/src/worker.py",
        ):
            source = (ROOT / relative).read_text(encoding="utf-8")
            tree = ast.parse(source, filename=relative)
            strings = [
                node.value
                for node in ast.walk(tree)
                if isinstance(node, ast.Constant) and isinstance(node.value, str)
            ]
            offenders = [value for value in strings if value.startswith(forbidden)]
            self.assertEqual(offenders, [], f"{relative}: {offenders}")

    def test_relative_urls_resolve_at_root_and_under_dictation(self):
        cases = {
            "http://example.test/": "http://example.test/api/health",
            "http://example.test/index.html": "http://example.test/api/health",
            "http://example.test/studio": "http://example.test/api/health",
            "http://example.test/check.html": "http://example.test/api/health",
            "http://example.test/dictation/": "http://example.test/dictation/api/health",
            "http://example.test/dictation/index.html": "http://example.test/dictation/api/health",
            "http://example.test/dictation/studio": "http://example.test/dictation/api/health",
            "http://example.test/dictation/check.html": "http://example.test/dictation/api/health",
        }
        for page, expected in cases.items():
            with self.subTest(page=page):
                self.assertEqual(urljoin(page, "./api/health"), expected)

    def test_v2_app_operates_when_mounted_under_dictation(self):
        with tempfile.TemporaryDirectory() as temp:
            database = Path(temp) / "dictation.db"
            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "shared" / "init_db.py"),
                    "--db",
                    str(database),
                    "--content-root",
                    str(PACK),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            original_db = v2_main.DB_PATH
            v2_main.DB_PATH = str(database)
            try:
                proxy = FastAPI()
                proxy.mount("/dictation", v2_main.app)
                for path in (
                    "/dictation/",
                    "/dictation/playback_config.json",
                    "/dictation/check.html",
                    "/dictation/studio",
                ):
                    with self.subTest(path=path):
                        status, _body = asyncio.run(asgi_get(proxy, path))
                        self.assertEqual(status, 200)

                for path in ("/", "/playback_config.json", "/check.html", "/studio"):
                    with self.subTest(root_path=path):
                        status, _body = asyncio.run(asgi_get(v2_main.app, path))
                        self.assertEqual(status, 200)

                status, body = asyncio.run(asgi_get(proxy, "/dictation/api/health"))
                self.assertEqual(status, 200)
                health = json.loads(body)
                self.assertEqual(
                    health["database"],
                    {"lessons": 5, "knowledge_points": 17},
                )
                status, body = asyncio.run(
                    asgi_get(proxy, "/dictation/api/studio/syswords")
                )
                self.assertEqual(status, 200)
                system_words = json.loads(body)
                self.assertTrue(
                    all(
                        item["url"].startswith("./audio/sys/")
                        for item in system_words["words"]
                    )
                )
            finally:
                v2_main.DB_PATH = original_db


if __name__ == "__main__":
    unittest.main()
