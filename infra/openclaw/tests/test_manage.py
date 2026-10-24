"""Offline tests for the isolated OpenClaw demo bootstrap. No Docker/OAuth required."""
from __future__ import annotations
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest


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
        self.assertIn("OPENAI_API_KEY=", original)
        self.assertTrue((self.root / ".local/state/workspace/bin/tari-google").exists())
        self.assertEqual(env_path.stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.root / ".local/state/workspace/bin/tari-google").stat().st_mode & 0o777, 0o700)
        manager.initialize("UTC", "Different", 19502)
        self.assertEqual(env_path.read_text(), original)
        self.assertEqual(json.loads((self.root / ".local/state/openclaw.json").read_text()),
                         config)

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
        cmd, env = manager.compose_args("ps")
        self.assertEqual(cmd[0:2], ["docker", "compose"])
        self.assertIn("19501", env["CLAWVOICE_OPENCLAW_PORT"])
        self.assertNotIn("OPENCLAW_GATEWAY_TOKEN", str(cmd))


if __name__ == "__main__":
    unittest.main()
