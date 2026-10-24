"""Offline tests for the isolated Astra OpenClaw bootstrap and astra-project helper."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

BASE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("clawvoice_manager", BASE / "manage.py")
manager = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(manager)

HELPER_PATH = BASE / "workspace-astra/bin/astra-project"


class AstraBootstrapAndHelperTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.checkout = Path(self.temp.name) / "checkout"
        self.root = self.checkout / "infra/openclaw"
        self.root.mkdir(parents=True)
        shutil.copytree(BASE / "workspace", self.root / "workspace")
        shutil.copytree(BASE / "workspace-astra", self.root / "workspace-astra")
        shutil.copytree(BASE / "config", self.root / "config")
        self.orig = (manager.ROOT, manager.LOCAL, manager.ASTRA_LOCAL, manager.ASTRA_WORKSPACE,
                     manager.ASTRA_EXTERNAL_HOME, manager.keeps_private_mode)
        manager.ROOT = self.root
        manager.LOCAL = self.root / ".local"
        manager.ASTRA_LOCAL = self.root / ".local-astra"
        manager.ASTRA_WORKSPACE = self.checkout / "astra-workspace"
        manager.ASTRA_EXTERNAL_HOME = Path(self.temp.name) / "linux-home/.local/share/clawvoice"
        self.addCleanup(self.restore)

    def restore(self) -> None:
        (manager.ROOT, manager.LOCAL, manager.ASTRA_LOCAL, manager.ASTRA_WORKSPACE,
         manager.ASTRA_EXTERNAL_HOME, manager.keeps_private_mode) = self.orig

    def run_helper(self, *args: str) -> subprocess.CompletedProcess[str]:
        env = {"PATH": "/usr/bin:/bin", "ASTRA_WORKSPACE_DIR": str(manager.ASTRA_WORKSPACE)}
        return subprocess.run(
            [sys.executable, str(HELPER_PATH), *args],
            capture_output=True,
            text=True,
            env=env,
            timeout=15,
        )

    def test_astra_init_isolated_from_tari_and_idempotent(self) -> None:
        manager.initialize("America/Toronto", "Astra", 18949, agent="astra", preview_port=18950)
        self.assertFalse((self.root / ".local").exists())
        env_path = self.root / ".local-astra/runtime.env"
        self.assertTrue(env_path.is_file())
        self.assertEqual(env_path.stat().st_mode & 0o777, 0o600)
        env_text = env_path.read_text()
        self.assertIn("CLAWVOICE_OPENCLAW_PORT=18949", env_text)
        self.assertIn("ASTRA_PREVIEW_PORT=18950", env_text)
        self.assertNotIn("TARI_GOOGLE_ACCOUNT", env_text)
        self.assertNotIn("GOG_KEYRING_PASSWORD", env_text)

        cfg = json.loads((self.root / ".local-astra/state/openclaw.json").read_text())
        self.assertEqual(cfg["agents"]["entries"]["astra"]["name"], "Astra")
        self.assertTrue(cfg["tools"]["fs"]["workspaceOnly"])
        self.assertEqual(
            cfg["gateway"]["controlUi"]["allowedOrigins"],
            ["http://localhost:18949", "http://127.0.0.1:18949"],
        )

        ws = manager.ASTRA_WORKSPACE
        self.assertTrue((ws / "projects").is_dir())
        self.assertTrue((ws / "project-state").is_dir())
        self.assertTrue((ws / "logs").is_dir())
        self.assertTrue((ws / "bin/astra-project").is_file())
        self.assertEqual((ws / "bin/astra-project").stat().st_mode & 0o777, 0o755)

        # Repeat init preserves existing credentials and state
        manager.initialize("UTC", "Other", 19999, agent="astra", preview_port=19998)
        self.assertEqual(env_path.read_text(), env_text)
        self.assertNotIn("ASTRA_STATE_DIR", env_text)

    def test_astra_state_and_workspace_move_off_a_checkout_that_cannot_keep_private_modes(self) -> None:
        # A Windows drive under WSL reports every directory as 0777.
        in_checkout = self.root / ".local-astra/state"
        manager.keeps_private_mode = lambda directory: directory != in_checkout
        manager.initialize("America/Toronto", "Astra", 18949, agent="astra", preview_port=18950)
        env_path = self.root / ".local-astra/runtime.env"
        data = manager.load_env("astra")
        home = manager.ASTRA_EXTERNAL_HOME / data["COMPOSE_PROJECT_NAME"]
        state = Path(data["ASTRA_STATE_DIR"])
        self.assertEqual(state, home / "state")
        self.assertEqual(state.stat().st_mode & 0o777, 0o700)
        self.assertEqual((state / "openclaw.json").stat().st_mode & 0o777, 0o600)
        self.assertFalse((in_checkout / "openclaw.json").exists())

        self.assertEqual(manager.astra_workspace_dir(), home / "workspace")
        self.assertTrue((home / "workspace/bin/astra-project").is_file())
        self.assertFalse(manager.ASTRA_WORKSPACE.exists())

        env_text = env_path.read_text()
        manager.initialize("America/Toronto", "Astra", 18949, agent="astra", preview_port=18950)
        self.assertEqual(env_path.read_text(), env_text)
        self.assertEqual(env_text.count("ASTRA_STATE_DIR="), 1)
        self.assertEqual(env_text.count("ASTRA_WORKSPACE_DIR="), 1)

    def test_astra_project_lifecycle_and_verification(self) -> None:
        manager.initialize("America/Toronto", "Astra", 18949, agent="astra", preview_port=18950)
        ws = manager.ASTRA_WORKSPACE

        # 1. Empty workspace status
        res = self.run_helper("status", "--json")
        self.assertEqual(res.returncode, 0, res.stderr)
        status0 = json.loads(res.stdout)
        self.assertIsNone(status0["active_project"])
        self.assertEqual(status0["project_count"], 0)

        # 2. Initialize coffee-shop project and write real files
        res = self.run_helper(
            "init",
            "coffee-shop",
            "--name",
            "Starlight Roasters",
            "--description",
            "Simple website for a fictional coffee shop",
            "--json",
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        proj_dir = ws / "projects/coffee-shop"
        (proj_dir / "index.html").write_text(
            "<!doctype html><html><head><title>Starlight Roasters</title></head>"
            "<body><h1>Brewed Under the Stars</h1><p>Fresh espresso daily.</p></body></html>\n",
            encoding="utf-8",
        )
        (proj_dir / "styles.css").write_text("body { font-family: sans-serif; }\n", encoding="utf-8")

        # 3. Validate creation
        res = self.run_helper(
            "check",
            "coffee-shop",
            "--action",
            "created",
            "--files",
            "index.html,styles.css",
            "--summary",
            "Created Starlight Roasters coffee shop website",
            "--json",
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        chk1 = json.loads(res.stdout)
        self.assertTrue(chk1["ok"])
        self.assertEqual(chk1["html_title"], "Starlight Roasters")
        self.assertEqual(chk1["html_heading"], "Brewed Under the Stars")
        check_id_1 = chk1["check_id"]

        # 4. Verify creation receipt
        res = self.run_helper(
            "verify",
            "--project",
            "coffee-shop",
            "--action",
            "created",
            "--files",
            "index.html,styles.css",
            "--check-id",
            check_id_1,
            "--expect-title",
            "Starlight Roasters",
            "--json",
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        ver1 = json.loads(res.stdout)
        self.assertTrue(ver1["verified"])

        # 5. Modify website title to Moonlight Coffee & heading to Welcome to Astra Studio
        (proj_dir / "index.html").write_text(
            "<!doctype html><html><head><title>Moonlight Coffee</title></head>"
            "<body><h1>Welcome to Astra Studio</h1><p>Artisan night roast.</p></body></html>\n",
            encoding="utf-8",
        )
        res = self.run_helper(
            "check",
            "coffee-shop",
            "--action",
            "updated",
            "--files",
            "index.html",
            "--summary",
            "Updated title to Moonlight Coffee and heading to Welcome to Astra Studio",
            "--json",
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        chk2 = json.loads(res.stdout)
        self.assertEqual(chk2["html_title"], "Moonlight Coffee")
        self.assertEqual(chk2["html_heading"], "Welcome to Astra Studio")

        # 6. Verify mismatching title claim fails verification
        res = self.run_helper(
            "verify",
            "--project",
            "coffee-shop",
            "--action",
            "updated",
            "--files",
            "index.html",
            "--expect-title",
            "Wrong Title",
            "--json",
        )
        self.assertFalse(json.loads(res.stdout)["verified"])

        # 7. Verify matching title & heading succeeds
        res = self.run_helper(
            "verify",
            "--project",
            "coffee-shop",
            "--action",
            "updated",
            "--files",
            "index.html",
            "--check-id",
            chk2["check_id"],
            "--expect-title",
            "Moonlight Coffee",
            "--expect-heading",
            "Welcome to Astra Studio",
            "--json",
        )
        self.assertTrue(json.loads(res.stdout)["verified"])

        # 8. Check status, recent, latest-update
        status_now = json.loads(self.run_helper("status", "--json").stdout)
        self.assertEqual(status_now["active_project"]["slug"], "coffee-shop")
        self.assertEqual(status_now["active_project"]["html_title"], "Moonlight Coffee")
        recent_now = json.loads(self.run_helper("recent", "--json").stdout)
        self.assertEqual(recent_now["count"], 1)
        latest_now = json.loads(self.run_helper("latest-update", "--json").stdout)
        self.assertEqual(latest_now["validation"]["status"], "passed")
        self.assertEqual(latest_now["html_heading"], "Welcome to Astra Studio")

    def test_refuse_invalid_slug_and_outside_paths(self) -> None:
        manager.initialize("America/Toronto", "Astra", 18949, agent="astra", preview_port=18950)
        res = self.run_helper("init", "../escape", "--json")
        self.assertNotEqual(res.returncode, 0)
        self.assertFalse(json.loads(res.stdout)["ok"])


if __name__ == "__main__":
    unittest.main()

