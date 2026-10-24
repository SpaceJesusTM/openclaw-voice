"""Jester: a voice-only third agent (no OpenClaw), alone and in the Team room."""

import asyncio
from types import SimpleNamespace

import pytest

from clawvoice.agents import ConfigError, load_registry, parse_agent
from clawvoice.registry import default_registry
from clawvoice.runtime import AgentRuntime, VoiceOnlyBackend
from clawvoice.team.addressing import AddressBook
from clawvoice.team.bot import team_members
from clawvoice.team.overseer import instructions, tools
from clawvoice.team.room import team_instructions
from clawvoice.voice.instructions import build_instructions
from clawvoice.voice.tools import VoiceTools
from test_team_overseer import call, created, done, room, settled  # noqa: F401 - room is a fixture
from test_team_room import FakeVoice, make_room, ready_reply, say

AGENT_TOOLS = {"get_context", "delegate_task", "check_tasks", "cancel_task"}


def registry():
    return load_registry(environ={})


def add_jester(target):
    reg = registry()
    target.voices["jester"] = FakeVoice(target, "jester")
    for key, voice in target.voices.items():
        voice.agent = reg.get(key)
    target.book = AddressBook({a.id: a.aliases for a in reg.all()})
    return target


@pytest.fixture
def trio(room):  # noqa: F811
    """The overseer test room, plus Jester."""
    return add_jester(room)


def test_registry_loads_jester_without_openclaw():
    reg = registry()
    assert list(reg.agents) == ["tari", "astra", "jester"] and reg.default_id == "tari"
    jester = reg.get("jester")
    assert jester.voice_only and jester.gateway is None
    assert not (jester.topics or jester.receipt_types or jester.helpers or jester.plugins)
    assert jester.voice not in (reg.get("tari").voice, reg.get("astra").voice)
    assert "jester" in jester.aliases
    identity = jester.public_identity()
    assert identity["voice_only"] and identity["tagline"] == "Entertainment" and "joke" in identity["role"]
    for other in ("tari", "astra"):  # still real OpenClaw agents
        assert not reg.get(other).voice_only and reg.get(other).gateway is not None


def test_a_voice_only_agent_cannot_declare_openclaw_capabilities(tmp_path):
    data = {"id": "quiet", "backend": "voice", "openclaw": {"gateway": {"token_ref": "env:X"}}}
    with pytest.raises(ConfigError, match="voice-only"):
        parse_agent(data, source=tmp_path / "quiet.yaml", base=tmp_path)


async def test_jester_runtime_starts_without_a_gateway_and_says_so(tmp_path):
    runtime = AgentRuntime(registry().get("jester"), default_registry(), state_directory=tmp_path, persist_jobs=False)
    assert isinstance(runtime.backend, VoiceOnlyBackend)
    await runtime.start()
    try:
        state = runtime.state()
        assert state["gateway"]["voice_only"] and not state["gateway"]["applicable"]
        assert state["gateway"]["config_error"] is None  # not applicable, not a failure
        assert state["cache"]["topics"] == [] and state["jobs"] == []
    finally:
        await runtime.stop()


def test_jester_offers_no_tools_and_tari_keeps_its_own():
    reg = registry()
    jester = VoiceTools(SimpleNamespace(agent=reg.get("jester")), None)
    assert jester.schemas() == [] and jester.realtime_tools() == []
    tari = VoiceTools(SimpleNamespace(agent=reg.get("tari")), None)
    assert {schema.name for schema in tari.schemas()} == AGENT_TOOLS
    text = build_instructions(reg.get("jester"))
    assert "no tools" in text and not any(name in text for name in AGENT_TOOLS)
    assert "delegate_task" in build_instructions(reg.get("tari"))
    team_text = team_instructions(reg.get("jester"), [reg.get("tari"), reg.get("astra")], intelligent=True)
    assert "Tari and Astra" in team_text and "Background results" not in team_text


def test_team_has_three_members_and_the_overseer_knows_jester():
    reg = registry()
    runtime = SimpleNamespace(agents={a.id: SimpleNamespace(agent=a) for a in reg.all()})
    assert [rt.agent.id for rt in team_members(runtime)] == ["tari", "astra", "jester"]
    roster = instructions(reg.all(), "tari")
    assert "- jester (Jester;" in roster and "joke" in roster
    assert tools([a.id for a in reg.all()])[0]["parameters"]["properties"]["agent"]["enum"] == ["tari", "astra", "jester"]


async def test_overseer_routes_to_jester_keeps_it_and_switches_away(trio):
    team = trio
    team.addressed = "tari"
    await team.typed("Tell me a really funny joke.")
    await created(team)
    await call(team, "jester", reason="Entertainment request")
    await done(team)
    await settled(team)
    assert team.voices["jester"].asked == ["Tell me a really funny joke."] and team.addressed == "jester"
    await team.typed("Another one.")  # no call_agent: stays with Jester
    await created(team, "r2")
    await done(team, "r2")
    await settled(team)
    assert team.voices["jester"].asked[-1] == "Another one." and team.addressed == "jester"
    await team.typed("What's on my calendar tomorrow?")
    await created(team, "r3")
    await call(team, "tari", rid="r3", cid="c3")
    await done(team, "r3")
    await settled(team)
    assert team.voices["tari"].asked == ["What's on my calendar tomorrow?"] and team.addressed == "tari"
    await team.typed("Jester, roast that website.")  # explicit name
    await created(team, "r4")
    await done(team, "r4")
    await settled(team)
    assert team.voices["jester"].asked[-1] == "Jester, roast that website." and team.addressed == "jester"
    assert not team.voices["astra"].asked


def test_names_mode_routes_to_jester():
    names_room = add_jester(make_room())
    say(names_room, "Hey Jester, make me laugh.")
    assert names_room.voices["jester"].asked == ["Hey Jester, make me laugh."]
    assert not names_room.voices["tari"].asked and not names_room.voices["astra"].asked


def test_three_agents_share_one_floor():
    async def scenario():
        trio_room = add_jester(make_room())
        replies = [ready_reply(trio_room, agent) for agent in ("tari", "astra", "jester")]
        await trio_room.floor.tick()
        assert trio_room.floor.current is replies[0]
        assert [r.agent for r in trio_room.floor.ready()] == ["astra", "jester"]

    asyncio.run(scenario())
