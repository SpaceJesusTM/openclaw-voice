"""Offline tests for the isolated OpenClaw demo bootstrap. No Docker/OAuth required."""
from __future__ import annotations
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch


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
        self.assertNotIn("OPENAI_API_KEY=", original)
        self.assertTrue((self.root / ".local/state/workspace/bin/day-planner").exists())
        self.assertFalse((self.root / ".local/state/workspace/bin/tari-google").exists())
        self.assertNotIn("GOOGLE", original)
        self.assertNotIn("GOG_", original)
        self.assertFalse((self.root / ".local/google").exists())
        self.assertEqual(env_path.stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.root / ".local/state/workspace/bin/day-planner").stat().st_mode & 0o777, 0o700)
        for directory in (self.root / ".local").rglob("*"):
            if directory.is_dir():
                self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
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
        with patch.dict("os.environ", {"CODEX_HOME": "/production", "OPENAI_API_KEY": "private", "GOG_ACCOUNT": "private"}):
            cmd, env = manager.compose_args("ps")
        self.assertEqual(cmd[0:2], ["docker", "compose"])
        self.assertIn("19501", env["CLAWVOICE_OPENCLAW_PORT"])
        self.assertNotIn("OPENCLAW_GATEWAY_TOKEN", str(cmd))
        self.assertNotIn("CODEX_HOME", env)
        self.assertNotIn("OPENAI_API_KEY", env)
        self.assertNotIn("GOG_ACCOUNT", env)

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

    def test_legacy_config_chat_uses_native_gateway_without_rewriting_state(self):
        manager.initialize("UTC", "Demo", 19501)
        path = self.root / ".local/state/openclaw.json"
        config = json.loads(path.read_text())
        # Old installations can omit utilityModel and modelPolicy entirely;
        # account/profile names and additional agents belong to OpenClaw.
        config["agents"]["defaults"].pop("utilityModel", None)
        config["agents"]["entries"]["tari"].pop("modelPolicy", None)
        config["auth"] = {"profiles": {"openai:owner": {"provider": "openai", "mode": "oauth"}}}
        config["agents"]["entries"]["other"] = {"model": "example/custom-model"}
        path.write_text(json.dumps(config))
        before = path.read_bytes()
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{"choices":[{"message":{"content":"Hello!"}}]}'
        with patch.object(manager.urllib.request, "urlopen", return_value=response) as call, patch("builtins.print") as output:
            manager.chat("Hello Tari!", "legacy-test")
        request = call.call_args.args[0]
        self.assertEqual(json.loads(request.data)["model"], "openclaw/tari")
        self.assertTrue(request.get_header("Authorization").startswith("Bearer "))
        output.assert_called_once_with("Hello!")
        self.assertEqual(path.read_bytes(), before)

    def test_nested_help_reaches_actual_runtime_cli(self):
        with patch("sys.argv", ["manage.py", "openclaw", "models", "auth", "login", "--help"]), patch.object(manager, "run_compose", return_value=0) as run:
            self.assertEqual(manager.main(), 0)
        self.assertEqual(run.call_args.args[-5:],
                         ("/app/openclaw.mjs", "models", "auth", "login", "--help"))

    def test_refuse_parent_traversal(self):
        with self.assertRaises(RuntimeError):
            manager.owned_path(self.root / ".." / "outside")

    def legacy_planner_workspace(self):
        manager.initialize("America/Toronto", "Demo", 19501)
        workspace = self.root / ".local/state/workspace"
        for relative in ("bin/day-planner", "skills/tari-day-planner/SKILL.md"):
            (workspace / relative).unlink()
        for relative in ("AGENTS.md", "TOOLS.md"):
            path = workspace / relative
            path.write_text(path.read_text().split(manager.PLANNER_SECTION)[0].rstrip() + "\n\nOwner custom note.\n")
        return workspace

    def test_targeted_planner_install_preserves_identity_credentials_and_state(self):
        workspace = self.legacy_planner_workspace()
        env_path = self.root / ".local/runtime.env"
        config = self.root / ".local/state/openclaw.json"
        protected = (env_path.read_bytes(), config.read_bytes())
        agent_before = (workspace / "AGENTS.md").read_text()
        (workspace / "memory").mkdir()
        memory = workspace / "memory/owner.md"
        memory.write_text("Private fixture, preserve.")
        manager.install_planner()
        self.assertTrue((workspace / "AGENTS.md").read_text().startswith(agent_before.rstrip()))
        self.assertIn(manager.PLANNER_SECTION, (workspace / "AGENTS.md").read_text())
        self.assertIn("This demo has no calendar or email access", (workspace / "AGENTS.md").read_text())
        self.assertEqual((env_path.read_bytes(), config.read_bytes()), protected)
        self.assertEqual(memory.read_text(), "Private fixture, preserve.")
        self.assertEqual((workspace / "bin/day-planner").stat().st_mode & 0o777, 0o700)
        content = (workspace / "AGENTS.md").read_bytes()
        manager.install_planner()
        self.assertEqual((workspace / "AGENTS.md").read_bytes(), content)
        self.assertFalse((workspace / "planner").exists())

    def test_planner_install_refuses_independently_modified_helper(self):
        workspace = self.legacy_planner_workspace()
        (workspace / "bin/day-planner").write_text("owner's existing helper")
        before = (workspace / "AGENTS.md").read_bytes()
        with self.assertRaises(RuntimeError):
            manager.install_planner()
        self.assertEqual((workspace / "AGENTS.md").read_bytes(), before)

    def test_init_never_seeds_personal_planner_data_from_template(self):
        path = self.root / "workspace/planner/days/2026-10-24.md"
        path.parent.mkdir(parents=True)
        path.write_text("Private accidental fixture.")
        manager.initialize("UTC", "Demo", 19501)
        self.assertFalse((self.root / ".local/state/workspace/planner").exists())


if __name__ == "__main__":
    unittest.main()
