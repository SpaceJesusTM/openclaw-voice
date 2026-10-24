"""Route typed RTVI input to the SDK's native append-frame handler.

Pipecat 1.12's user aggregator consumes LLMMessagesAppendFrame and emits only
LLMContextFrame. Boson 0.1.4 processes subsequent context frames as tool-result
updates, so it never sends that typed user message. Keep the aggregator for
audio turns, but let the Boson service handle this one existing frame directly.
"""

from pipecat.frames.frames import LLMMessagesAppendFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


class RealtimeTextBridge(FrameProcessor):
    def __init__(self, llm):
        super().__init__()
        self.llm = llm

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMMessagesAppendFrame) and direction == FrameDirection.DOWNSTREAM:
            await self.llm.queue_frame(frame, direction)
        else:
            await self.push_frame(frame, direction)
