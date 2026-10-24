"""Offline tests for the isolated OpenClaw demo bootstrap. No Docker/OAuth required."""
from __future__ import annotations
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch


BASE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("clawvoice_manager", BASE / "manage.py")
manager = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(manager)


class FoundationBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "demo"
        self.root.mkdir()
        shutil.copytree(BASE / "workspace", self.root / "workspace")
        shutil.copytree(BASE / "config", self.root / "config")
        self.orig_root, self.orig_local = manager.ROOT, manager.LOCAL
        manager.ROOT, manager.LOCAL = self.root, self.root / ".local"
        self.addCleanup(self.restore)

    def restore(self):
        manager.ROOT, manager.LOCAL = self.orig_root, self.orig_local

    def test_private_init_and_idempotence(self):
        manager.initialize("America/Toronto", "Example Assistant", 19501)
        env_path = self.root / ".local/runtime.env"
        original = env_path.read_text()
        config = json.loads((self.root / ".local/state/openclaw.json").read_text())
        self.assertEqual(config["agents"]["defaults"]["model"]["primary"], "openai/gpt-6.1-sol")
        self.assertEqual(config["agents"]["defaults"]["model"]["fallbacks"], [])
        self.assertEqual(config["agents"]["entries"]["tari"]["name"], "Example Assistant")
        self.assertEqual(config["gateway"]["controlUi"]["allowedOrigins"], [
            "http://localhost:19501", "http://127.0.0.1:19501"])
        self.assertEqual((self.root / ".local/state/workspace/IDENTITY.md").read_text().count(
            "Example Assistant"), 1)
        self.assertIn("TARI_CALENDAR_WRITES=0", original)
        self.assertNotIn("OPENAI_API_KEY=", original)
        self.assertTrue((self.root / ".local/state/workspace/bin/tari-google").exists())
        self.assertEqual(env_path.stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.root / ".local/state/workspace/bin/tari-google").stat().st_mode & 0o777, 0o700)
        for directory in (self.root / ".local").rglob("*"):
            if directory.is_dir():
                self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
        manager.initialize("UTC", "Different", 19502)
        self.assertEqual(env_path.read_text(), original)
        self.assertEqual(json.loads((self.root / ".local/state/openclaw.json").read_text()),
                         config)
        manager.validate_policy()

    def test_reject_bad_port_and_name(self):
        with self.assertRaises(RuntimeError):
            manager.initialize("America/Toronto", "../production", 19501)
        with self.assertRaises(RuntimeError):
            manager.initialize("America/Toronto", "Demo", 100)

    def test_refuse_symlink_state(self):
        (self.root / ".local").symlink_to(Path(self.temp.name), target_is_directory=True)
        with self.assertRaises(RuntimeError):
            manager.initialize("UTC", "Demo", 19501)

    def test_runtime_config_isolated(self):
        manager.initialize("UTC", "Demo", 19501)
        with patch.dict("os.environ", {"CODEX_HOME": "/production", "OPENAI_API_KEY": "private", "GOG_ACCOUNT": "private"}):
            cmd, env = manager.compose_args("ps")
        self.assertEqual(cmd[0:2], ["docker", "compose"])
        self.assertIn("19501", env["CLAWVOICE_OPENCLAW_PORT"])
        self.assertNotIn("OPENCLAW_GATEWAY_TOKEN", str(cmd))
        self.assertNotIn("CODEX_HOME", env)
        self.assertNotIn("OPENAI_API_KEY", env)
        self.assertNotIn("GOG_ACCOUNT", env)

    def test_reject_billing_model_and_write_changes(self):
        manager.initialize("UTC", "Demo", 19501)
        path = self.root / ".local/runtime.env"
        original = path.read_text()
        for text in (original + "OPENAI_API_KEY=example\n",
                     original.replace("TARI_CALENDAR_WRITES=0", "TARI_CALENDAR_WRITES=1"),
                     original.replace("openai/gpt-6.1-sol", "openai/another-model")):
            path.write_text(text)
            with self.assertRaises(RuntimeError):
                manager.load_env()
        path.write_text(original)
        config_path = self.root / ".local/state/openclaw.json"
        config = json.loads(config_path.read_text())
        config["agents"]["defaults"]["model"]["fallbacks"] = ["openai/another-model"]
        config_path.write_text(json.dumps(config))
        with self.assertRaises(RuntimeError):
            manager.validate_policy()

    def test_init_repairs_permissions_without_overwriting(self):
        manager.initialize("UTC", "Demo", 19501)
        path = self.root / ".local/runtime.env"
        original = path.read_text()
        path.chmod(0o644)
        manager.initialize("UTC", "Demo", 19501)
        self.assertEqual(path.read_text(), original)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_reject_symlink_workspace_file(self):
        manager.initialize("UTC", "Demo", 19501)
        path = self.root / ".local/state/workspace/SOUL.md"
        path.unlink()
        path.symlink_to(self.root / "workspace/SOUL.md")
        with self.assertRaises(RuntimeError):
            manager.initialize("UTC", "Demo", 19501)

    def test_accept_oauth_login_identity_metadata(self):
        manager.initialize("UTC", "Demo", 19501)
        path = self.root / ".local/state/openclaw.json"
        config = json.loads(path.read_text())
        config["auth"]["profiles"]["openai:clawvoice"]["email"] = "demo@example.test"
        config["auth"]["profiles"]["openai:clawvoice"]["displayName"] = "Demo"
        path.write_text(json.dumps(config))
        manager.validate_policy()

    def test_nested_help_reaches_actual_runtime_cli(self):
        with patch("sys.argv", ["manage.py", "openclaw", "models", "auth", "login", "--help"]), patch.object(manager, "run_compose", return_value=0) as run:
            self.assertEqual(manager.main(), 0)
        self.assertEqual(run.call_args.args[-5:],
                         ("/app/openclaw.mjs", "models", "auth", "login", "--help"))

    def test_can_stop_own_container_with_invalid_policy(self):
        manager.initialize("UTC", "Demo", 19501)
        path = self.root / ".local/runtime.env"
        path.write_text(path.read_text().replace("TARI_CALENDAR_WRITES=0", "TARI_CALENDAR_WRITES=1"))
        cmd, env = manager.compose_args("stop", "openclaw")
        self.assertEqual(cmd[-2:], ["stop", "openclaw"])
        with self.assertRaises(RuntimeError):
            manager.compose_args("up", "-d", "openclaw")

    def test_refuse_parent_traversal(self):
        with self.assertRaises(RuntimeError):
            manager.owned_path(self.root / ".." / "outside")


if __name__ == "__main__":
    unittest.main()
