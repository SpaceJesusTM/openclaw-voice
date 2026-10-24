"""A Team voice room: several real OpenClaw agents behind one browser connection.

    WebRTC in -> TeamRoom -> WebRTC out           (Pipecat transport + RTVI, as before)

- Listener: one silent Higgs session (text output, server VAD with
  ``create_response`` off, input transcription). It hears the microphone,
  finds turn boundaries and barge-in, and transcribes. It never generates, has
  no tools and never sees an agent's output. This is the silent-overseer idea
  from the agent-selector experiment, minus any model-made routing decision.
- Routing: deterministic name addressing on the transcript (addressing.py).
  Unnamed requests go to nobody.
- Voices: one AgentVoice per agent, each with its own Higgs session and its own
  AgentRuntime (cache, jobs, verifiers). Agents work at the same time.
- Floor: one SpeakingFloor. Replies from every agent queue for it, one is
  audible at a time, and finished background tasks are announced at pauses.
"""

from __future__ import annotations

import asyncio
import base64
import os
import re
import time
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from loguru import logger
from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    InputAudioRawFrame,
    InterruptionFrame,
    OutputAudioRawFrame,
    StartFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from clawvoice.config import MODEL, Settings
from clawvoice.jobs.models import ACTIVE
from clawvoice.team.addressing import AddressBook
from clawvoice.team.floor import MIN_HEARD_BYTES, NOTICE_GAP, Reply, SpeakingFloor
from clawvoice.team.realtime import RealtimeSession, SessionError, session_config
from clawvoice.team.voice import AgentVoice
from clawvoice.voice.instructions import build_instructions
from clawvoice.voice.telemetry import Trace

if TYPE_CHECKING:
    from clawvoice.agents import AgentConfig
    from clawvoice.runtime import AgentRuntime

SAMPLE_RATE = 24000
TICK_SECONDS = 0.02
MIC_BATCH_BYTES = 4800  # 100 ms per append
FOLLOW_UP_SECONDS = 12.0  # after a bare "Hey <Name>.", the next utterance is for that agent
CONTINUATION_SECONDS = 1.5  # an unnamed fragment this soon after a named one continues it
CLIENT_SILENCE_SECONDS = 3.0
LISTENER_INSTRUCTIONS = "You are a silent listener in a voice room. Never respond and never produce output."


def team_vad(settings: Settings) -> dict[str, Any]:
    """Server VAD for the listener only. Longer silence than single-agent mode: routing needs whole sentences."""
    silence = int(os.environ.get("CLAWVOICE_TEAM_VAD_SILENCE_MS") or max(settings.vad_silence_ms or 600, 700))
    vad = {"type": "server_vad", "silence_duration_ms": silence, "prefix_padding_ms": 300,
           "create_response": False, "interrupt_response": False}
    if settings.vad_threshold is not None:
        vad["threshold"] = settings.vad_threshold
    return vad


def team_instructions(agent: AgentConfig, others: list[AgentConfig]) -> str:
    names = " and ".join(o.display_name for o in others) or "other assistants"
    return (
        build_instructions(agent)
        + f"""

Team room: you share this voice room with {names}. The owner speaks to each of you by name, and you receive only what is said to you: you never hear {names}, their tools or their results, and they never hear yours. Speak only for yourself; never answer for {names} or claim their work. If the owner asks you about something that is {names}'s, say they can ask {names} by name. Background results reach you as notices and are spoken when the room is quiet."""
    )


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower().replace("’", "'"))


class TeamRoom(FrameProcessor):
    def __init__(
        self,
        *,
        runtimes: list[AgentRuntime],
        default_agent: str,
        settings: Settings,
        trace: Trace,
        room_id: str,
    ):
        super().__init__(name="team-room")
        self.settings = settings
        self.trace = trace
        self.room_id = room_id
        self.send_ui: Callable[[dict[str, Any]], Awaitable[None]] | None = None
        self.floor = SpeakingFloor(emit=self._emit_audio, flush=self._flush, on_event=self._floor_event, notice_ok=self._notice_ok)
        self.runtimes = {rt.agent.id: rt for rt in runtimes}
        self.voices: dict[str, AgentVoice] = {}
        for rt in runtimes:
            others = [o.agent for o in runtimes if o is not rt]
            self.voices[rt.agent.id] = AgentVoice(
                self, rt, instructions=team_instructions(rt.agent, others), voice=settings.voice_override or rt.agent.voice
            )
        self.book = AddressBook({rt.agent.id: rt.agent.aliases or (rt.agent.display_name.lower(),) for rt in runtimes})
        self.default_agent = default_agent if default_agent in self.voices else next(iter(self.voices))
        self.listener: RealtimeSession | None = None
        self.addressed: str | None = None
        self.last_client_audio: float | None = None
        self.closed = False
        self._client_ready = False
        self._sessions_ready = False
        self._greeted = False
        self._tasks: set[asyncio.Task] = set()
        self._mic = bytearray()
        self._mic_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=50)
        self._turns: dict[str, dict[str, float]] = {}
        self._item_text: dict[str, str] = {}  # transcript already routed, per listener item
        self._last_named: tuple[str, float] | None = None
        self._follow_up: tuple[str, float] | None = None
        self._dirty = True
        self._published: dict[str, Any] | None = None
        self._rate_warned = False

    # ------------------------------------------------------------ Pipecat
    async def process_frame(self, frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, StartFrame):
            await self.push_frame(frame, direction)
            self._spawn(self._startup())
        elif isinstance(frame, (EndFrame, CancelFrame)):
            await self.close()
            await self.push_frame(frame, direction)
        elif isinstance(frame, InputAudioRawFrame):
            self._on_mic(frame)  # the microphone goes to the listener only, never to an agent
        else:
            await self.push_frame(frame, direction)

    async def cleanup(self):
        await self.close()
        await super().cleanup()

    async def _emit_audio(self, chunk: bytes) -> None:
        await self.push_frame(OutputAudioRawFrame(audio=chunk, sample_rate=SAMPLE_RATE, num_channels=1))

    async def _flush(self) -> None:
        await self.push_frame(InterruptionFrame())  # clears audio the transport has queued but not played

    def _spawn(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    # ------------------------------------------------------------ startup
    async def _startup(self) -> None:
        self.ui("team.status", {"text": "Starting Team room…"})
        listener = self._new_listener()
        results = await asyncio.gather(listener.connect(), *(v.start() for v in self.voices.values()), return_exceptions=True)
        if self.closed:
            return
        if isinstance(results[0], BaseException):
            message = f"Team listener could not start: {results[0]}"
            logger.error(message)
            self.ui("team.error", {"agent": None, "message": "Could not start the Team room (listener). Check the Boson key and network, then reconnect.", "fatal": True})
            return
        self.listener = listener
        for voice, result in zip(self.voices.values(), results[1:]):
            if isinstance(result, BaseException):
                voice.error = f"{type(result).__name__}"
                logger.error(f"Team voice {voice.id} could not start: {result}")
                self.ui("team.error", {"agent": voice.id, "message": f"{voice.agent.display_name}'s voice could not start; it retries on the next request."})
        self._sessions_ready = True
        self._spawn(self._ticker())
        self._spawn(self._mic_sender())
        self.trace.record("team.ready", agents=list(self.voices), voices={v.id: v.voice_name for v in self.voices.values()})
        logger.info(f"Team room {self.room_id}: listener and {len(self.voices)} agent voices ready")
        await self._maybe_greet()

    def _new_listener(self) -> RealtimeSession:
        return RealtimeSession(
            self.settings,
            label="listener",
            config=session_config(
                instructions=LISTENER_INSTRUCTIONS,
                modality="text",
                turn_detection=team_vad(self.settings),
                transcription=True,
                noise_reduction=self.settings.noise_reduction,
            ),
            on_event=self._on_listener,
            trace=self.trace,
            on_close=self._listener_closed,
        )

    async def _listener_closed(self, session: RealtimeSession) -> None:
        if self.closed or session is not self.listener:
            return
        if self.floor.user_speaking:
            self.floor.user_stopped(None)  # its "speech stopped" will never come
        self.ui("team.status", {"text": "Reconnecting the listener…"})
        self._spawn(self._reconnect_listener())

    async def _reconnect_listener(self) -> None:
        for attempt in range(6):
            await asyncio.sleep(min(8.0, 1.0 + attempt * 1.5))
            if self.closed:
                return
            listener = self._new_listener()
            try:
                await listener.connect()
            except SessionError as error:
                logger.warning(f"Team listener reconnect failed: {error}")
                continue
            self.listener = listener
            self.ui("team.status", {"text": ""})
            return
        self.ui("team.error", {"agent": None, "message": "Lost the Team listener; reconnect.", "fatal": True})

    def client_ready(self) -> None:
        self._client_ready = True
        self._spawn(self._maybe_greet())

    async def _maybe_greet(self) -> None:
        if not (self._client_ready and self._sessions_ready) or self._greeted or self.closed:
            return
        self._greeted = True
        identities = [
            {**v.agent.public_identity(), "voice": v.voice_name, "online": v.session is not None and not v.session.closed}
            for v in self.voices.values()
        ]
        self.ui("team.ready", {"agents": identities, "default": self.default_agent})
        await self._send({"kind": "preview-model", "configured": MODEL, "acknowledged": MODEL})
        # Unheard results from earlier connections are queued per agent as notices.
        for voice in self.voices.values():
            voice.runtime.attach(voice.seat)
        greeter = self.voices[self.default_agent]
        others = " and ".join(v.agent.display_name for v in self.voices.values() if v is not greeter)
        greeter.greet(
            "[ClawVoice note] The owner just joined a Team voice room with you and "
            f"{others}. Greet them in one short sentence as {greeter.agent.display_name}, mention {others} "
            "is here too, and that they can talk to either of you by name. Do not call tools. "
            "This note is not from the owner."
        )
        self.changed()

    # ------------------------------------------------------------ microphone
    def _on_mic(self, frame: InputAudioRawFrame) -> None:
        self.last_client_audio = time.monotonic()
        if frame.sample_rate != SAMPLE_RATE or frame.num_channels != 1:
            if not self._rate_warned:
                self._rate_warned = True
                logger.error(f"Team room expects 24 kHz mono input, got {frame.sample_rate} Hz x{frame.num_channels}")
            return
        if self.listener is None or self.listener.closed:
            return
        self._mic.extend(frame.audio)
        if len(self._mic) >= MIC_BATCH_BYTES:
            chunk = bytes(self._mic)
            self._mic.clear()
            if self._mic_queue.full():
                self._mic_queue.get_nowait()  # the listener fell behind: drop the oldest audio
            self._mic_queue.put_nowait(chunk)

    async def _mic_sender(self) -> None:
        while not self.closed:
            chunk = await self._mic_queue.get()
            listener = self.listener
            if listener is not None and not listener.closed:
                await listener.send({"type": "input_audio_buffer.append", "audio": base64.b64encode(chunk).decode()})

    def client_alive(self) -> bool:
        return self.last_client_audio is not None and time.monotonic() - self.last_client_audio < CLIENT_SILENCE_SECONDS

    def on_client_alive(self) -> None:
        self.last_client_audio = time.monotonic()

    # ------------------------------------------------------------ listener events
    async def _on_listener(self, event: dict[str, Any]) -> None:
        kind = event.get("type")
        item = str(event.get("item_id") or "")
        now = time.monotonic()
        if kind == "input_audio_buffer.speech_started":
            self._turns[item] = {"started": now}
            await self.floor.user_started()  # barge-in: the audible reply stops; jobs are untouched
            self.ui("team.hearing", {"on": True})
            self.changed()
        elif kind == "input_audio_buffer.speech_stopped":
            self._turns.setdefault(item, {"started": now})["stopped"] = now
            self.floor.user_stopped(item)
            self.ui("team.hearing", {"on": False})
            self.changed()
        elif kind == "conversation.item.input_audio_transcription.completed":
            self.floor.transcript_done(item)
            turn = self._turns.pop(item, {})
            text = self._new_words(item, str(event.get("transcript") or ""))
            if text:
                await self.utterance(text, started=turn.get("started", now), ended=turn.get("stopped", now))
            self.changed()
        elif kind == "conversation.item.input_audio_transcription.failed":
            self.floor.transcript_done(item)
            self._turns.pop(item, None)
            self.ui("team.user", {"text": "", "agent": None, "status": "unclear"})
        elif kind == "response.created":
            logger.debug("Team listener started a response although create_response is off; ignoring it")
        elif kind == "error":
            error = event.get("error") or {}
            logger.warning(f"Team listener error {error.get('code') or error.get('type')}: {str(error.get('message'))[:160]}")

    def _new_words(self, item_id: str, transcript: str) -> str:
        """The new part of a transcript.

        The listener can reuse one conversation item for consecutive turns, and
        its transcript then accumulates ("<earlier turn>?Stop."), as the
        agent-selector experiment also saw. Per item, only what follows the text
        already routed is new. A repeat in a new item is a new request.
        """
        text = " ".join(transcript.split())
        previous = self._item_text.get(item_id, "") if item_id else ""
        if item_id:
            self._item_text[item_id] = text
            if len(self._item_text) > 200:
                self._item_text.pop(next(iter(self._item_text)))
        if not previous:
            return text
        if text.startswith(previous):
            return text[len(previous) :].strip()
        # Re-split or re-punctuated: drop the leading words that repeat the earlier text.
        earlier = _tokens(previous)
        matches = list(re.finditer(r"[a-z0-9']+", text.lower().replace("’", "'")))
        if earlier and [m.group(0) for m in matches[: len(earlier)]] == earlier:
            return text[matches[len(earlier) - 1].end() :].lstrip(" ,.;:!?-").strip()
        return text

    # ------------------------------------------------------------ routing
    async def typed(self, text: str) -> None:
        text = " ".join(str(text or "").split())[:1000]
        if text:
            now = time.monotonic()
            await self.utterance(text, started=now, ended=now, typed=True)

    async def utterance(self, text: str, *, started: float, ended: float, typed: bool = False) -> None:
        """Route one user utterance by the name it starts (or ends) with."""
        for route in self.book.parse(text):
            if route.stop:
                await self._stop(route.agent, route.text, typed)
                continue
            agent = route.agent
            if agent is None:
                agent, how = self._implicit(started, typed)
                if agent is None:
                    for voice in self.voices.values():
                        voice.silenced = False  # something new was said
                    self.trace.record("team.route", text=text, agent=None, how="unaddressed")
                    self.ui("team.user", {"text": route.text, "agent": None, "status": "unaddressed", "typed": typed})
                    continue
                route = route.to(agent, how)
            self.addressed = agent
            if route.bare:
                self._follow_up = (agent, time.monotonic() + FOLLOW_UP_SECONDS)
                self.trace.record("team.route", text=route.text, agent=agent, how="bare")
                self.ui("team.user", {"text": route.text, "agent": agent, "status": "listening", "typed": typed})
                continue
            self._follow_up = None
            self._last_named = (agent, ended)
            for voice in self.voices.values():
                voice.silenced = False
            self.trace.record("team.route", text=route.text, agent=agent, how=route.how)
            self.ui("team.user", {"text": route.text, "agent": agent, "status": "routed", "how": route.how, "typed": typed})
            self.voices[agent].ask(route.text)
        self.changed()

    def _implicit(self, started: float, typed: bool) -> tuple[str | None, str]:
        """An unnamed utterance continues a named one only right after it; nothing else is inferred."""
        now = time.monotonic()
        if self._follow_up and now <= self._follow_up[1]:
            agent = self._follow_up[0]
            self._follow_up = None
            return agent, "follow-up"
        if not typed and self._last_named and 0 <= started - self._last_named[1] <= CONTINUATION_SECONDS:
            return self._last_named[0], "continuation"
        return None, "none"

    async def _stop(self, agent: str | None, text: str, typed: bool) -> None:
        """"Stop" ends speech (everyone's, or the named agent's) and drops queued replies. Jobs keep running."""
        targets = [agent] if agent else list(self.voices)
        current = self.floor.current
        if current is not None and current.agent in targets:
            cut = await self.floor.cut()
            if cut is not None:
                cut.cancelled = True
        self.floor.drop(lambda r: r.agent in targets, "dismissed")
        for target in targets:
            self.voices[target].on_stop()
        self.trace.record("team.route", text=text, agent=agent, how="stop")
        self.ui("team.user", {"text": text, "agent": agent, "status": "stop", "typed": typed})
        self.ui("stop", {"phase": "requested", "agent": agent})
        self.changed()

    # ------------------------------------------------------------ floor and replies
    def _notice_ok(self, agent: str) -> bool:
        voice = self.voices.get(agent)
        return self.client_alive() and voice is not None and not voice.silenced

    def reply_created(self, reply: Reply) -> None:
        self.ui("team.reply", {**reply.public(), "phase": "thinking"})
        self.changed()

    def reply_text(self, reply: Reply) -> None:
        if reply is self.floor.current:
            self.ui("team.reply", {**reply.public(), "phase": "speaking", "text": reply.text})

    def _floor_event(self, event: str, reply: Reply) -> None:
        voice = self.voices.get(reply.agent)
        if event == "started":
            self.trace.record("team.reply", phase="speaking", agent=reply.agent, reply_kind=reply.kind, reply=reply.seq)
            self.ui("team.reply", {**reply.public(), "phase": "speaking", "text": reply.text})
        elif event in ("finished", "cut", "dropped"):
            phase = {"finished": "done", "cut": "cut", "dropped": "dropped"}[event]
            text = reply.heard_text() if event == "cut" else reply.text
            self.trace.record("team.reply", phase=phase, agent=reply.agent, reply_kind=reply.kind, reply=reply.seq,
                              outcome=reply.outcome, heard_ms=reply.heard // 48)
            self.ui("team.reply", {**reply.public(), "phase": phase, "text": text if reply.started_at is not None else ""})
            if voice is not None:
                if reply.started_at is not None:
                    voice.heard(reply)
                if reply.kind == "notice":
                    voice.notice_outcome(reply)
                if event != "finished" or reply.outcome != "played":
                    self._spawn(voice.settle_later(reply))
        self.changed()

    def _schedule_notices(self) -> None:
        floor = self.floor
        if floor.current is not None or floor.ready() or floor.user_speaking or floor.awaiting:
            return
        if any(r.kind != "notice" and r.state == "pending" for r in floor.replies):
            return  # an answer is on its way; let it go first
        if time.monotonic() - floor.last_activity < NOTICE_GAP or not self.client_alive():
            return
        for voice in self.voices.values():
            if voice.notice_ready():
                voice.deliver_notices()

    async def _ticker(self) -> None:
        next_notice = next_ui = 0.0
        while not self.closed:
            await asyncio.sleep(TICK_SECONDS)
            try:
                await self.floor.tick()
                now = time.monotonic()
                if now >= next_notice:
                    next_notice = now + 0.2
                    self._schedule_notices()
                if self._dirty and now >= next_ui:
                    next_ui = now + 0.1
                    self._publish()
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001 - keep the room alive
                logger.warning(f"Team room tick failed: {type(error).__name__}: {error}")

    # ------------------------------------------------------------ UI
    def changed(self) -> None:
        self._dirty = True

    def agent_states(self) -> dict[str, dict[str, Any]]:
        speaking = self.floor.current.agent if self.floor.current else None
        states = {}
        for agent_id, voice in self.voices.items():
            working = sum(1 for job in voice.runtime.jobs.public(limit=10) if job["status"] in ACTIVE)
            if speaking == agent_id:
                state = "speaking"
            elif self.floor.waiting(agent_id):
                state = "waiting"
            elif self.floor.thinking(agent_id) or voice.busy:
                state = "thinking"
            elif self._follow_up and self._follow_up[0] == agent_id and time.monotonic() <= self._follow_up[1]:
                state = "listening"
            else:
                state = "idle"
            states[agent_id] = {
                "state": state,
                "working": working,
                "notices": len(voice.notices),
                "addressed": self.addressed == agent_id,
                "online": voice.session is not None and not voice.session.closed,
                "silenced": voice.silenced,
            }
        return states

    def _publish(self) -> None:
        self._dirty = False
        payload = {**self.floor.snapshot(), "addressed": self.addressed, "agents": self.agent_states()}
        if payload != self._published:
            self._published = payload
            self.ui("team.floor", payload)

    def ui(self, kind: str, data: dict[str, Any]) -> None:
        """Browser-safe server message over RTVI (never secrets)."""
        if not self._client_ready or self.send_ui is None or self.closed:
            return
        self._spawn(self._send({**data, "kind": f"clawvoice.{kind}"}))  # the message kind always wins

    async def _send(self, message: dict[str, Any]) -> None:
        try:
            await self.send_ui(message)
        except Exception as error:  # noqa: BLE001 - UI updates are best effort
            logger.debug(f"Team UI message dropped: {type(error).__name__}")

    def state(self) -> dict[str, Any]:
        return {agent_id: rt.state() for agent_id, rt in self.runtimes.items()}

    # ------------------------------------------------------------ shutdown
    async def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        for voice in self.voices.values():
            voice.runtime.detach(voice.seat)
        # A notice being spoken that was already heard counts as delivered (never replayed);
        # notices generated or queued but not heard stay pending for the next connection.
        playing = self.floor.current
        heard = playing is not None and playing.kind == "notice" and self.floor.heard_now(playing) >= MIN_HEARD_BYTES
        unfinished = {id(r): r for r in [*self.floor.replies, playing] if r is not None and r.kind == "notice"}
        for reply in unfinished.values():
            state = "delivered" if heard and reply is playing else "pending"
            for item in reply.notice_items:
                self.voices[reply.agent]._mark(item["job_id"], state)
        for task in list(self._tasks):
            if task is not asyncio.current_task():
                task.cancel()
        await asyncio.gather(
            *(voice.close() for voice in self.voices.values()),
            *(self.listener.close() for _ in [0] if self.listener is not None),
            return_exceptions=True,
        )
        self.trace.record("team.closed", room=self.room_id)
