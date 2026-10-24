"""Single-agent speaking floor and background-completion announcements.

The observer derives floor state from real pipeline frames: the user speaking
(server VAD), the bot speaking (transport playback), a model response in
progress, and tool calls in flight. The coordinator holds finished-job
notices until the floor has been free for a short quiet gap, then injects one
coalesced notice into the Higgs conversation as a clearly marked system notice
(the validated typed-input path of the realtime service). It never overlaps
speech: a notice waits while the user or bot is talking.

At most once: a notice counts as delivered once its spoken answer started. If
the user barges in before any audio of it played, it is re-queued once.
Interrupting a notice (or anything else) never cancels a background job.

Priority 2 extension point: the same state machine generalizes to several
agents by adding a speaking-agent field and a floor queue across agents.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Any, Callable

from loguru import logger
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    FunctionCallCancelFrame,
    FunctionCallInProgressFrame,
    FunctionCallResultFrame,
    InputAudioRawFrame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMMessagesAppendFrame,
    TranscriptionFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.observers.base_observer import BaseObserver, FramePushed

from clawvoice.voice.stop import is_stop_request

try:  # Present in pipecat 1.12; guarded so the observer degrades gracefully.
    from pipecat.frames.frames import ProposedUserStartedSpeakingFrame, ProposedUserStoppedSpeakingFrame
except ImportError:  # pragma: no cover
    ProposedUserStartedSpeakingFrame = ProposedUserStoppedSpeakingFrame = ()  # type: ignore[assignment]

NOTICE_PREFIX = "[ClawVoice notice]"
QUIET_SECONDS = 0.9
UNHEARD_TIMEOUT_SECONDS = 8.0
# A "stop" this soon after a notice was interrupted dismisses that notice.
STOP_WINDOW_SECONDS = 15.0
MAX_COALESCE = 3
# A connected browser always streams input audio (microphone or a silent track).
# WebRTC peers that vanish are detected late, so recent input audio is the
# liveness signal: notices are not spoken into, or counted as heard by, a gone client.
CLIENT_SILENCE_SECONDS = 3.0


class FloorState:
    def __init__(self) -> None:
        self.user_speaking = False
        self.bot_speaking = False
        self.response_active = False
        self.tools: set[str] = set()
        self.last_activity = time.monotonic()
        self.user_started_at: float | None = None

    def free(self) -> bool:
        # Guard against a lost "stopped" event leaving the floor blocked forever.
        if self.user_speaking and self.user_started_at and time.monotonic() - self.user_started_at > 45:
            self.user_speaking = False
        return not (self.user_speaking or self.bot_speaking or self.response_active or self.tools)

    def snapshot(self) -> dict[str, Any]:
        return {
            "user_speaking": self.user_speaking,
            "bot_speaking": self.bot_speaking,
            "response_active": self.response_active,
            "tools_in_flight": len(self.tools),
        }


class AnnouncementCoordinator:
    def __init__(
        self,
        *,
        session_id: str,
        agent_name: str,
        inject: Callable[[str], Any],
        mark: Callable[[str, str], None],
        on_state: Callable[[dict[str, Any]], None] | None = None,
        quiet_seconds: float = QUIET_SECONDS,
    ):
        self.session_id = session_id
        self.agent_name = agent_name
        self.floor = FloorState()
        self._inject = inject
        self._mark = mark
        self._on_state = on_state
        self.quiet_seconds = quiet_seconds
        self._pending: deque[dict[str, Any]] = deque()
        self._delivering: list[dict[str, Any]] = []
        self._delivered_ids: set[str] = set()
        self._retries: dict[str, int] = {}
        self._delivering_since = 0.0
        self._heard = False
        self.ready = False
        self._ready_deadline = time.monotonic() + 12.0
        self._task: asyncio.Task | None = None
        self.user_words: deque[str] = deque(maxlen=3)
        self.last_client_audio: float | None = None
        # After an explicit "stop": no notices until the user says something new.
        self.silenced = False
        self._last_interrupted: tuple[float, list[dict[str, Any]]] = (0.0, [])

    def client_alive(self) -> bool:
        return self.last_client_audio is not None and time.monotonic() - self.last_client_audio < CLIENT_SILENCE_SECONDS

    def on_client_audio(self) -> None:
        self.last_client_audio = time.monotonic()

    # ------------------------------------------------------------ lifecycle
    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name=f"announcements:{self.session_id}")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        # Anything not yet heard stays pending in the job store for later.
        for item in [*self._delivering, *self._pending]:
            if item["job_id"] not in self._delivered_ids:
                self._mark(item["job_id"], "pending")

    # ------------------------------------------------------------ queue
    def enqueue(self, job_id: str, text: str, *, earlier: bool = False) -> None:
        if job_id in self._delivered_ids or any(i["job_id"] == job_id for i in [*self._pending, *self._delivering]):
            return  # coalesce duplicates
        self._pending.append({"job_id": job_id, "text": text, "earlier": earlier})
        self._mark(job_id, "queued")
        self._publish()

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    def _publish(self) -> None:
        if self._on_state:
            self._on_state(
                {
                    "pending": [i["job_id"] for i in self._pending],
                    "delivering": [i["job_id"] for i in self._delivering],
                    **self.floor.snapshot(),
                }
            )

    # ------------------------------------------------------------ frame events
    def activity(self) -> None:
        self.floor.last_activity = time.monotonic()

    def on_user_speaking(self, speaking: bool) -> None:
        self.floor.user_speaking = speaking
        self.floor.user_started_at = time.monotonic() if speaking else None
        self.activity()

    def on_bot_speaking(self, speaking: bool) -> None:
        self.floor.bot_speaking = speaking
        self.activity()
        if speaking and self._delivering:
            self._heard = True
        if not speaking and self._delivering and self._heard:
            # Played out, but only "heard" if the client was still there.
            self._finish_delivery("delivered" if self.client_alive() else "unheard")

    def on_response(self, active: bool) -> None:
        self.floor.response_active = active
        self.activity()
        if not active and not self.ready:
            self.ready = True  # the greeting (or first reply) finished

    def on_tool(self, call_id: str, running: bool) -> None:
        (self.floor.tools.add if running else self.floor.tools.discard)(call_id)
        self.activity()

    def on_interruption(self) -> None:
        self.activity()
        if self._delivering:
            self._last_interrupted = (time.monotonic(), list(self._delivering))
            if self._heard:
                self._finish_delivery("delivered")  # partially heard: at most once
            else:
                for item in self._delivering:
                    tries = self._retries.get(item["job_id"], 0)
                    if tries < 1:
                        self._retries[item["job_id"]] = tries + 1
                        self._pending.appendleft(item)
                        self._mark(item["job_id"], "requeued")
                    else:
                        self._delivered_ids.add(item["job_id"])
                        self._mark(item["job_id"], "interrupted")
                self._delivering = []
                self._publish()

    def on_user_words(self, text: str) -> None:
        text = " ".join(str(text or "").split())
        if text and not text.startswith(NOTICE_PREFIX):
            self.user_words.append(text[:400])
            self.activity()
            if not is_stop_request(text):
                self.silenced = False  # something new was said

    def on_stop_request(self) -> None:
        """Explicit "stop": dismiss the notice being (or just) spoken; hold new ones.

        The job and its result are untouched; only the spoken announcement is
        marked dismissed so it is not replayed. Jobs keep running.
        """
        self.silenced = True
        self.activity()
        interrupted_at, recent = self._last_interrupted
        items = list(self._delivering) + (recent if time.monotonic() - interrupted_at < STOP_WINDOW_SECONDS else [])
        for item in items:
            if item in self._pending:
                self._pending.remove(item)
            self._delivered_ids.add(item["job_id"])
            self._mark(item["job_id"], "dismissed")
        self._delivering = []
        self._last_interrupted = (0.0, [])
        self._heard = False
        self._publish()

    # ------------------------------------------------------------ delivery
    def _finish_delivery(self, state: str) -> None:
        for item in self._delivering:
            self._delivered_ids.add(item["job_id"])
            self._mark(item["job_id"], state)
        self._delivering = []
        self._heard = False
        self._publish()

    def compose(self, items: list[dict[str, Any]]) -> str:
        return compose_notice(self.agent_name, items)

    async def _deliver(self) -> None:
        items = [self._pending.popleft() for _ in range(min(MAX_COALESCE, len(self._pending)))]
        self._delivering = items
        self._heard = False
        self._delivering_since = time.monotonic()
        for item in items:
            self._mark(item["job_id"], "delivering")
        self._publish()
        try:
            await self._inject(self.compose(items))
        except Exception as error:  # noqa: BLE001 - keep notices pending on failure
            logger.warning(f"announcement injection failed: {type(error).__name__}")
            for item in reversed(items):
                self._pending.appendleft(item)
                self._mark(item["job_id"], "pending")
            self._delivering = []
        self.activity()

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(0.15)
            try:
                now = time.monotonic()
                if not self.ready and now > self._ready_deadline:
                    self.ready = True
                if self._delivering:
                    if not self._heard and now - self._delivering_since > UNHEARD_TIMEOUT_SECONDS:
                        # No audio came back; do not loop forever on one notice.
                        self._finish_delivery("unheard")
                    continue
                if (
                    self._pending
                    and self.ready
                    and not self.silenced
                    and self.client_alive()
                    and self.floor.free()
                    and now - self.floor.last_activity >= self.quiet_seconds
                ):
                    await self._deliver()
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001
                logger.warning(f"announcement loop error: {type(error).__name__}")


class FloorObserver(BaseObserver):
    """Feeds pipeline frames into the coordinator (no frames are modified)."""

    def __init__(self, coordinator: AnnouncementCoordinator):
        super().__init__()
        self.c = coordinator

    async def on_push_frame(self, data: FramePushed):
        if not data.first_push:
            return
        frame = data.frame
        if isinstance(frame, InputAudioRawFrame):
            self.c.on_client_audio()
        elif isinstance(frame, (UserStartedSpeakingFrame, *_tuple(ProposedUserStartedSpeakingFrame))):
            self.c.on_user_speaking(True)
        elif isinstance(frame, (UserStoppedSpeakingFrame, *_tuple(ProposedUserStoppedSpeakingFrame))):
            self.c.on_user_speaking(False)
        elif isinstance(frame, BotStartedSpeakingFrame):
            self.c.on_bot_speaking(True)
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self.c.on_bot_speaking(False)
        elif isinstance(frame, LLMFullResponseStartFrame):
            self.c.on_response(True)
        elif isinstance(frame, LLMFullResponseEndFrame):
            self.c.on_response(False)
        elif isinstance(frame, FunctionCallInProgressFrame):
            self.c.on_tool(frame.tool_call_id, True)
        elif isinstance(frame, (FunctionCallResultFrame, FunctionCallCancelFrame)):
            self.c.on_tool(frame.tool_call_id, False)
        elif isinstance(frame, InterruptionFrame):
            self.c.on_interruption()
        elif isinstance(frame, TranscriptionFrame):
            self.c.on_user_words(frame.text)
        elif isinstance(frame, LLMMessagesAppendFrame):
            for message in frame.messages or []:
                if isinstance(message, dict) and message.get("role") == "user":
                    content = message.get("content")
                    if isinstance(content, str):
                        self.c.on_user_words(content)


def compose_notice(agent_name: str, items: list[dict[str, Any]]) -> str:
    """The system notice that asks the voice model to relay finished background tasks once."""
    if len(items) == 1:
        item = items[0]
        when = "finished earlier, while the user was away" if item["earlier"] else "just finished"
        return (
            f"{NOTICE_PREFIX} A background task for {agent_name} {when} (task {item['job_id']}). "
            f"Result: {_closed(item['text'])} "
            "Tell the user now in one or two short, natural sentences, once. Do not call any tools for this "
            "notice. This notice is not from the user."
        )
    lines = " ".join(f"({n}) {_closed(item['text'])}" for n, item in enumerate(items, 1))
    return (
        f"{NOTICE_PREFIX} {len(items)} background tasks for {agent_name} finished. {lines} "
        "Tell the user briefly, one short sentence each, once. Do not call any tools for this notice. "
        "This notice is not from the user."
    )


def _closed(text: str) -> str:
    """End a quoted result as a sentence so it cannot run on into the instruction after it."""
    text = " ".join(str(text or "").split())
    if not text:
        return "No details were given."
    return text if text.rstrip("\"')]").endswith((".", "!", "?", "…")) else f"{text}."


def _tuple(value) -> tuple:
    if isinstance(value, tuple):
        return value
    return (value,)
