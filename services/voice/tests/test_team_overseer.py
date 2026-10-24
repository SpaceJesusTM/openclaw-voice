"""Real TeamRoom/floor with deterministic Higgs protocol events; no provider calls."""

import asyncio
import json
import time

import pytest

from clawvoice.agents import load_registry
from clawvoice.config import MODEL, load_settings
from clawvoice.team.overseer import Overseer, instructions, router_mode, tools
from test_team_room import make_room, ready_reply


class Session:
    closed = False

    def __init__(self):
        self.sent = []
        self.requests = asyncio.Queue()

    async def send(self, event):
        self.sent.append(event)
        if event["type"] == "response.create":
            self.requests.put_nowait(event)
        return not self.closed

    async def close(self):
        self.closed = True

    def outputs(self):
        return [e["item"] for e in self.sent if e.get("item", {}).get("type") == "function_call_output"]


@pytest.fixture
def room(monkeypatch):
    monkeypatch.setattr("clawvoice.team.overseer.QUIET_SECONDS", 0.001)
    result = make_room()
    result.router_mode = "overseer"
    result.default_agent = result.addressed = "tari"
    result.closed = False
    result._tasks = set()
    result.listener = Session()
    result.overseer = Overseer(result)
    registry = load_registry(environ={})
    for key, voice in result.voices.items():
        voice.agent = registry.get(key)
    result.reconnects = 0

    async def reconnect(_session):
        result.reconnects += 1
        result.overseer.disconnected()

    result._listener_closed = reconnect
    return result


async def created(room, rid="r1"):
    await asyncio.wait_for(room.listener.requests.get(), 0.5)
    await room._on_listener({"type": "response.created", "response": {"id": rid}})


async def call(room, agent, *, rid="r1", cid="c1", reason="Clear change of specialty", **extra):
    event = {"type": "response.function_call_arguments.done", "response_id": rid, "call_id": cid,
             "name": "call_agent", "arguments": json.dumps({"agent": agent, "reason": reason}), **extra}
    await room._on_listener(event)
    return event


async def done(room, rid="r1", *, status="completed", code="", output=None):
    await room._on_listener({"type": "response.done", "response": {
        "id": rid, "status": status, "status_details": {"error": {"code": code}}, "output": output or [],
    }})


async def settled(room):
    await asyncio.wait_for(room.overseer.worker, 0.5)


async def speech(room, item, text):
    await room._on_listener({"type": "input_audio_buffer.speech_started", "item_id": item})
    await room._on_listener({"type": "input_audio_buffer.speech_stopped", "item_id": item})
    await room._on_listener({"type": "conversation.item.input_audio_transcription.completed",
                             "item_id": item, "transcript": text})


def test_schema_prompt_and_feature_flag_are_configured(monkeypatch):
    agents = load_registry(environ={}).all()
    assert tools(["custom-a", "custom-b"])[0]["parameters"]["properties"]["agent"]["enum"] == ["custom-a", "custom-b"]
    prompt = instructions(agents, agents[-1].id)
    assert all(a.display_name in prompt for a in agents)
    assert "token_ref" not in prompt and "gateway" not in prompt
    monkeypatch.delenv("CLAWVOICE_TEAM_ROUTER_MODE", raising=False)
    assert router_mode() == "overseer"
    monkeypatch.setenv("CLAWVOICE_TEAM_ROUTER_MODE", "names")
    assert router_mode() == "names"
    monkeypatch.setenv("CLAWVOICE_TEAM_ROUTER_MODE", "typo")
    with pytest.raises(ValueError):
        router_mode()


def test_listener_is_pinned_text_only_raw_audio_with_real_tools(room, monkeypatch):
    monkeypatch.setenv("BOSON_API_KEY", "offline-placeholder")
    room.settings = load_settings()
    session = room._new_listener()
    assert session.config["model"] == MODEL == "higgs-realtime-1.5-preview"
    assert session.config["output_modalities"] == ["text"]
    assert session.config["audio"]["input"]["turn_detection"]["type"] == "server_vad"
    assert not session.config["audio"]["input"]["turn_detection"]["create_response"]
    assert session.config["tools"][0]["name"] == "call_agent"
    room.overseer = None
    legacy = room._new_listener()
    assert legacy.config["tools"] == []


@pytest.mark.parametrize("first,words,destination", [
    ("tari", "Hey Astra, what project is active?", "astra"),
    ("astra", "What's on my calendar today?", "tari"),
    ("tari", "What was the last website change?", "astra"),
])
async def test_actual_tool_decision_precedes_dispatch(room, first, words, destination):
    room.addressed = first
    await speech(room, "mic1", words)
    await created(room)
    event = await call(room, destination)
    assert not any(v.asked for v in room.voices.values())
    assert room.addressed == first
    # Replay the arguments event; one matching function output, no duplicate dispatch.
    await room._on_listener(event)
    assert [o["call_id"] for o in room.listener.outputs()] == ["c1"]
    await done(room)
    await settled(room)
    assert room.addressed == destination
    assert room.voices[destination].asked == [words]
    assert not room.voices[first].asked
    assert not room.floor.awaiting
    # Raw mic routing: the ASR text is never re-injected into the overseer.
    assert not any(e.get("item", {}).get("role") == "user" for e in room.listener.sent)
    assert any(data.get("how") == "overseer" for kind, data in room.messages if kind == "team.user")


async def test_no_call_and_followups_keep_active_and_ignore_model_text(room):
    room.addressed = "astra"
    for i, request in enumerate(["Can you make the title bigger?", "Actually use a darker blue.", "Thanks, perfect."]):
        await room.typed(request)
        await created(room, f"r{i}")
        await room._on_listener({"type": "response.output_text.done", "response_id": f"r{i}", "text": "I am an audible assistant"})
        await done(room, f"r{i}")
        await settled(room)
    assert len(room.voices["astra"].asked) == 3
    assert not room.voices["tari"].asked
    assert room.addressed == "astra"
    assert "audible assistant" not in str(room.messages)
    assert not any(data.get("reason") for kind, data in room.messages if kind == "team.user")


async def test_explicit_name_overrides_contradictory_tool(room):
    await room.typed("Astra, what project is active?")
    await created(room)
    await call(room, "tari")
    assert json.loads(room.listener.outputs()[0]["output"])["error"] == "explicit_address_takes_priority"
    await done(room)
    await settled(room)
    assert room.addressed == "astra"
    assert room.voices["astra"].asked and not room.voices["tari"].asked
    assert not any(data.get("how") == "overseer" for kind, data in room.messages if kind == "team.user")


@pytest.mark.parametrize("extra", [
    {"name": "delegate_task"}, {"arguments": "oops"},
    {"arguments": '{"agent":"unknown","reason":"x"}'},
    {"arguments": '{"agent":[],"reason":"x"}'},
    {"arguments": '{"agent":"astra","reason":""}'},
])
async def test_invalid_calls_are_settled_and_ambiguous_turn_not_dispatched(room, extra):
    await room.typed("Check that for me.")
    await created(room)
    await call(room, "astra", **extra)
    await done(room)
    await settled(room)
    assert not json.loads(room.listener.outputs()[0]["output"])["ok"]
    assert not any(v.asked for v in room.voices.values())
    assert any(data.get("status") == "routing_failed" for _, data in room.messages)
    assert not room.floor.awaiting


async def test_typed_uses_same_policy_and_sends_only_user_input_and_public_roster(room):
    await room.typed("What's the latest website change?")
    await created(room)
    await call(room, "astra")
    await done(room)
    await settled(room)
    assert room.voices["astra"].asked == ["What's the latest website change?"]
    user_items = [e["item"] for e in room.listener.sent if e.get("item", {}).get("role") == "user"]
    assert len(user_items) == 1
    assert user_items[0]["content"][0]["text"] == room.voices["astra"].asked[0]
    assert not room.voices["tari"].asked
    assert not any("input_audio_buffer.append" == e["type"] for e in room.listener.sent)


async def test_superseded_decision_and_late_asr_cannot_dispatch_or_switch(room):
    await speech(room, "old", "What was the website change?")
    await created(room, "old-response")
    await room._on_listener({"type": "input_audio_buffer.speech_started", "item_id": "new"})
    await call(room, "astra", rid="old-response", cid="old-call")
    assert not json.loads(room.listener.outputs()[-1]["output"])["ok"]
    await room._on_listener({"type": "conversation.item.input_audio_transcription.completed",
                             "item_id": "old", "transcript": "Something old and different"})
    await room._on_listener({"type": "input_audio_buffer.speech_stopped", "item_id": "new"})
    await room._on_listener({"type": "conversation.item.input_audio_transcription.completed",
                             "item_id": "new", "transcript": "What's on my calendar?"})
    await done(room, "old-response")
    await created(room, "new-response")
    await call(room, "tari", rid="old-response", cid="late")
    await done(room, "old-response")
    assert not any(v.asked for v in room.voices.values())
    await done(room, "new-response")
    await settled(room)
    assert room.voices["tari"].asked == ["What's on my calendar?"]
    assert not room.voices["astra"].asked


async def test_cumulative_reused_item_and_duplicate_completion_dispatch_once(room):
    first = "Hey Astra, what project is active?"
    await speech(room, "reused", first)
    await created(room)
    await call(room, "astra")
    await done(room)
    await settled(room)
    await room._on_listener({"type": "conversation.item.input_audio_transcription.completed",
                             "item_id": "reused", "transcript": first})
    await speech(room, "reused", first + " What is its title?")
    await created(room, "r2")
    await done(room, "r2")
    await settled(room)
    assert room.voices["astra"].asked == [first, "What is its title?"]


async def test_retry_once_only_for_upstream_error(room):
    await room.typed("What was the latest website change?")
    await created(room, "failed")
    await done(room, "failed", status="failed", code="upstream_error")
    await created(room, "retry")
    await call(room, "astra", rid="retry")
    await done(room, "retry")
    await settled(room)
    assert len(room.voices["astra"].asked) == 1
    assert sum(e["type"] == "response.create" for e in room.listener.sent) == 2


async def test_second_failure_is_visible_without_dispatch(room):
    await room.typed("Check that thing.")
    for rid in ("first", "retry"):
        await created(room, rid)
        await done(room, rid, status="failed", code="upstream_error")
    await settled(room)
    assert not any(v.asked for v in room.voices.values())
    assert not room.floor.awaiting


@pytest.mark.parametrize("text,expected", [("Astra, what project is active?", "astra"), ("Check that.", None)])
async def test_timeout_releases_floor_and_replaces_socket(room, monkeypatch, text, expected):
    monkeypatch.setattr("clawvoice.team.overseer.RESPONSE_SECONDS", 0.015)
    await room.typed(text)
    await created(room)
    await settled(room)  # no response.done ever arrives
    assert room.listener.closed and room.reconnects == 1
    assert not room.floor.awaiting
    assert [key for key, v in room.voices.items() if v.asked] == ([expected] if expected else [])


async def test_unsolicited_auto_response_and_function_output_are_not_new_user_turns(room):
    await room._on_listener({"type": "response.created", "response": {"id": "auto"}})
    await room.typed("Astra, what project is active?")
    await asyncio.sleep(0.015)
    assert room.listener.requests.empty()
    await call(room, "tari", rid="auto", cid="auto-call")
    await done(room, "auto")
    await created(room, "requested")
    await done(room, "requested")
    await settled(room)
    assert room.voices["astra"].asked == ["Astra, what project is active?"]
    assert not room.voices["tari"].asked


async def test_handoff_cuts_audio_not_work_and_stop_asks_nobody(room):
    playing = ready_reply(room, "astra")
    await room.floor.tick()
    room.voices["astra"].spoke = True
    queued = ready_reply(room, "astra")
    await room.typed("What's on my calendar today?")
    await created(room)
    await call(room, "tari")
    await done(room)
    await settled(room)
    assert playing.outcome == "cut" and not playing.cancelled
    assert queued.outcome == "handed_over" and not queued.cancelled
    assert room.voices["astra"].backgrounded
    await room.typed("Stop talking.")
    assert all(v.stopped == 1 for v in room.voices.values())
    assert not room.voices["astra"].asked
    assert len(room.voices["tari"].asked) == 1


async def test_transcription_timeout_is_bounded_and_typed_during_speech_is_visible(room):
    await room._on_listener({"type": "input_audio_buffer.speech_started", "item_id": "lost"})
    await room.typed("Check my calendar.")
    assert any("Finish speaking" in d.get("text", "") for _, d in room.messages)
    await room._on_listener({"type": "input_audio_buffer.speech_stopped", "item_id": "lost"})
    room.floor.transcript_done("lost")
    room.overseer.latest.ended = time.monotonic() - 20
    room.overseer.tick()
    assert not room.floor.awaiting
    assert not any(v.asked for v in room.voices.values())


async def test_completed_output_call_fallback_and_explicit_multi_agent_exception(room):
    await room.typed("Astra, what is active? Tari, what is on my calendar?")
    await created(room)
    await done(room, output=[{"type": "function_call", "call_id": "c1", "name": "call_agent",
                             "arguments": '{"agent":"astra","reason":"Explicit address"}'}])
    await settled(room)
    assert len(room.voices["astra"].asked) == len(room.voices["tari"].asked) == 1
    assert room.addressed == "astra"
    assert len(room.listener.outputs()) == 1


async def test_deferred_asr_can_flush_while_routing_holds_the_floor(room, monkeypatch):
    monkeypatch.setattr("clawvoice.team.room.TICK_SECONDS", 0.001)
    room._dirty = False
    await room._on_listener({"type": "input_audio_buffer.speech_started", "item_id": "reused"})
    await room._on_listener({"type": "conversation.item.input_audio_transcription.completed",
                             "item_id": "reused", "transcript": "Astra, what project is active?"})
    assert room._deferred is not None
    await room._on_listener({"type": "input_audio_buffer.speech_stopped", "item_id": "reused"})
    room.floor.transcript_done("reused")
    item, text, started, _ = room._deferred
    room._deferred = (item, text, started, time.monotonic() - 3)
    ticker = asyncio.create_task(room._ticker())
    try:
        await created(room)
        await done(room)
        await settled(room)
        assert room.voices["astra"].asked == ["Astra, what project is active?"]
        assert not room.floor.awaiting
    finally:
        ticker.cancel()
        await asyncio.gather(ticker, return_exceptions=True)
