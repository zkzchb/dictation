from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class DeployContentRootTests(unittest.TestCase):
    def test_local_and_vps_services_receive_selected_content_root(self):
        for name in ("local-install.sh", "vps-install.sh"):
            script = (ROOT / "deploy" / name).read_text(encoding="utf-8")
            self.assertIn("DICTATION_CONTENT_ROOT=$CONTENT_ROOT", script, name)

    def test_cloudflare_export_uses_selected_content_root(self):
        script = (ROOT / "deploy" / "cloudflare-deploy.sh").read_text(encoding="utf-8")
        self.assertIn(
            'export_d1.py" --content-root "$CONTENT_ROOT"',
            script,
        )


if __name__ == "__main__":
    unittest.main()
