"""Per-connection Pipecat voice pipeline (one browser = one Higgs session).

Pipeline (unchanged from the validated experiment):
    WebRTC in -> RealtimeTextBridge -> user aggregator -> Higgs 1.5 Preview
    -> WebRTC out -> assistant aggregator

Agent identity, instructions, voice and tools come from the selected agent's
configuration. Jobs and cache are process-level (see runtime.py), so a
disconnect ends only this voice session.
"""

from __future__ import annotations

import os
import uuid

from loguru import logger
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.frames.frames import LLMRunFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker, ProcessorUnusablePolicy
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.processors.frameworks.rtvi import RTVIProcessor
from pipecat.processors.frameworks.rtvi.observer import RTVIFunctionCallReportLevel, RTVIObserverParams
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import create_transport
from pipecat.transports.base_transport import TransportParams
from pipecat.workers.runner import WorkerRunner

from clawvoice.config import MODEL, load_settings
from clawvoice.runtime import get_runtime
from clawvoice.voice.floor import FloorObserver
from clawvoice.voice.instructions import build_instructions
from clawvoice.voice.service import VerifiedBosonService
from clawvoice.voice.session import VoiceSession
from clawvoice.voice.telemetry import DiagnosticObserver, Trace
from clawvoice.voice.text_bridge import RealtimeTextBridge
from clawvoice.voice.tools import VoiceTools

IDLE_TIMEOUT_SECS = float(os.environ.get("CLAWVOICE_IDLE_TIMEOUT_SECS", "1800"))


async def bot(runner_args: RunnerArguments):
    runtime = get_runtime()
    body = runner_args.body if isinstance(runner_args.body, dict) else {}
    agent_runtime = runtime.agent(body.get("agent") if isinstance(body.get("agent"), str) else None)
    agent = agent_runtime.agent
    settings = load_settings()
    session_id = getattr(runner_args, "session_id", None) or uuid.uuid4().hex
    trace = Trace(settings.api_key, label=f"session-{agent.id}", persist=settings.persist_traces)
    session = VoiceSession(session_id=session_id, runtime=agent_runtime, trace=trace)
    trace.record("session.start", agent=agent.id, session=session_id)

    transport = await create_transport(
        runner_args,
        {"webrtc": lambda: TransportParams(audio_in_enabled=True, audio_out_enabled=True)},
    )
    tools = ToolsSchema(standard_tools=VoiceTools(agent_runtime, session).schemas())
    llm = VerifiedBosonService(
        settings,
        trace,
        tools,
        instructions=build_instructions(agent),
        voice=settings.voice_override or agent.voice,
    )
    session.llm = llm
    context = LLMContext(tools=tools)
    user, assistant = LLMContextAggregatorPair(context, realtime_service_mode=True)
    rtvi = RTVIProcessor()
    session.rtvi = rtvi
    worker = PipelineWorker(
        Pipeline([transport.input(), RealtimeTextBridge(llm), user, llm, transport.output(), assistant]),
        name="voice",
        params=PipelineParams(enable_metrics=True, audio_in_sample_rate=24000, audio_out_sample_rate=24000),
        rtvi_processor=rtvi,
        observers=[DiagnosticObserver(trace), FloorObserver(session.coordinator)],
        rtvi_observer_params=RTVIObserverParams(
            function_call_report_level={
                name: RTVIFunctionCallReportLevel.FULL
                for name in ("get_context", "delegate_task", "check_tasks", "cancel_task")
            }
        ),
        processor_unusable_policy=ProcessorUnusablePolicy.CANCEL,
        idle_timeout_secs=IDLE_TIMEOUT_SECS,
    )
    runner = WorkerRunner(handle_sigint=runner_args.handle_sigint)

    @llm.event_handler("on_preview_event")
    async def diagnostic(service, entry):
        await rtvi.send_server_message({"kind": "preview-diagnostic", **entry})

    @llm.event_handler("on_latency")
    async def latency(service, entry):
        session.ui("latency", {"origin": entry.get("origin"), "first_audio_ms": entry.get("first_audio_ms")})

    @rtvi.event_handler("on_client_ready")
    async def client_ready(processor):
        session.client_ready = True
        await rtvi.send_server_message({"kind": "preview-model", "configured": MODEL, "acknowledged": trace.model})
        session.ui("hello", {"session_id": session_id, "state": agent_runtime.state()})
        agent_runtime.attach(session)
        session.coordinator.start()
        await worker.queue_frame(LLMRunFrame())  # short greeting, as in the official tutorial

    @rtvi.event_handler("on_client_message")
    async def client_message(processor, message):
        kind = getattr(message, "type", None)
        data = getattr(message, "data", None) or {}
        if kind == "clawvoice.alive":
            # UI keepalive: a client can be present without sending audio (typed-only mode).
            session.coordinator.on_client_audio()
        elif kind == "clawvoice.cancel" and isinstance(data, dict) and data.get("job_id"):
            result = await agent_runtime.jobs.cancel(str(data["job_id"]).upper(), source="ui")
            session.ui("cancel", {"job_id": str(data["job_id"]).upper(), **result})
        elif kind == "clawvoice.state":
            session.ui("state", {"state": agent_runtime.state()})

    @llm.event_handler("on_session_terminated")
    async def session_terminated(service, event_type, event):
        trace.record("session.terminated", event_type=event_type)
        await rtvi.send_error(f"Boson ended the session: {event_type}")
        await runner.cancel()

    @transport.event_handler("on_client_disconnected")
    async def disconnected(transport, client):
        trace.record("transport.disconnected")
        await runner.cancel()

    try:
        await runner.add_workers(worker)
        await runner.run()
    finally:
        agent_runtime.detach(session)
        await session.coordinator.stop()
        trace.record("session.end", running_jobs=sum(1 for j in agent_runtime.jobs.public(10) if j["status"] in ("accepted", "queued", "running", "verifying")))
        logger.info(f"Voice session {session_id} ended; background jobs continue in the runtime")
