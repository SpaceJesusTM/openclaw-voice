"""Team mode connection: one browser, every configured agent, one speaking floor.

    WebRTC in -> TeamRoom (listener, name routing, agent voices, floor) -> WebRTC out

Same transport, RTVI channel and process-level runtime as single-agent mode;
only the middle of the pipeline differs. Jobs and caches live in each agent's
AgentRuntime, so leaving the room ends only this voice session.
"""

from __future__ import annotations

import os
import time
import uuid

from loguru import logger
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker, ProcessorUnusablePolicy
from pipecat.processors.frameworks.rtvi import RTVIProcessor
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import create_transport
from pipecat.transports.base_transport import TransportParams
from pipecat.workers.runner import WorkerRunner

from clawvoice.config import load_settings
from clawvoice.runtime import Runtime
from clawvoice.team.room import TeamRoom
from clawvoice.voice.telemetry import DiagnosticObserver, Trace

IDLE_TIMEOUT_SECS = float(os.environ.get("CLAWVOICE_IDLE_TIMEOUT_SECS", "1800"))


def team_members(runtime: Runtime) -> list:
    """Agents in a Team room: every configured agent (fixtures only when nothing else is configured)."""
    members = [rt for rt in runtime.agents.values() if not rt.agent.fixture]
    return members if len(members) >= 2 else list(runtime.agents.values())


async def team_bot(runner_args: RunnerArguments, runtime: Runtime) -> None:
    settings = load_settings()
    members = team_members(runtime)
    if len(members) < 2:
        raise RuntimeError("Team mode needs at least two configured agents")
    session_id = getattr(runner_args, "session_id", None) or uuid.uuid4().hex
    trace = Trace(settings.api_key, label="session-team", persist=settings.persist_traces)
    trace.record("session.start", mode="team", agents=[rt.agent.id for rt in members], session=session_id)

    transport = await create_transport(
        runner_args,
        {"webrtc": lambda: TransportParams(audio_in_enabled=True, audio_out_enabled=True)},
    )
    room = TeamRoom(
        runtimes=members,
        default_agent=runtime.registry.default_id,
        settings=settings,
        trace=trace,
        room_id=session_id,
    )
    rtvi = RTVIProcessor()
    room.send_ui = rtvi.send_server_message
    worker = PipelineWorker(
        Pipeline([transport.input(), room, transport.output()]),
        name="team-voice",
        params=PipelineParams(audio_in_sample_rate=24000, audio_out_sample_rate=24000),
        rtvi_processor=rtvi,
        observers=[DiagnosticObserver(trace)],
        processor_unusable_policy=ProcessorUnusablePolicy.CANCEL,
        idle_timeout_secs=IDLE_TIMEOUT_SECS,
    )
    runner = WorkerRunner(handle_sigint=runner_args.handle_sigint)

    @rtvi.event_handler("on_client_ready")
    async def client_ready(processor):
        room.client_ready()
        await rtvi.send_server_message({"kind": "clawvoice.hello", "team": True, "session_id": session_id, "states": room.state()})

    @rtvi.event_handler("on_client_message")
    async def client_message(processor, message):
        kind = getattr(message, "type", None)
        data = getattr(message, "data", None) or {}
        if not isinstance(data, dict):
            data = {}
        if kind == "clawvoice.alive":
            room.on_client_alive()
        elif kind == "clawvoice.say" and data.get("text"):
            await room.typed(str(data["text"]))
        elif kind == "clawvoice.cancel" and data.get("job_id") and data.get("agent") in room.runtimes:
            job_id = str(data["job_id"]).upper()
            result = await room.runtimes[data["agent"]].jobs.cancel(job_id, source="ui")
            room.ui("cancel", {"job_id": job_id, "agent": data["agent"], **result})
        elif kind == "clawvoice.state":
            await rtvi.send_server_message({"kind": "clawvoice.hello", "team": True, "session_id": session_id, "states": room.state()})

    @transport.event_handler("on_client_disconnected")
    async def disconnected(transport, client):
        trace.record("transport.disconnected")
        await runner.cancel()

    started = time.monotonic()
    try:
        await runner.add_workers(worker)
        await runner.run()
    finally:
        await room.close()
        trace.record("session.end", mode="team", seconds=round(time.monotonic() - started))
        logger.info(f"Team voice session {session_id} ended; background jobs continue in each agent's runtime")
