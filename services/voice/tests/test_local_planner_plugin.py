import pytest

from clawvoice.plugins import local_planner
from conftest import tari_fixture_config, write_fixture
from test_day_planner_plugin import NOON, PLANNER, ctx


@pytest.fixture
def agent(fixture_dir):
    write_fixture(fixture_dir, "planner", PLANNER)
    return tari_fixture_config()


async def test_local_view_speaks_blocks_commutes_and_checklist_only(agent):
    resolved = await local_planner.local_planner_day(ctx(agent), agent.topic("planner.today"))
    assert resolved.complete and resolved.day == "2026-10-23" and resolved.warnings == []
    assert resolved.summary == (
        "Today, Friday, October 23: 1 planner block: Deep work, 9 to 11 AM. "
        "Commutes: Commute, 2 to 3 PM. Checklist: 3 open tasks (1 priority, 1 in progress)."
    )
    assert "calendar" not in resolved.summary.lower()
    assert set(resolved.value) == {"date", "day", "planner_blocks", "commutes", "checklist"}


async def test_empty_local_day_never_mentions_a_calendar(agent, fixture_dir):
    write_fixture(fixture_dir, "planner", {**PLANNER, "days": {}, "checklist": []})
    resolved = await local_planner.local_planner_day(ctx(agent, NOON), agent.topic("planner.tomorrow"))
    assert resolved.summary == "Tomorrow, Saturday, October 24: no planner blocks or commutes."
    assert resolved.complete


def test_tari_config_uses_local_planner_topics(components):
    agent = tari_fixture_config()
    assert agent.topic("planner.today").resolver == "local_planner_day"
    assert components.check_agent(agent) == []
