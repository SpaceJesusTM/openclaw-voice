import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

from pipecat.frames.frames import LLMMessagesAppendFrame, LLMRunFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.tests.utils import SleepFrame, run_test
from websockets.exceptions import ConnectionClosedError

from bot import make_llm
from config import Settings
from probe import Evidence
from telemetry import Trace
from text_bridge import RealtimeTextBridge


async def test_typed_frame_reaches_sdk_once_through_real_aggregator_pipeline():
    llm, tools = make_llm(Settings(api_key="test"), Trace(persist=False))
    llm._connect = AsyncMock()
    llm._disconnect = AsyncMock()
    llm._ws_send = AsyncMock()
    llm._api_session_ready = True
    user, assistant = LLMContextAggregatorPair(LLMContext(tools=tools), realtime_service_mode=True)
    pipeline = Pipeline([RealtimeTextBridge(llm), user, llm, assistant])
    await run_test(
        pipeline,
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
    assert len(user_items) == 1
    assert user_items[0]["item"]["content"][0]["text"] == "typed regression"
    assert sum(event["type"] == "response.create" for event in sent) == 2


def test_search_preamble_is_not_accepted_as_a_search_answer():
    state = Evidence()

    def emit(kind, data=None):
        state.receive(json.dumps({"type": kind, "data": data or {}}))

    def wire(event):
        emit("server-message", {"kind": "boson.recv", "event": event})

    emit("bot-llm-text", {"text": "Let me search."})
    wire(
        {
            "type": "response.output_item.added",
            "item": {"type": "function_call", "name": "web_search", "executor": "server", "call_id": "s"},
        }
    )
    wire({"type": "response.done", "response": {"status": "completed"}})
    wire(
        {
            "type": "conversation.item.added",
            "item": {"type": "function_call_output", "call_id": "s", "output": "search result"},
        }
    )
    emit("bot-stopped-speaking")
    assert state.turn_evidence(0, "search")[0] is False
    emit("bot-llm-text", {"text": "The official domain is boson.ai."})
    wire({"type": "response.done", "response": {"status": "completed"}})
    emit("bot-stopped-speaking")
    assert state.turn_evidence(0, "search")[0] is True


async def test_upstream_disconnect_emits_an_error():
    class DroppedSocket:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise ConnectionClosedError(None, None)

    llm, _ = make_llm(Settings(api_key="test"), Trace(persist=False))
    llm._websocket = DroppedSocket()
    llm.push_error = AsyncMock()
    llm._mark_websocket_disconnected = AsyncMock()
    await llm._receive_task_handler()
    assert "closed unexpectedly" in llm.push_error.call_args.kwargs["error_msg"]


async def test_wav_microphone_has_continuous_clock_across_silence_and_file(tmp_path):
    import wave
    from probe_audio import WavMicrophone

    path = tmp_path / "input.wav"
    with wave.open(str(path), "wb") as file:
        file.setparams((1, 2, 48000, 0, "NONE", "not compressed"))
        file.writeframes(bytes(960 * 2 * 3))
    microphone = WavMicrophone()
    try:
        first = await microphone.recv()
        microphone.play(path)
        second = await asyncio.wait_for(microphone.recv(), 1)
        assert second.pts == first.samples
        assert first.sample_rate == second.sample_rate == 48000
        for _ in range(6):
            frame = await asyncio.wait_for(microphone.recv(), 1)
        assert frame.pts > second.pts
    finally:
        microphone.stop()


async def test_cancelled_done_before_speech_started_keeps_native_truncation_working():
    llm, _ = make_llm(Settings(api_key="test"), Trace(persist=False))
    llm._ws_send = AsyncMock()
    llm.push_frame = AsyncMock()
    llm.broadcast_frame = AsyncMock()
    llm.broadcast_interruption = AsyncMock()
    llm._boson_active_response_ids.add("r1")
    llm._current_audio_response = SimpleNamespace(
        item_id="assistant1",
        response_id="r1",
        content_index=0,
        start_time_ms=int(time.time() * 1000) - 400,
        total_size=24000,
    )
    await llm._dispatch_boson_server_event(
        {
            "type": "response.done",
            "response": {"id": "r1", "status": "cancelled", "output": [], "usage": None},
        }
    )
    assert llm._current_audio_response is not None
    await llm._dispatch_boson_server_event({"type": "input_audio_buffer.speech_started", "item_id": "u1"})
    sent = [call.args[0] for call in llm._ws_send.call_args_list]
    cuts = [event for event in sent if event["type"] == "conversation.item.truncate"]
    assert len(cuts) == 1 and cuts[0]["item_id"] == "assistant1"
    assert 0 <= cuts[0]["audio_end_ms"] <= 500
    assert llm._current_audio_response is None


async def test_retained_audio_is_cleared_after_transport_playback_finishes():
    llm, _ = make_llm(Settings(api_key="test"), Trace(persist=False))
    llm._current_audio_response = object()
    await llm._handle_bot_stopped_speaking()
    assert llm._current_audio_response is None
