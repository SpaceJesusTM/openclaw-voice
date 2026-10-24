"""Offline voice-layer tests. Boson's socket is mocked; no API calls."""

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.frames.frames import InterruptionFrame, LLMMessagesAppendFrame, LLMRunFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.tests.utils import SleepFrame, run_test
from websockets.exceptions import ConnectionClosedError

from clawvoice.config import MODEL, Settings
from clawvoice.registry import default_registry
from clawvoice.runtime import AgentRuntime
from clawvoice.voice.instructions import build_instructions
from clawvoice.voice.service import VerifiedBosonService
from clawvoice.voice.session import VoiceSession
from clawvoice.voice.telemetry import Trace
from clawvoice.voice.text_bridge import RealtimeTextBridge
from clawvoice.voice.tools import VoiceTools
from conftest import astra_config, tari_fixture_config


def make_llm(agent=None, tools=None):
    agent = agent or tari_fixture_config()
    trace = Trace("test-key", persist=False)
    tools = tools or ToolsSchema(standard_tools=[])
    llm = VerifiedBosonService(
        Settings(api_key="test-key"), trace, tools, instructions=build_instructions(agent), voice=agent.voice
    )
    return llm, tools, trace


async def test_typed_frame_reaches_sdk_once_through_real_aggregator_pipeline():
    llm, tools, _ = make_llm()
    llm._connect = AsyncMock()
    llm._disconnect = AsyncMock()
    llm._ws_send = AsyncMock()
    llm._api_session_ready = True
    user, assistant = LLMContextAggregatorPair(LLMContext(tools=tools), realtime_service_mode=True)
    await run_test(
        Pipeline([RealtimeTextBridge(llm), user, llm, assistant]),
        frames_to_send=[
            LLMRunFrame(),
            SleepFrame(0.05),
            LLMMessagesAppendFrame(messages=[{"role": "user", "content": "typed regression"}], run_llm=True),
            SleepFrame(0.05),
        ],
        start_timeout=10,
    )
    sent = [call.args[0] for call in llm._ws_send.call_args_list]
    user_items = [event for event in sent if event.get("item", {}).get("role") == "user"]
    assert len(user_items) == 1 and user_items[0]["item"]["content"][0]["text"] == "typed regression"
    assert sum(event["type"] == "response.create" for event in sent) == 2


async def test_cancelled_done_before_speech_started_keeps_native_truncation_working():
    llm, _, _ = make_llm()
    llm._ws_send = AsyncMock()
    llm.push_frame = AsyncMock()
    llm.broadcast_frame = AsyncMock()
    llm.broadcast_interruption = AsyncMock()
    llm._boson_active_response_ids.add("r1")
    llm._current_audio_response = SimpleNamespace(
        item_id="assistant1", response_id="r1", content_index=0,
        start_time_ms=int(time.time() * 1000) - 400, total_size=24000,
    )
    await llm._dispatch_boson_server_event(
        {"type": "response.done", "response": {"id": "r1", "status": "cancelled", "output": [], "usage": None}}
    )
    assert llm._current_audio_response is not None
    await llm._dispatch_boson_server_event({"type": "input_audio_buffer.speech_started", "item_id": "u1"})
    cuts = [c.args[0] for c in llm._ws_send.call_args_list if c.args[0]["type"] == "conversation.item.truncate"]
    assert len(cuts) == 1 and cuts[0]["item_id"] == "assistant1" and 0 <= cuts[0]["audio_end_ms"] <= 500
    assert llm._current_audio_response is None


async def test_retained_audio_is_cleared_after_transport_playback_finishes():
    llm, _, _ = make_llm()
    llm._current_audio_response = object()
    await llm._handle_bot_stopped_speaking()
    assert llm._current_audio_response is None


async def test_model_acknowledgement_is_verified_before_sdk_marks_session_ready():
    for model in ["higgs-realtime", None]:
        llm, _, trace = make_llm()
        llm.push_error = AsyncMock()
        result = await llm._dispatch_boson_server_event({"type": "session.created", "session": {"model": model}})
        assert result is False and not llm._api_session_ready and trace.model is None
        assert llm.push_error.call_args.kwargs["force_treat_as_permanent"]
    llm, _, trace = make_llm()
    assert await llm._dispatch_boson_server_event({"type": "session.created", "session": {"model": MODEL}})
    assert llm._api_session_ready and trace.model == MODEL


async def test_runtime_model_change_and_api_errors_are_refused():
    llm, _, _ = make_llm()
    llm._ws_send = AsyncMock()
    try:
        await llm.send_client_event({"type": "session.update", "session": {"model": "higgs-realtime"}})
        raise AssertionError("legacy model accepted")
    except ValueError:
        pass
    llm.push_error = AsyncMock()
    assert await llm._dispatch_boson_server_event({"type": "error", "error": {"code": "invalid_api_key", "message": "x"}}) is False


async def test_upstream_disconnect_emits_an_error():
    class DroppedSocket:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise ConnectionClosedError(None, None)

    llm, _, _ = make_llm()
    llm._websocket = DroppedSocket()
    llm.push_error = AsyncMock()
    llm._mark_websocket_disconnected = AsyncMock()
    await llm._receive_task_handler()
    assert "closed unexpectedly" in llm.push_error.call_args.kwargs["error_msg"]


async def runtime_and_session(gateway, fixture_dir, agent=None):
    runtime = AgentRuntime(agent or astra_config(gateway.port), default_registry(), state_directory=fixture_dir.parent / "state")
    await runtime.start()
    session = VoiceSession(session_id="sess-1", runtime=runtime, trace=Trace(persist=False))
    return runtime, session


async def test_session_payload_advertises_config_built_tools(gateway, fixture_dir):
    runtime, session = await runtime_and_session(gateway, fixture_dir)
    try:
        tools = ToolsSchema(standard_tools=VoiceTools(runtime, session).schemas())
        llm, _, _ = make_llm(runtime.agent, tools)
        llm._ws_send = AsyncMock()
        await llm._send_session_update()
        payload = llm._ws_send.call_args.args[0]["session"]
        assert payload["model"] == MODEL and payload["output_modalities"] == ["audio"]
        by_name = {t["name"]: t for t in payload["tools"]}
        assert set(by_name) == {"get_context", "delegate_task", "check_tasks", "cancel_task"}
        assert by_name["get_context"]["parameters"]["properties"]["topic"]["enum"] == ["project.active", "tests.last", "jobs.recent"]
        assert by_name["delegate_task"]["parameters"]["properties"]["target"]["enum"] == ["tests", "project", "other"]
        assert "You are Astra" in payload["instructions"]
        # Cached context answers questions; a request to act is always delegated.
        assert "never performs an action" in payload["instructions"]
        assert "a request to do something goes to delegate_task" in by_name["get_context"]["description"]
        # Every tool is asynchronous: barge-in never cancels a tool call.
        llm._sync_registered_tool_handlers(tools)
        assert all(not entry.cancel_on_interruption for entry in llm._functions.values() if entry.function_name)
    finally:
        await runtime.stop()


def params(name, arguments, call_id="call-1"):
    callback = AsyncMock()
    return SimpleNamespace(function_name=name, tool_call_id=call_id, arguments=arguments, result_callback=callback), callback


async def test_delegate_returns_before_job_and_interruption_does_not_cancel_it(gateway, fixture_dir):
    gateway.delay_scale = 1.0
    runtime, session = await runtime_and_session(gateway, fixture_dir)
    try:
        tools = VoiceTools(runtime, session)
        schemas = {s.name: s for s in tools.schemas()}
        p, callback = params("delegate_task", {"request": "run the tests delay 0.6 seconds", "kind": "change", "target": "tests"})
        started = time.perf_counter()
        await schemas["delegate_task"].handler(p)
        assert (time.perf_counter() - started) < 0.2
        result = callback.call_args.args[0]
        assert result["status"] == "accepted" and "NOT done" in result["note"]
        job_id = result["job_id"]
        # A barge-in: the realtime service cancels interruptible calls only.
        llm, _, _ = make_llm(runtime.agent, ToolsSchema(standard_tools=tools.schemas()))
        llm._sync_registered_tool_handlers(ToolsSchema(standard_tools=tools.schemas()))
        await llm._handle_interruptions(InterruptionFrame())
        # Meanwhile a cached question is answered while the job is still running.
        p2, callback2 = params("get_context", {"topic": "project.active"}, "call-2")
        await schemas["get_context"].handler(p2)
        assert callback2.call_args.args[0]["status"] == "fresh"
        assert runtime.jobs.get(job_id).status in ("accepted", "running")
        for _ in range(300):
            if runtime.jobs.get(job_id).terminal:
                break
            await asyncio.sleep(0.02)
        assert runtime.jobs.get(job_id).status == "completed"
        p3, callback3 = params("check_tasks", {"job_id": job_id}, "call-3")
        await schemas["check_tasks"].handler(p3)
        assert callback3.call_args.args[0]["task"]["verification"].startswith("verified")
    finally:
        await runtime.stop()


async def test_notice_reaches_higgs_as_one_user_item_and_response(gateway, fixture_dir):
    runtime, session = await runtime_and_session(gateway, fixture_dir)
    try:
        llm, tools, _ = make_llm(runtime.agent)
        llm._connect = AsyncMock()
        llm._disconnect = AsyncMock()
        llm._ws_send = AsyncMock()
        llm._api_session_ready = True
        session.llm = llm
        user, assistant = LLMContextAggregatorPair(LLMContext(tools=tools), realtime_service_mode=True)

        async def inject_later():
            await asyncio.sleep(0.05)
            await session._inject("[ClawVoice notice] A background task for Astra just finished. Result: Verified.")

        task = asyncio.create_task(inject_later())
        await run_test(
            Pipeline([RealtimeTextBridge(llm), user, llm, assistant]),
            frames_to_send=[LLMRunFrame(), SleepFrame(0.3)],
            start_timeout=10,
        )
        await task
        sent = [call.args[0] for call in llm._ws_send.call_args_list]
        notices = [e for e in sent if "[ClawVoice notice]" in str(e.get("item", {}))]
        assert len(notices) == 1 and notices[0]["item"]["role"] == "user"
        assert sum(e["type"] == "response.create" for e in sent) == 2  # greeting + notice
    finally:
        await runtime.stop()


async def test_latency_from_speech_end_to_first_audio_is_measured():
    llm, _, trace = make_llm()
    seen = []

    async def on_latency(service, entry):
        seen.append(entry)

    llm.add_event_handler("on_latency", on_latency)
    llm._api_session_ready = True
    await llm._dispatch_boson_server_event({"type": "input_audio_buffer.speech_stopped", "item_id": "u1"})
    await asyncio.sleep(0.02)
    llm._boson_active_response_ids.add("r9")
    llm._handle_evt_audio_delta = AsyncMock()
    await llm._dispatch_boson_server_event({"type": "response.output_audio.delta", "response_id": "r9", "delta": "AAAA"})
    assert seen and seen[0]["origin"] == "speech_end" and seen[0]["first_audio_ms"] >= 15


async def test_tool_result_latency_counts_the_answer_response_only():
    llm, _, _ = make_llm()
    llm._ws_send = AsyncMock()
    llm._handle_evt_audio_delta = AsyncMock()
    llm._handle_evt_response_created = lambda evt: None
    seen = []

    async def on_latency(service, entry):
        seen.append(entry["origin"])

    llm.add_event_handler("on_latency", on_latency)
    await llm.send_client_event({"type": "conversation.item.create", "item": {"type": "function_call_output", "call_id": "c", "output": "{}"}})
    # Audio of the response that issued the call (a preamble) is not the answer.
    await llm._dispatch_boson_server_event({"type": "response.output_audio.delta", "response_id": "pre", "delta": "AA"})
    assert seen == []
    await llm._dispatch_boson_server_event({"type": "response.created", "response": {"id": "ans"}})
    await llm._dispatch_boson_server_event({"type": "response.output_audio.delta", "response_id": "ans", "delta": "AA"})
    assert seen == ["tool_result"]
