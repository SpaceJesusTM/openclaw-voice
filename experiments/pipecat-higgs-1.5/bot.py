"""Official Pipecat runner playground: uv run bot.py -t webrtc --host 127.0.0.1."""

from pipecat.adapters.schemas.tools_schema import AdapterType, ToolsSchema
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

from config import MODEL, load_settings, utf8_console
from mock_worker import MockPlannerWorker
from planner import planner_tool
from service import VerifiedBosonService
from telemetry import DiagnosticObserver, Trace, configure_logging
from text_bridge import RealtimeTextBridge


def make_llm(settings, trace):
    tools = ToolsSchema(
        standard_tools=[planner_tool(trace, settings.mock_worker)],
        custom_tools={AdapterType.OPENAI: [{"type": "web_search"}]} if settings.web_search else {},
    )
    return VerifiedBosonService(settings, trace, tools), tools


async def bot(runner_args: RunnerArguments):
    settings = load_settings()
    configure_logging(settings.api_key)
    trace = Trace(settings.api_key)
    transport = await create_transport(
        runner_args,
        {
            "webrtc": lambda: TransportParams(audio_in_enabled=True, audio_out_enabled=True),
        },
    )
    llm, tools = make_llm(settings, trace)
    context = LLMContext(tools=tools)
    user, assistant = LLMContextAggregatorPair(context, realtime_service_mode=True)
    rtvi = RTVIProcessor()
    worker = PipelineWorker(
        Pipeline([transport.input(), RealtimeTextBridge(llm), user, llm, transport.output(), assistant]),
        name="voice",
        params=PipelineParams(enable_metrics=True, audio_in_sample_rate=24000, audio_out_sample_rate=24000),
        rtvi_processor=rtvi,
        observers=[DiagnosticObserver(trace)],
        rtvi_observer_params=RTVIObserverParams(
            function_call_report_level={"get_demo_planner": RTVIFunctionCallReportLevel.FULL}
        ),
        processor_unusable_policy=ProcessorUnusablePolicy.CANCEL,
        idle_timeout_secs=runner_args.pipeline_idle_timeout_secs,
    )
    runner = WorkerRunner(handle_sigint=runner_args.handle_sigint)

    @llm.event_handler("on_preview_event")
    async def diagnostic(service, entry):
        await rtvi.send_server_message({"kind": "preview-diagnostic", **entry})

    @rtvi.event_handler("on_client_ready")
    async def client_ready(processor):
        await rtvi.send_server_message(
            {"kind": "preview-model", "configured": MODEL, "acknowledged": trace.model}
        )
        # Request a short initial greeting, following the official tutorial.
        await worker.queue_frame(LLMRunFrame())

    @llm.event_handler("on_session_terminated")
    async def session_terminated(service, event_type, event):
        trace.record("session.terminated", event_type=event_type)
        await rtvi.send_error(f"Boson ended the session: {event_type}")
        await runner.cancel()

    @transport.event_handler("on_client_disconnected")
    async def disconnected(transport, client):
        trace.record("transport.disconnected")
        await runner.cancel()

    if settings.mock_worker:
        await runner.add_workers(MockPlannerWorker(trace), worker)
    else:
        await runner.add_workers(worker)
    await runner.run()


if __name__ == "__main__":
    utf8_console()
    # Validate before the runner starts listening; no credentials in arguments.
    settings = load_settings()
    configure_logging(settings.api_key)
    from pipecat.runner.run import main

    main()
