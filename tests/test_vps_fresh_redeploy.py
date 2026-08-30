from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "deploy" / "vps-fresh-redeploy.sh"


class VpsFreshRedeployTests(unittest.TestCase):
    def _env_file(self, directory: Path, *, app_root: str = "/opt/dictation") -> Path:
        path = directory / "vps.env"
        path.write_text(
            textwrap.dedent(
                f"""\
                V2_SITE_ADDRESSES=http://10.0.0.10
                BASIC_AUTH_USER=dictation-test
                BASIC_AUTH_PASSWORD='test-password-1234'
                APP_ROOT={app_root}
                CONTENT_ROOT=/opt/dictation-content/packs/zh-cn/primary-3a
                STATE_ROOT=/var/lib/dictation
                V2_DEPENDENCY_SOURCE=online
                V2_PORT=8889
                APP_USER=dictation
                STUDIO_ENABLED=0
                """
            ),
            encoding="utf-8",
        )
        path.chmod(0o600)
        return path

    def test_shell_syntax_and_help(self):
        subprocess.run(["bash", "-n", str(SCRIPT)], check=True)
        script = SCRIPT.read_text(encoding="utf-8")
        helper = script.split("<<'REMOTE'\n", 1)[1].split("\nREMOTE\n", 1)[0]
        subprocess.run(["bash", "-n"], input=helper, text=True, check=True)
        result = subprocess.run(
            ["bash", str(SCRIPT), "--help"],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertIn("--preflight-only", result.stdout)
        self.assertIn("--allow-caddy-replace", result.stdout)

    def test_legacy_service_account_home_is_migrated_and_rollback_safe(self):
        script = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('((app_uid < 1000))', script)
        self.assertIn('/usr/sbin/nologin|/sbin/nologin|/bin/false|/usr/bin/false', script)
        self.assertIn('"$APP_ROOT"|"$STATE_ROOT"', script)
        self.assertIn('dictation_account_home=%s', script)
        self.assertIn('usermod --home "$STATE_ROOT" dictation', script)
        self.assertIn('usermod --home "$original_account_home" dictation', script)
        self.assertLess(
            script.index('printf \'dictation_account_home=%s'),
            script.index('phase_destroy()'),
        )

    def test_validate_env_only_accepts_fixed_safe_layout(self):
        with tempfile.TemporaryDirectory() as temp:
            env_file = self._env_file(Path(temp))
            result = subprocess.run(
                [
                    "bash",
                    str(SCRIPT),
                    "--validate-env-only",
                    "--env-file",
                    str(env_file),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertIn("通过本地安全校验", result.stdout)
            self.assertNotIn("test-password", result.stdout)

    def test_validate_env_only_rejects_different_deletion_root(self):
        with tempfile.TemporaryDirectory() as temp:
            env_file = self._env_file(Path(temp), app_root="/srv/dictation")
            result = subprocess.run(
                [
                    "bash",
                    str(SCRIPT),
                    "--validate-env-only",
                    "--env-file",
                    str(env_file),
                ],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("APP_ROOT 必须为 /opt/dictation", result.stderr)

    def test_validate_env_only_rejects_group_readable_secret(self):
        with tempfile.TemporaryDirectory() as temp:
            env_file = self._env_file(Path(temp))
            env_file.chmod(0o640)
            result = subprocess.run(
                [
                    "bash",
                    str(SCRIPT),
                    "--validate-env-only",
                    "--env-file",
                    str(env_file),
                ],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("chmod 600", result.stderr)


if __name__ == "__main__":
    unittest.main()
