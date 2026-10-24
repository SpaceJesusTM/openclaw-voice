import asyncio
import re
from pathlib import Path

import httpx
import pytest

from clawvoice.agents import AgentRegistry
from clawvoice.runtime import Runtime, set_runtime
from conftest import TOKEN, astra_config, tari_fixture_config, write_fixture

CORE = Path(__file__).resolve().parents[1] / "clawvoice"


@pytest.fixture
async def two_agent_runtime(gateway, fixture_dir):
    write_fixture(fixture_dir, "planner", {"time_zone": "America/Toronto", "days": {}, "checklist": [], "calendar_complete": True})
    registry = AgentRegistry(
        agents={"tari": tari_fixture_config(gateway.port, "ws"), "astra": astra_config(gateway.port)},
        default_id="tari",
    )
    runtime = Runtime(registry, state_directory=fixture_dir.parent / "state")
    await runtime.start()
    set_runtime(runtime)
    yield runtime
    set_runtime(None)
    await runtime.stop()


async def client():
    from clawvoice.server import app

    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:7860")


async def test_api_exposes_identity_and_state_without_secrets(two_agent_runtime):
    async with await client() as http:
        agents = (await http.get("/api/agents")).json()
        assert agents["default"] == "tari" and agents["model"] == "higgs-realtime-1.5-preview"
        assert {a["id"] for a in agents["agents"]} == {"tari", "astra"}
        state = await http.get("/api/state", params={"agent": "astra"})
        body = state.text
        assert state.status_code == 200 and "project.active" in body
        for forbidden in (TOKEN, "envfile", "token_ref", "private_key"):
            assert forbidden not in body
        assert (await http.get("/api/state", params={"agent": "nobody"})).status_code == 404
        index = await http.get("/")
        assert index.status_code == 200 and "<title>Chorus</title>" in index.text


async def test_cross_origin_posts_are_refused(two_agent_runtime):
    async with await client() as http:
        evil = await http.post("/api/jobs/J123456/cancel", headers={"Origin": "https://evil.example"})
        assert evil.status_code == 403
        same = await http.post("/api/jobs/J123456/cancel", headers={"Origin": "http://127.0.0.1:7860"})
        assert same.status_code == 200 and same.json()["status"] == "not_found"
        refreshed = await http.post("/api/agents/astra/cache/project.active/refresh")
        assert refreshed.json()["refreshed"] is True


async def test_two_agents_share_code_but_not_state(two_agent_runtime):
    tari, astra = two_agent_runtime.agent("tari"), two_agent_runtime.agent("astra")
    assert set(tari.cache.topic_keys) & set(astra.cache.topic_keys) == {"jobs.recent"}
    job, _ = astra.jobs.submit(request="run the tests", kind="change", target="tests")
    for _ in range(300):
        if astra.jobs.get(job.id).terminal:
            break
        await asyncio.sleep(0.02)
    assert astra.jobs.get(job.id).status == "completed"
    assert tari.jobs.get(job.id) is None and tari.jobs.public() == []
    assert tari.store.path != astra.store.path
    tari_recent = await tari.cache.get("jobs.recent")
    astra_recent = await astra.cache.get("jobs.recent")
    assert "no recent background tasks" in tari_recent["summary"]
    assert "Recently finished" in astra_recent["summary"]
    assert tari.agent.session_key != astra.agent.session_key


def test_core_runtime_has_no_agent_specific_branches():
    """Agent names, helpers and paths live in YAML; the core must not name them."""
    pattern = re.compile(r"tari|astra|jester|infra/openclaw|manage\.py", re.I)
    offenders = []
    for path in CORE.rglob("*.py"):
        relative = path.relative_to(CORE)
        if relative.parts[0] in ("plugins", "testing"):
            continue  # optional plugins and test doubles may name what they adapt
        for number, line in enumerate(path.read_text().splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{relative}:{number}: {line.strip()}")
    assert offenders == []
