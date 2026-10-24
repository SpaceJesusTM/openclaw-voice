"""One agent's voice in a Team room.

Each agent keeps its own Higgs session (its own voice, instructions and tools)
and receives only what the user addresses to it, its own tool results and its
own background-task notices. Its requests run in order; different agents run
at the same time. Replies go to the room's speaking floor; this class never
plays audio itself.

Behind the voice is the agent's existing AgentRuntime: the same tools
(VoiceTools), hot-context cache, OpenClaw job manager and verifiers as
single-agent mode. A shared room is not shared memory: nothing from another
agent's session, tools or jobs is ever sent to this one.
"""

from __future__ import annotations

import asyncio
import base64
import time
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from loguru import logger

from clawvoice.team.floor import MIN_HEARD_BYTES, Reply
from clawvoice.team.realtime import RealtimeSession, function_output, session_config, text_item, truncate
from clawvoice.voice.floor import STOP_WINDOW_SECONDS, compose_notice
from clawvoice.voice.tools import VoiceTools

if TYPE_CHECKING:
    from clawvoice.jobs.models import Job
    from clawvoice.runtime import AgentRuntime
    from clawvoice.team.room import TeamRoom

MAX_CHAIN = 4  # responses per request: speech, tool call, answer...
RESPONSE_TIMEOUT = 60.0
NOTICE_BATCH = 3
NOTICE_RETRIES = 1
# Announcement states as the job store names them (same mapping as single-agent mode).
_JOB_STATE = {"requeued": "queued", "unheard": "interrupted_unheard"}


class AgentSeat:
    """What AgentRuntime and VoiceTools expect of a voice session, for one agent in a room."""

    def __init__(self, voice: AgentVoice):
        self.voice = voice
        self.id = f"{voice.room.room_id}:{voice.agent.id}"
        self.trace = voice.room.trace

    def ui(self, kind: str, data: dict[str, Any]) -> None:
        self.voice.room.ui(kind, {**data, "agent": self.voice.agent.id})

    def announce(self, job: Job, *, earlier: bool) -> None:
        self.voice.add_notice(job.id, job.outcome_text, earlier=earlier)

    def recent_user_words(self) -> list[str]:
        return list(self.voice.user_words)


@dataclass
class _Response:
    reply: Reply
    id: str | None = None
    status: str = ""
    had_audio: bool = False
    calls: list[tuple[str, str, asyncio.Task]] = field(default_factory=list)
    done: asyncio.Event = field(default_factory=asyncio.Event)


class AgentVoice:
    def __init__(self, room: TeamRoom, runtime: AgentRuntime, *, instructions: str, voice: str):
        self.room = room
        self.runtime = runtime
        self.agent = runtime.agent
        self.id = runtime.agent.id
        self.instructions = instructions
        self.voice_name = voice
        self.seat = AgentSeat(self)
        self.tools = VoiceTools(runtime, self.seat)
        self.session: RealtimeSession | None = None
        self.error: str | None = None
        self.busy = False
        self.user_words: deque[str] = deque(maxlen=3)  # recent requests to this agent (job context)
        self.history: deque[tuple[str, str]] = deque(maxlen=8)  # for a recap after reconnecting
        self.notices: deque[dict[str, Any]] = deque()
        self.silenced = False  # after "stop": no notices until the user says something new
        self.spoke = False  # its answer to the latest request has started playing
        self.backgrounded = False  # the owner addressed another agent: work on, say nothing more
        self._answering = False
        self._ops: asyncio.Queue = asyncio.Queue()
        self._queued_requests = 0
        self._worker: asyncio.Task | None = None
        self._response: _Response | None = None
        self._chain: Reply | None = None  # the reply whose response chain is running (tools included)
        self._notice_ids: set[str] = set()
        self._finished_notices: set[str] = set()
        self._retries: dict[str, int] = {}
        self._recent_cut: tuple[float, list[dict[str, Any]]] = (0.0, [])
        self._needs_recap = False

    # ------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        # The worker first: if this connect fails, the next request reconnects.
        self._worker = asyncio.create_task(self._run(), name=f"team-voice:{self.id}")
        await self._connect()

    async def _connect(self) -> None:
        self.session = RealtimeSession(
            self.room.settings,
            label=f"voice:{self.id}",
            config=session_config(
                instructions=self.instructions,
                modality="audio",
                voice=self.voice_name,
                turn_detection=None,  # manual turns: the room decides what this agent hears
                tools=self.tools.realtime_tools(),
            ),
            on_event=self._on_event,
            trace=self.room.trace,
            on_close=self._on_close,
        )
        await self.session.connect()
        self.error = None

    async def _on_close(self, session: RealtimeSession) -> None:
        if session is not self.session:
            return
        self._needs_recap = True
        if self._response is not None:
            self._response.status = "failed"
            self._response.done.set()
        logger.info(f"Team voice {self.id}: Higgs session closed ({session.close_reason}); reconnects on next use")

    async def close(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            await asyncio.gather(self._worker, return_exceptions=True)
        # Results not heard yet stay pending in the job store for the next connection.
        for item in list(self.notices):
            self._mark(item["job_id"], "pending")
        self.notices.clear()
        if self.session is not None:
            await self.session.close()

    # ------------------------------------------------------------ requests
    def ask(self, text: str) -> None:
        """A request the user addressed to this agent (spoken or typed)."""
        self.user_words.append(text[:400])
        self.history.append(("Owner", text[:400]))
        self.silenced = False
        self.spoke = False
        self.backgrounded = False
        # A newer request to the same agent supersedes its answers not yet heard.
        self.room.floor.drop(lambda r: r.agent == self.id and r.kind != "notice", "superseded")
        if self._chain is not None and self._chain.kind != "notice":
            self._chain.cancelled = True
        self._queued_requests += 1
        self._ops.put_nowait(("request", text))
        self.room.changed()

    def greet(self, note: str) -> None:
        self._ops.put_nowait(("greeting", note))

    @property
    def idle(self) -> bool:
        return not self.busy and self._ops.empty()

    @property
    def answer_pending(self) -> bool:
        """A request to this agent is still being answered out loud."""
        return not self.backgrounded and (self._queued_requests > 0 or self._answering)

    def yield_floor(self) -> list[Reply]:
        """The owner turned to another agent: stop this conversation, keep the work.

        Its answers not heard yet are dropped, and the rest of its response chain
        runs without being spoken: tool calls already made finish, jobs keep
        running, and its memory is truncated to what was heard. Notices (finished
        jobs) are not touched; they wait for a natural break. The next request to
        this agent ends the silent chain, as any newer request does.
        """
        self.backgrounded = True
        dropped = self.room.floor.drop(lambda r: r.agent == self.id and r.kind != "notice", "handed_over", cancel=False)
        self.room.changed()
        return dropped

    # ------------------------------------------------------------ notices
    def add_notice(self, job_id: str, text: str, *, earlier: bool) -> None:
        if job_id in self._finished_notices or job_id in self._notice_ids:
            return
        self._notice_ids.add(job_id)
        self.notices.append({"job_id": job_id, "text": text, "earlier": earlier})
        self._mark(job_id, "queued")
        self.room.changed()

    def notice_ready(self) -> bool:
        return bool(self.notices) and self.idle and not self.silenced and self.session is not None

    def deliver_notices(self) -> None:
        items = [self.notices.popleft() for _ in range(min(NOTICE_BATCH, len(self.notices)))]
        for item in items:
            self._mark(item["job_id"], "delivering")
        self.busy = True  # claimed now, so the room does not schedule it twice
        self._ops.put_nowait(("notice", items))

    def notice_outcome(self, reply: Reply) -> None:
        """Account for a notice reply once the floor is done with it (at most once, as in single-agent mode)."""
        now = time.monotonic()
        for item in reply.notice_items:
            job_id = item["job_id"]
            if reply.outcome == "played":
                self._finish_notice(job_id, "delivered" if self.room.client_alive() else "unheard")
            elif reply.outcome == "cut" and reply.heard >= MIN_HEARD_BYTES:
                self._finish_notice(job_id, "delivered")  # partly heard: never replayed
                self._recent_cut = (now, [item])
            elif reply.outcome == "dismissed":
                self._finish_notice(job_id, "dismissed")
            elif self._retries.get(job_id, 0) < NOTICE_RETRIES:
                self._retries[job_id] = self._retries.get(job_id, 0) + 1
                self.notices.appendleft(item)
                self._mark(job_id, "requeued")
                if reply.outcome == "cut":
                    self._recent_cut = (now, [item])
            else:
                self._finish_notice(job_id, "interrupted" if reply.outcome == "cut" else "unheard")
        self.room.changed()

    def _finish_notice(self, job_id: str, state: str) -> None:
        self._notice_ids.discard(job_id)
        self._finished_notices.add(job_id)
        self._mark(job_id, state)

    def on_stop(self) -> None:
        """Explicit "stop": hold notices until the next request; dismiss one just cut off."""
        self.silenced = True
        cut_at, items = self._recent_cut
        if items and time.monotonic() - cut_at < STOP_WINDOW_SECONDS:
            for item in items:
                if item in self.notices:
                    self.notices.remove(item)
                if item["job_id"] not in self._finished_notices:
                    self._finish_notice(item["job_id"], "dismissed")
        self._recent_cut = (0.0, [])
        if self._chain is not None and not self.backgrounded:
            self._chain.cancelled = True  # a silent chain is background work: stop does not end it

    def _mark(self, job_id: str, state: str) -> None:
        self.room.trace.record("announcement", phase=state, job_id=job_id, agent=self.id)
        self.runtime.jobs.mark_announcement(job_id, _JOB_STATE.get(state, state), self.seat.id)

    # ------------------------------------------------------------ worker
    async def _run(self) -> None:
        while True:
            kind, payload = await self._ops.get()
            self.busy = True
            self._answering = kind in ("request", "greeting")
            try:
                if self.session is None or self.session.closed:
                    await self._reconnect()
                if kind == "request":
                    self._queued_requests -= 1
                    await self._request(payload)
                elif kind == "greeting":
                    await self._say("greeting", payload)
                elif kind == "notice":
                    await self._say("notice", compose_notice(self.agent.display_name, payload), payload)
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001 - one failed request must not stop the agent
                self.error = f"{type(error).__name__}: {error}"[:200]
                logger.warning(f"Team voice {self.id}: {kind} failed: {self.error}")
                self.room.ui("team.error", {"agent": self.id, "message": f"{self.agent.display_name}: voice request failed"})
                if kind == "notice":
                    for item in reversed(payload):
                        self.notices.appendleft(item)
                        self._mark(item["job_id"], "pending")
            finally:
                self.busy = self._answering = False
                self.room.changed()

    async def _reconnect(self) -> None:
        await self._connect()
        if self._needs_recap and self.history:
            lines = " ".join(f"{who}: {text}" for who, text in self.history)
            await self.session.send(
                text_item(
                    "[ClawVoice note] Your voice connection was renewed. Recent conversation, for context only; "
                    f"do not reply to this note: {lines}"
                )
            )
        self._needs_recap = False

    async def _request(self, text: str) -> None:
        await self.session.send(text_item(text))
        if self._queued_requests > 0:
            return  # a newer request to this agent is already waiting; answer that one
        await self._generate(self.room.floor.new_reply(self.id, "answer"))

    async def _say(self, kind: str, text: str, items: list[dict[str, Any]] | None = None) -> None:
        reply = self.room.floor.new_reply(self.id, kind, tuple(i["job_id"] for i in items or ()))
        reply.notice_items = list(items or [])
        await self.session.send(text_item(text))
        await self._generate(reply)

    async def _generate(self, reply: Reply) -> None:
        """Run one reply's response chain: speech, tool calls, the answer after them."""
        self._chain = reply
        try:
            await self._chain_loop(reply)
        finally:
            last, self._chain, self._response = self._chain, None, None
            if last is not None and last.generating:
                self.room.floor.generation_done(last)  # an error mid-chain must not leave it "thinking"

    async def _chain_loop(self, reply: Reply) -> None:
        self.room.reply_created(reply)
        retried = False
        for _ in range(MAX_CHAIN):
            response = _Response(reply)
            self._response = response
            if not await self.session.send({"type": "response.create"}):
                response.status = "failed"
            else:
                try:
                    await asyncio.wait_for(response.done.wait(), timeout=RESPONSE_TIMEOUT)
                except asyncio.TimeoutError:
                    response.status = "timeout"
            self._response = None
            if (
                response.status in ("failed", "timeout")
                and not response.had_audio
                and not response.calls
                and not reply.cancelled
                and not retried
                and self.session is not None
                and not self.session.closed
            ):
                retried = True  # Higgs 1.5 fails some responses outright (upstream_error): ask once more
                continue
            if not response.calls:
                break
            # Tools started as their arguments arrived; their results continue the chain.
            results = await asyncio.gather(*(task for _, _, task in response.calls), return_exceptions=True)
            for (call_id, _name, _task), result in zip(response.calls, results):
                if isinstance(result, BaseException):
                    result = {"status": "error", "error": type(result).__name__, "note": "Tell the user this failed."}
                await self.session.send(function_output(call_id, result))
            await self._settle(reply)
            if reply.cancelled or self.session is None or self.session.closed:
                break
            if reply.state == "done":
                reply.generating = False
                if self.backgrounded:
                    # Handed over: finish the chain's work without speaking.
                    reply = self.room.floor.background_reply(self.id, reply.kind, reply.job_ids)
                else:
                    # Cut off mid-chain (the user spoke): the rest is a new reply that waits its turn.
                    reply = self.room.floor.new_reply(self.id, reply.kind, reply.job_ids)
                    self.room.reply_created(reply)
                self._chain = reply
        self.room.floor.generation_done(reply)
        await self._settle(reply)

    async def _settle(self, reply: Reply) -> None:
        """Truncate this agent's memory of a reply to what was actually heard."""
        active = self._response is not None and self._response.reply is reply
        if reply.state != "done" or reply.truncated or reply.outcome == "played" or active:
            return
        if self.session is None or self.session.closed:
            return
        reply.truncated = True
        for item_id, audio_end_ms in reply.truncations():
            await self.session.send(truncate(item_id, audio_end_ms))

    async def settle_later(self, reply: Reply) -> None:
        """Called when the floor finishes with a reply whose generation already ended."""
        if not reply.generating:
            await self._settle(reply)

    def heard(self, reply: Reply) -> None:
        text = reply.heard_text()
        if text:
            self.history.append((self.agent.display_name, text[:400]))

    # ------------------------------------------------------------ Higgs events
    async def _on_event(self, event: dict[str, Any]) -> None:
        kind = event.get("type")
        response = self._response
        if kind == "response.created":
            response_id = (event.get("response") or {}).get("id")
            if response is not None and response.id is None:
                response.id = response_id
            else:
                logger.debug(f"Team voice {self.id}: response {response_id} was not requested; ignoring it")
            return
        if kind == "error":
            error = event.get("error") or {}
            logger.warning(f"Team voice {self.id}: Higgs error {error.get('code') or error.get('type')}: {str(error.get('message'))[:160]}")
            if response is not None and response.id is None:
                response.status = "failed"  # response.create was refused; do not wait for it
                response.done.set()
            return
        if response is None:
            return
        response_id = event.get("response_id") or (event.get("response") or {}).get("id")
        if response_id != response.id:
            return
        reply = response.reply
        if kind == "response.output_item.added":
            item = event.get("item") or {}
            if item.get("type") == "message":
                reply.add_item(str(item.get("id") or ""))
        elif kind == "response.output_audio.delta":
            pcm = base64.b64decode(event.get("delta") or "")
            if pcm:
                reply.add_item(str(event.get("item_id") or ""))
                reply.audio.extend(pcm)
                response.had_audio = True
                self.room.floor.audio_arrived(reply)
        elif kind == "response.output_audio_transcript.delta":
            reply.add_text(str(event.get("item_id") or ""), str(event.get("delta") or ""), final=False)
        elif kind == "response.output_audio_transcript.length":
            reply.add_fragment(str(event.get("item_id") or ""), str(event.get("delta") or ""), int(event.get("length_ms") or 0))
        elif kind == "response.output_audio_transcript.done":
            reply.add_text(str(event.get("item_id") or ""), str(event.get("transcript") or ""), final=True)
            self.room.reply_text(reply)
        elif kind == "response.function_call_arguments.done":
            call_id = str(event.get("call_id") or "")
            name = str(event.get("name") or "")
            task = asyncio.create_task(self.tools.run(name, event.get("arguments"), call_id))
            response.calls.append((call_id, name, task))
        elif kind == "response.done":
            response.status = str((event.get("response") or {}).get("status") or "completed")
            response.done.set()
