"""One browser voice connection: its trace, UI channel and announcement coordinator."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from loguru import logger
from pipecat.frames.frames import LLMMessagesAppendFrame

from clawvoice.voice.floor import AnnouncementCoordinator
from clawvoice.voice.telemetry import Trace

if TYPE_CHECKING:
    from pipecat.processors.frameworks.rtvi import RTVIProcessor

    from clawvoice.jobs.models import Job
    from clawvoice.runtime import AgentRuntime

_STATE_MAP = {"requeued": "queued", "pending": "pending", "unheard": "interrupted_unheard"}


class VoiceSession:
    def __init__(self, *, session_id: str, runtime: AgentRuntime, trace: Trace):
        self.id = session_id
        self.runtime = runtime
        self.trace = trace
        self.rtvi: RTVIProcessor | None = None
        self.llm = None
        self.client_ready = False
        self._tasks: set[asyncio.Task] = set()
        self.coordinator = AnnouncementCoordinator(
            session_id=session_id,
            agent_name=runtime.agent.display_name,
            inject=self._inject,
            mark=self._mark,
            on_state=lambda state: self.ui("floor", state),
        )

    # ------------------------------------------------------------ UI channel
    def ui(self, kind: str, data: dict[str, Any]) -> None:
        """Send a browser-safe server message over RTVI (never secrets)."""
        if not self.client_ready or self.rtvi is None:
            return
        task = asyncio.create_task(self._send({"kind": f"clawvoice.{kind}", **data}))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _send(self, message: dict[str, Any]) -> None:
        try:
            await self.rtvi.send_server_message(message)
        except Exception as error:  # noqa: BLE001 - UI updates are best effort
            logger.debug(f"UI message dropped: {type(error).__name__}")

    # ------------------------------------------------------------ announcements
    def announce(self, job: Job, *, earlier: bool) -> None:
        self.coordinator.enqueue(job.id, job.outcome_text, earlier=earlier)

    async def _inject(self, text: str) -> None:
        self.trace.record("announcement", phase="inject", text=text)
        await self.llm.queue_frame(LLMMessagesAppendFrame(messages=[{"role": "user", "content": text}], run_llm=True))

    def _mark(self, job_id: str, state: str) -> None:
        self.trace.record("announcement", phase=state, job_id=job_id)
        self.runtime.jobs.mark_announcement(job_id, _STATE_MAP.get(state, state), self.id)

    def recent_user_words(self) -> list[str]:
        return list(self.coordinator.user_words)
