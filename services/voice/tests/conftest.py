"""Shared offline fixtures: fake Gateway, fixture helpers and agent configs.

Nothing here contacts Boson, OpenClaw, Docker or Google.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
import yaml

from clawvoice.agents import AgentConfig, parse_agent
from clawvoice.paths import AGENTS_DIR
from clawvoice.registry import default_registry
from clawvoice.testing.fake_gateway import FakeGateway

TOKEN = "fixture-gateway-token-for-tests-only"


@pytest.fixture
def fixture_dir(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "fixtures"
    path.mkdir()
    monkeypatch.setenv("CLAWVOICE_FIXTURE_DIR", str(path))
    monkeypatch.setenv("CLAWVOICE_FIXTURE_GATEWAY_TOKEN", TOKEN)
    monkeypatch.setenv("CLAWVOICE_STATE_DIR", str(tmp_path / "state"))
    return path


@pytest.fixture
async def gateway(fixture_dir):
    fake = FakeGateway(token=TOKEN, delay_scale=0.05)
    await fake.start()
    yield fake
    await fake.stop()


def load_yaml(name: str) -> dict:
    return yaml.safe_load((AGENTS_DIR / f"{name}.yaml").read_text())


def agent_from(data: dict) -> AgentConfig:
    return parse_agent(data, source=AGENTS_DIR / f"{data['id']}.yaml", base=AGENTS_DIR)


def astra_config(port: int, transport: str = "ws", **timeouts) -> AgentConfig:
    data = copy.deepcopy(load_yaml("fixture"))
    data["id"] = "astra"
    data["display_name"] = "Astra"
    data["emoji"] = "🔵"
    data["color"] = "#3b82f6"
    data["openclaw"]["gateway"]["ws_url"] = f"ws://127.0.0.1:{port}"
    data["openclaw"]["gateway"]["http_url"] = f"http://127.0.0.1:{port}"
    data["openclaw"]["gateway"]["transport"] = transport
    data["openclaw"]["timeouts"].update(timeouts)
    return agent_from(data)


def astra_workspace_config(port: int, workspace_dir: Path, transport: str = "ws", **timeouts) -> AgentConfig:
    """Real Astra configuration backed by coding_workspace plugin and astra-project helper."""
    data = copy.deepcopy(load_yaml("astra"))
    data["openclaw"]["gateway"].update(
        ws_url=f"ws://127.0.0.1:{port}",
        http_url=f"http://127.0.0.1:{port}",
        token_ref="env:CLAWVOICE_FIXTURE_GATEWAY_TOKEN",
        transport=transport,
        device_file="astra-fixture-device.json",
    )
    data["helpers"] = {
        "project": [
            "{python}",
            "{repo}/infra/openclaw/workspace-astra/bin/astra-project",
            "--workspace",
            str(workspace_dir),
        ]
    }
    data["openclaw"]["timeouts"].update(timeouts)
    return agent_from(data)


def tari_fixture_config(port: int = 9, transport: str = "http") -> AgentConfig:
    """Tari's real configuration, with helpers and Gateway swapped for fixtures."""
    data = copy.deepcopy(load_yaml("tari"))
    data["openclaw"]["gateway"].update(
        ws_url=f"ws://127.0.0.1:{port}",
        http_url=f"http://127.0.0.1:{port}",
        token_ref="env:CLAWVOICE_FIXTURE_GATEWAY_TOKEN",
        transport=transport,
        device_file="fixture-device.json",
    )
    data["helpers"] = {
        "planner": ["{python}", "-m", "clawvoice.testing.fixture_helper", "planner"],
        "google": ["{python}", "-m", "clawvoice.testing.fixture_helper", "google"],
    }
    return agent_from(data)


@pytest.fixture
def components():
    registry = default_registry()
    registry.load_plugin("day_planner")
    return registry


def write_fixture(directory: Path, name: str, data) -> None:
    (directory / f"{name}.json").write_text(json.dumps(data))
