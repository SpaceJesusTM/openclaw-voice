"""Offline tests for the coding_workspace plugin (Astra cache topics & verifiers)."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys

from clawvoice.context.cache import HotContextCache
from clawvoice.helpers import HelperRunner
from clawvoice.jobs.manager import JobManager
from clawvoice.jobs.models import Expectations
from clawvoice.jobs.store import JobStore
from clawvoice.jobs.verifiers import VerifyContext, run_verifier
from clawvoice.openclaw.adapter import SubmittedRun
from clawvoice.openclaw.outcomes import RunOutcome
from clawvoice.paths import REPO_ROOT
from clawvoice.registry import default_registry
from clawvoice.timeutil import FrozenClock
from conftest import astra_workspace_config

HELPER_BIN = REPO_ROOT / "infra/openclaw/workspace-astra/bin/astra-project"
FIXED_CLOCK = FrozenClock(datetime(2026, 10, 24, 15, 0, tzinfo=timezone.utc))


def run_astra_cli(workspace: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(HELPER_BIN), "--workspace", str(workspace), *args],
        capture_output=True,
        text=True,
        check=True,
        timeout=15,
    )


async def test_astra_cache_topics_read_real_workspace_and_distinguish_running_jobs(tmp_path):
    ws = tmp_path / "astra-workspace"
    ws.mkdir()
    agent = astra_workspace_config(18949, ws)
    reg = default_registry()
    reg.load_plugin("coding_workspace")
    helpers = HelperRunner(agent.helpers, memo_seconds=0)

    running_jobs_list: list[dict] = []
    cache = HotContextCache(
        agent=agent,
        registry=reg,
        helpers=helpers,
        jobs_view=lambda: running_jobs_list,
        clock=FIXED_CLOCK,
    )

    # 1. Empty workspace
    active0 = await cache.get("projects.active")
    assert active0["status"] == "fresh"
    assert active0["data"]["active_project"] is None
    assert active0["data"]["work_state"] == "idle"
    assert active0["data"]["active_job_running"] is False
    assert "No active coding project" in active0["summary"]

    # 2. Create coffee-shop project on disk and run check
    run_astra_cli(
        ws,
        "init",
        "coffee-shop",
        "--name",
        "Starlight Coffee",
        "--description",
        "Simple website for a fictional coffee shop",
        "--json",
    )
    proj_dir = ws / "projects/coffee-shop"
    (proj_dir / "index.html").write_text(
        "<!doctype html><html><head><title>Starlight Coffee</title></head>"
        "<body><h1>Fresh Roasted Daily</h1></body></html>\n",
        encoding="utf-8",
    )
    (proj_dir / "styles.css").write_text("body { margin: 0; }\n", encoding="utf-8")
    run_astra_cli(
        ws,
        "check",
        "coffee-shop",
        "--action",
        "created",
        "--files",
        "index.html,styles.css",
        "--summary",
        "Created Starlight Coffee website",
        "--json",
    )

    # 3. Read projects.active while idle (no running job)
    cache.invalidate(["projects.active", "projects.recent", "project.latest_update"], "created coffee-shop")
    await cache.refresh_now("projects.active")
    active1 = await cache.get("projects.active")
    assert active1["data"]["slug"] == "coffee-shop"
    assert active1["data"]["path"] == "projects/coffee-shop"
    assert active1["data"]["html_title"] == "Starlight Coffee"
    assert active1["data"]["html_heading"] == "Fresh Roasted Daily"
    assert active1["data"]["work_state"] == "idle"
    assert active1["data"]["active_job_running"] is False
    assert "idle" in active1["summary"].lower()

    # 4. Simulate an active background coding job — projects.active distinguishes it
    running_jobs_list.append(
        {"job_id": "J10001", "title": "Update website title to Moonlight Coffee", "status": "running", "age": "2s ago"}
    )
    await cache.refresh_now("projects.active")
    active2 = await cache.get("projects.active")
    assert active2["data"]["work_state"] == "active_job_running"
    assert active2["data"]["active_job_running"] is True
    assert "Update website title to Moonlight Coffee" in active2["summary"]
    running_jobs_list.clear()

    # 5. Read projects.recent and project.latest_update
    recent = await cache.get("projects.recent")
    assert recent["data"]["count"] == 1
    assert recent["data"]["projects"][0]["slug"] == "coffee-shop"

    latest = await cache.get("project.latest_update")
    assert latest["data"]["project"] == "coffee-shop"
    assert latest["data"]["validation"]["status"] == "passed"
    assert latest["data"]["validation"]["exit_code"] == 0
    assert "index.html" in latest["data"]["files_changed"]
    assert "Validation/build passed" in latest["summary"]


async def test_coding_change_verifier_checks_real_files_titles_and_check_ids(tmp_path):
    ws = tmp_path / "astra-workspace"
    ws.mkdir()
    agent = astra_workspace_config(18949, ws)
    reg = default_registry()
    reg.load_plugin("coding_workspace")
    helpers = HelperRunner(agent.helpers, memo_seconds=0)
    vctx = VerifyContext(agent=agent, helpers=helpers, clock=FIXED_CLOCK)

    run_astra_cli(ws, "init", "coffee-shop", "--name", "Moonlight Coffee", "--json")
    proj_dir = ws / "projects/coffee-shop"
    (proj_dir / "index.html").write_text(
        "<!doctype html><html><head><title>Moonlight Coffee</title></head>"
        "<body><h1>Welcome to Astra Studio</h1></body></html>\n",
        encoding="utf-8",
    )
    (proj_dir / "styles.css").write_text("h1 { color: #3b82f6; }\n", encoding="utf-8")
    chk_proc = run_astra_cli(
        ws,
        "check",
        "coffee-shop",
        "--action",
        "updated",
        "--files",
        "index.html,styles.css",
        "--summary",
        "Changed title to Moonlight Coffee and heading to Welcome to Astra Studio",
        "--json",
    )
    import json

    check_id = json.loads(chk_proc.stdout)["check_id"]

    # 1. Valid receipt matching real files, title, heading, and check_id
    item_ok = await run_verifier(
        reg,
        vctx,
        {
            "type": "coding_change",
            "action": "updated",
            "project": "coffee-shop",
            "files": "index.html,styles.css",
            "check_id": check_id,
            "title": "Moonlight Coffee",
            "heading": "Welcome to Astra Studio",
        },
        Expectations(title="Moonlight Coffee"),
    )
    assert item_ok.status == "verified"
    assert item_ok.facts["title"] == "Moonlight Coffee"
    assert item_ok.facts["heading"] == "Welcome to Astra Studio"

    # 1b. Heading update where voice model passes Expectations(title="Welcome to Astra Studio")
    item_heading_ok = await run_verifier(
        reg,
        vctx,
        {
            "type": "coding_change",
            "action": "updated",
            "project": "coffee-shop",
            "files": "index.html",
            "check_id": check_id,
            "title": "Moonlight Coffee",
            "heading": "Welcome to Astra Studio",
        },
        Expectations(title="Welcome to Astra Studio"),
    )
    assert item_heading_ok.status == "verified"
    assert item_heading_ok.facts["title"] == "Moonlight Coffee"
    assert item_heading_ok.facts["heading"] == "Welcome to Astra Studio"

    # 2. Receipt claiming a title that is NOT in index.html fails verification
    item_bad_title = await run_verifier(
        reg,
        vctx,
        {
            "type": "coding_change",
            "action": "updated",
            "project": "coffee-shop",
            "files": "index.html",
            "title": "Nonexistent Title",
        },
        Expectations(title="Nonexistent Title"),
    )
    assert item_bad_title.status == "mismatch"
    assert "Expected HTML <title>" in item_bad_title.message

    # 3. Receipt claiming a file that does not exist fails verification
    item_bad_file = await run_verifier(
        reg,
        vctx,
        {
            "type": "coding_change",
            "action": "updated",
            "project": "coffee-shop",
            "files": "index.html,ghost.js",
        },
        Expectations(),
    )
    assert item_bad_file.status == "mismatch"
    assert "ghost.js" in item_bad_file.message

    # 4. Receipt claiming a local preview URL when preview server is not running fails verification
    item_bad_preview = await run_verifier(
        reg,
        vctx,
        {
            "type": "coding_change",
            "action": "updated",
            "project": "coffee-shop",
            "files": "index.html",
            "preview_url": "http://127.0.0.1:18950",
        },
        Expectations(),
    )
    assert item_bad_preview.status == "mismatch"
    assert "not reachable" in item_bad_preview.message


async def test_end_to_end_astra_job_verification_and_cache_invalidation(tmp_path):
    import asyncio
    import json

    ws = tmp_path / "astra-workspace"
    ws.mkdir()
    agent = astra_workspace_config(18949, ws)
    reg = default_registry()
    reg.load_plugin("coding_workspace")
    helpers = HelperRunner(agent.helpers, memo_seconds=0)
    store = JobStore(tmp_path / "state", "astra")

    class InProcessAstraBackend:
        def __init__(self):
            self._outcome = None

        async def submit(self, *, message, session_key, idempotency_key, timeout_s, on_tool_event=None, require_transport=None):
            run_astra_cli(ws, "init", "coffee-shop", "--name", "Moonlight Coffee", "--json")
            proj_dir = ws / "projects/coffee-shop"
            (proj_dir / "index.html").write_text(
                "<!doctype html><html><head><title>Moonlight Coffee</title></head>"
                "<body><h1>Welcome to Astra Studio</h1></body></html>\n",
                encoding="utf-8",
            )
            chk = json.loads(
                run_astra_cli(
                    ws,
                    "check",
                    "coffee-shop",
                    "--action",
                    "updated",
                    "--files",
                    "index.html",
                    "--summary",
                    "Updated title to Moonlight Coffee",
                    "--json",
                ).stdout
            )
            receipt = {
                "status": "done",
                "summary": "Updated the website title to Moonlight Coffee.",
                "changes": [
                    {
                        "type": "coding_change",
                        "action": "updated",
                        "project": "coffee-shop",
                        "files": ["index.html"],
                        "check_id": chk["check_id"],
                        "title": "Moonlight Coffee",
                        "heading": "Welcome to Astra Studio",
                    }
                ],
            }
            reply = f"I updated the title to Moonlight Coffee.\nCLAWVOICE_RECEIPT {json.dumps(receipt)}"
            self._outcome = RunOutcome(status="completed", text=reply)
            return SubmittedRun(transport="ws", run_id="run-1", session_key=session_key, ack_ms=5)

        async def outcome(self, submitted, timeout_s, via_wait=False):
            return self._outcome

        async def cancel(self, submitted):
            return False

    invalidated_topics: list[str] = []
    cache = HotContextCache(
        agent=agent,
        registry=reg,
        helpers=helpers,
        jobs_view=lambda: jobs.public(10),
        clock=FIXED_CLOCK,
    )

    def on_invalidate(keys: list[str], reason: str):
        invalidated_topics.extend(keys)
        cache.invalidate(keys, reason)

    jobs = JobManager(
        agent=agent,
        backend=InProcessAstraBackend(),
        store=store,
        registry=reg,
        helpers=helpers,
        invalidate=on_invalidate,
        clock=FIXED_CLOCK,
    )
    await jobs.start()
    try:
        job, dup = jobs.submit(
            request="Change the website title to Moonlight Coffee",
            kind="change",
            target="website",
            expectations={"title": "Moonlight Coffee"},
        )
        assert not dup
        for _ in range(100):
            if jobs.get(job.id).terminal:
                break
            await asyncio.sleep(0.02)
        settled = jobs.get(job.id)
        assert settled.status == "completed"
        assert settled.verification["status"] == "verified"
        assert "Moonlight Coffee" in settled.outcome_text
        assert set(invalidated_topics) >= {"projects.active", "projects.recent", "project.latest_update"}

        active = await cache.get("projects.active")
        assert active["data"]["html_title"] == "Moonlight Coffee"
        assert active["data"]["html_heading"] == "Welcome to Astra Studio"
    finally:
        await jobs.stop()
