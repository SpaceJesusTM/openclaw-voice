"""The same distinctive fixture as Step 1, run through Pipecat's function API."""

import json
import time

from pipecat.adapters.schemas.direct_function import tool_options
from pipecat.frames.frames import FunctionCallResultProperties
from pipecat.pipeline.job_context import JobParams
from pipecat.services.llm_service import FunctionCallParams

from config import ROOT

FIXTURE = ROOT.parent / "higgs-realtime-1.5" / "fixtures" / "planner.json"


def planner_result(arguments):
    if arguments != {"day": "today"}:
        return {"error": "invalid_arguments", "summary": 'Expected exactly {"day":"today"}.'}
    try:
        data = json.loads(FIXTURE.read_text())
        return {
            "summary": f"{len(data['entries'])} demo entries. Verification code {data['verification_code']}.",
            "detail": data,
        }
    except (OSError, ValueError, KeyError) as error:
        return {"error": type(error).__name__, "summary": "Demo planner fixture could not be loaded."}


def planner_tool(trace, use_worker=False):
    @tool_options(cancel_on_interruption=False)
    async def get_demo_planner(params: FunctionCallParams, day: str = "today") -> None:
        """Read today's demo planner. Always call for planner questions; never invent entries.
        Include the first entry's full title and verification code in your spoken answer.

        Args:
            day: Must be today.
        """
        started = time.monotonic()
        trace.record(
            "tool.started",
            call_id=params.tool_call_id,
            name="get_demo_planner",
            arguments=dict(params.arguments),
        )
        try:
            if use_worker:
                async with params.pipeline_worker.job(
                    "mock-planner",
                    params=JobParams(name="lookup", payload=dict(params.arguments), timeout=10),
                ) as job:
                    pass
                result = job.response
            else:
                result = planner_result(dict(params.arguments))
        except Exception as error:
            result = {"error": type(error).__name__, "summary": "Demo planner lookup failed."}
        entry = trace.record(
            "tool.result",
            call_id=params.tool_call_id,
            name="get_demo_planner",
            result=result,
            ms=round((time.monotonic() - started) * 1000),
        )
        await params.llm._call_event_handler("on_preview_event", entry)
        await params.result_callback(result, properties=FunctionCallResultProperties(run_llm=True))

    return get_demo_planner
