import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pipecat.frames.frames import InterruptionFrame
from pipecat.pipeline.job_context import JobParams
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineWorker
from pipecat.workers.runner import WorkerRunner

from bot import make_llm
from config import MODEL, URL, Settings, load_settings
from mock_worker import MockPlannerWorker, Passthrough
from planner import planner_result, planner_tool
from probe import Evidence, wait_for
from telemetry import Trace, sanitize


def test_reuse_shared_env_and_precedence_without_exposing_key(tmp_path):
    direct = tmp_path / "higgs-realtime-1.5"
    root = tmp_path / "pipecat-higgs-1.5"
    direct.mkdir()
    root.mkdir()
    (direct / ".env").write_text("BOSON_API_KEY=shared-private-key\n")
    (root / ".env").write_text("BOSON_API_KEY=\nBOSON_VOICE=other\n")
    settings = load_settings({}, root=root)
    assert settings.api_key == "shared-private-key"
    assert settings.voice == "other"
    assert settings.model == MODEL and settings.url == URL
    assert "shared-private-key" not in repr(settings)
    assert load_settings({"BOSON_API_KEY": "process-key"}, root=root).api_key == "process-key"


def test_invalid_configuration_fails_early(tmp_path):
    with pytest.raises(ValueError, match="BOSON_API_KEY missing"):
        load_settings({}, root=tmp_path)
    for model in ["higgs-realtime", "", "other"]:
        with pytest.raises(ValueError, match="No fallback"):
            Settings(api_key="test", model=model)
    for url in [
        "ws://api.boson.ai/v1/realtime",
        "wss://other.example/v1/realtime",
        URL + "&model=higgs-realtime",
    ]:
        with pytest.raises(ValueError, match="official endpoint"):
            Settings(api_key="test", url=url)
    for kwargs in [{"vad_threshold": 2}, {"vad_silence_ms": 1}, {"noise_reduction": "bad"}]:
        with pytest.raises(ValueError):
            Settings(api_key="test", **kwargs)


def test_optional_documented_vad_settings():
    settings = Settings(api_key="test", vad_threshold=0.8, vad_silence_ms=700, noise_reduction="near_field")
    assert settings.turn_detection == {"type": "server_vad", "threshold": 0.8, "silence_duration_ms": 700}
    assert Settings(api_key="test").turn_detection == {"type": "server_vad"}


def test_diagnostics_strip_credentials_and_audio(tmp_path):
    event = {
        "type": "response.output_audio.delta",
        "delta": "ABCD",
        "session": {"client_secret": "private-key"},
        "message": "private-key",
    }
    safe = sanitize(event, "private-key")
    assert safe["delta"] == "[4 base64 characters]"
    assert "private-key" not in json.dumps(safe)
    trace = Trace("private-key", path=tmp_path / "trace.jsonl")
    trace.record("test", event=event)
    assert "private-key" not in trace.path.read_text()


def test_planner_fixture_and_validation():
    result = planner_result({"day": "today"})
    assert result["detail"]["verification_code"] == "NEBULA-742"
    assert result["detail"]["entries"][0]["title"] == "Copper Otter rehearsal"
    for args in [{}, {"day": "tomorrow"}, {"day": "today", "extra": True}, None]:
        assert planner_result(args)["error"] == "invalid_arguments"


async def test_planner_returns_failures_as_results_and_survives_interruption(monkeypatch, tmp_path):
    trace = Trace(persist=False)
    handler = planner_tool(trace)
    assert handler._pipecat_cancel_on_interruption is False
    params = SimpleNamespace(
        tool_call_id="call1",
        arguments={"day": "today"},
        llm=SimpleNamespace(_call_event_handler=AsyncMock()),
        result_callback=AsyncMock(),
    )
    await handler(params)
    assert params.result_callback.call_args.args[0]["detail"]["verification_code"] == "NEBULA-742"
    monkeypatch.setattr("planner.FIXTURE", tmp_path / "missing.json")
    await handler(params)
    assert params.result_callback.call_args.args[0]["error"] == "FileNotFoundError"


async def test_sdk_payload_is_preview_and_native_search_stays_server_side():
    trace = Trace(persist=False)
    llm, _ = make_llm(Settings(api_key="test", web_search=True), trace)
    llm._ws_send = AsyncMock()
    await llm._send_session_update()
    payload = llm._ws_send.call_args.args[0]
    assert payload["session"]["model"] == MODEL
    assert payload["session"]["output_modalities"] == ["audio"]
    assert {"type": "web_search"} in payload["session"]["tools"]
    assert llm.base_url == URL
    with pytest.raises(ValueError, match="Runtime session"):
        await llm.send_client_event({"type": "session.update", "session": {"model": "higgs-realtime"}})
    llm.call_function = AsyncMock()
    await llm._dispatch_boson_server_event(
        {
            "type": "response.output_item.added",
            "item": {
                "type": "function_call",
                "name": "web_search",
                "call_id": "search1",
                "executor": "server",
            },
        }
    )
    await llm._dispatch_boson_server_event(
        {
            "type": "response.function_call_arguments.done",
            "call_id": "search1",
            "name": "web_search",
            "arguments": "{}",
        }
    )
    llm.call_function.assert_not_called()
    assert "search1" not in llm._pending_function_calls


async def test_acknowledgements_verified_before_sdk_marks_session_ready():
    for model in ["higgs-realtime", None]:
        trace = Trace(persist=False)
        llm, _ = make_llm(Settings(api_key="test"), trace)
        llm.push_error = AsyncMock()
        assert (
            await llm._dispatch_boson_server_event({"type": "session.created", "session": {"model": model}})
            is False
        )
        assert not llm._api_session_ready
        assert trace.failure and trace.model is None
        assert llm.push_error.call_args.kwargs["force_treat_as_permanent"]
    llm, _ = make_llm(Settings(api_key="test"), Trace(persist=False))
    assert await llm._dispatch_boson_server_event({"type": "session.created", "session": {"model": MODEL}})
    assert llm._api_session_ready and llm.trace.model == MODEL
    llm.push_error = AsyncMock()
    assert (
        await llm._dispatch_boson_server_event({"type": "session.updated", "session": {"model": "other"}})
        is False
    )


async def test_api_error_stops_session_processing_with_diagnostics():
    llm, _ = make_llm(Settings(api_key="test"), Trace(persist=False))
    llm.push_error = AsyncMock()
    assert (
        await llm._dispatch_boson_server_event(
            {"type": "error", "error": {"code": "invalid_api_key", "message": "Rejected credential"}}
        )
        is False
    )
    assert llm.trace.failure
    assert llm.push_error.call_args.kwargs["force_treat_as_permanent"]


async def test_real_worker_bus_returns_mock_result_despite_voice_interruption():
    trace = Trace(persist=False)
    backend = MockPlannerWorker(trace)
    voice = PipelineWorker(
        Pipeline([Passthrough()]), name="voice-test", idle_timeout_secs=None, enable_rtvi=False
    )
    runner = WorkerRunner(handle_sigint=False)
    await runner.add_workers(backend, voice)
    task = asyncio.create_task(runner.run())
    try:
        async with asyncio.timeout(15):
            async with voice.job(
                "mock-planner", params=JobParams(name="lookup", payload={"day": "today"}, timeout=5)
            ) as job:
                await voice.queue_frame(InterruptionFrame())
            assert job.response["detail"]["verification_code"] == "NEBULA-742"
            assert any(event["kind"] == "worker.result" for event in trace.events)
    finally:
        await runner.cancel()
        await asyncio.wait_for(task, 10)


def test_probe_rejects_model_and_failed_turn_evidence():
    state = Evidence()
    state.receive(
        json.dumps(
            {
                "type": "server-message",
                "data": {"event": {"type": "session.created", "session": {"model": "higgs-realtime"}}},
            }
        )
    )
    assert state.failure
    state = Evidence()
    state.receive(
        json.dumps(
            {
                "type": "server-message",
                "data": {"event": {"type": "response.done", "response": {"status": "cancelled"}}},
            }
        )
    )
    assert state.failure
    assert state.turn_evidence(0, "planner")[0] is False


async def test_probe_timeout_is_failure():
    with pytest.raises(TimeoutError):
        await wait_for(lambda: False, Evidence(), 0.01)
