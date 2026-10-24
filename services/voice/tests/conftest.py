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
    data = copy.deepcopy(load_yaml("astra"))
    data["openclaw"]["gateway"]["ws_url"] = f"ws://127.0.0.1:{port}"
    data["openclaw"]["gateway"]["http_url"] = f"http://127.0.0.1:{port}"
    data["openclaw"]["gateway"]["transport"] = transport
    data["openclaw"]["timeouts"].update(timeouts)
    return agent_from(data)


# Tari's live config is local-only, but the day_planner plugin still supports a
# Calendar. These restore the Calendar topics so its resolvers and verifier stay covered.
CALENDAR_TOPICS = [
    {"key": "calendar.today", "description": "today's Calendar events only",
     "resolver": "calendar_day", "args": {"day": "today"}},
    {"key": "calendar.next", "description": "the next upcoming Calendar event or meeting",
     "resolver": "calendar_next", "args": {"horizon_days": 3}},
]
CALENDAR_RECEIPT = {
    "verifier": "calendar_event",
    "args": {"helper": "google"},
    "invalidates": ["calendar.today", "calendar.next", "planner.today", "planner.tomorrow"],
    "format": '{"type":"calendar_event","action":"created|updated|deleted",'
    '"calendar_id":"<calendar id, e.g. primary>","event_id":"<event id>"}',
}


def with_calendar(data: dict) -> dict:
    for topic in data["hot_context"]["topics"]:
        if topic["resolver"] == "local_planner_day":
            topic["resolver"] = "planner_day"
    data["hot_context"]["topics"][2:2] = copy.deepcopy(CALENDAR_TOPICS)
    data["delegation"]["targets"].insert(0, "calendar")
    data["delegation"]["receipt_types"] = {"calendar_event": dict(CALENDAR_RECEIPT),
                                           **data["delegation"]["receipt_types"]}
    return data


def tari_fixture_config(port: int = 9, transport: str = "http", calendar: bool = False) -> AgentConfig:
    """Tari's real configuration, with helpers and Gateway swapped for fixtures.

    ``calendar=True`` adds the Calendar topics and receipt type back (see ``with_calendar``).
    """
    data = copy.deepcopy(load_yaml("tari"))
    if calendar:
        with_calendar(data)
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
    registry.load_plugin("local_planner")
    return registry


def write_fixture(directory: Path, name: str, data) -> None:
    (directory / f"{name}.json").write_text(json.dumps(data))
