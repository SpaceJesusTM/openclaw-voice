"""A single mock backend job on the real Pipecat worker bus. No OpenClaw calls."""

import asyncio

from pipecat.bus import BusJobRequestMessage
from pipecat.pipeline.job_decorator import job
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineWorker
from pipecat.processors.frame_processor import FrameProcessor

from planner import planner_result


class Passthrough(FrameProcessor):
    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)


class MockPlannerWorker(PipelineWorker):
    def __init__(self, trace):
        super().__init__(
            Pipeline([Passthrough()]), name="mock-planner", idle_timeout_secs=None, enable_rtvi=False
        )
        self.trace = trace

    @job(name="lookup")
    async def lookup(self, message: BusJobRequestMessage):
        await self.send_job_update(message.job_id, {"status": "working"})
        await asyncio.sleep(0.05)  # Simulated backend work; not on the voice pipeline.
        result = planner_result(message.payload)
        self.trace.record("worker.result", job_id=message.job_id, result=result)
        await self.send_job_response(message.job_id, result)
