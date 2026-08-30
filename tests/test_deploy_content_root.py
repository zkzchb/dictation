from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]


class DeployContentRootTests(unittest.TestCase):
    def test_local_and_vps_services_receive_selected_content_root(self):
        for name in ("local-install.sh", "vps-install.sh"):
            script = (ROOT / "deploy" / name).read_text(encoding="utf-8")
            self.assertRegex(
                script,
                re.compile(r'DICTATION_CONTENT_ROOT=["\']?\$CONTENT_ROOT'),
                name,
            )
            self.assertIn("STATE_ROOT", script, name)

    def test_cloudflare_export_uses_selected_content_root(self):
        script = (ROOT / "deploy" / "cloudflare-deploy.sh").read_text(encoding="utf-8")
        self.assertIn(
            'export_d1.py" --content-root "$CONTENT_ROOT"',
            script,
        )

    def test_vps_installer_preserves_the_configured_ssh_port(self):
        script = (ROOT / "deploy" / "vps-install.sh").read_text(encoding="utf-8")
        self.assertIn('ufw allow "$SSH_PORT/tcp"', script)
        self.assertIn("rsync cron ca-certificates", script)

    def test_vps_installer_migrates_only_a_dedicated_runtime_account(self):
        script = (ROOT / "deploy" / "vps-install.sh").read_text(encoding="utf-8")
        self.assertIn('((account_uid < 1000))', script)
        self.assertIn('/usr/sbin/nologin|/sbin/nologin|/bin/false|/usr/bin/false', script)
        self.assertIn('"$APP_ROOT"|"$STATE_ROOT"', script)
        self.assertIn('usermod --home "$STATE_ROOT" "$APP_USER"', script)
        self.assertIn('chmod -R a+rX "$APP_ROOT/v2" "$APP_ROOT/shared"', script)


if __name__ == "__main__":
    unittest.main()
