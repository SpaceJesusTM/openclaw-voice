"""Pinned pipecat-boson service with model verification and the validated fixes.

Carried over unchanged in behavior from experiments/pipecat-higgs-1.5/service.py:

1. Every resolved ``session.created`` / ``session.updated`` must acknowledge
   ``higgs-realtime-1.5-preview`` before the SDK processes it; runtime
   ``session.update`` events for any other model are refused. No fallback.
2. Native-audio lifetime fix: Boson can send a cancelled ``response.done``
   before ``speech_started``; the SDK's audio reference is retained until
   ``BotStoppedSpeakingFrame`` so its own truncation still runs on barge-in.

Added: per-agent instructions/voice/tools, and latency measurements from the
server event stream (speech end, typed input or tool result -> first audio).
The private dispatch hooks tie this class to pipecat-boson 0.1.4.
"""

from __future__ import annotations

import time

from pipecat_boson.realtime import BosonRealtimeLLMService

from clawvoice.config import MODEL, TRANSCRIPTION_MODEL, Settings
from clawvoice.voice.telemetry import Trace, sanitize


class VerifiedBosonService(BosonRealtimeLLMService):
    def __init__(self, settings: Settings, trace: Trace, tools, *, instructions: str, voice: str):
        options = {}
        if settings.noise_reduction:
            options["input_audio_noise_reduction"] = {"type": settings.noise_reduction}
        super().__init__(
            url=settings.url,
            api_key=settings.api_key,
            model=settings.model,
            voice=voice,
            output_modalities=["audio"],
            instructions=instructions,
            tools=tools,
            turn_detection=settings.turn_detection,
            input_audio_transcription_model=TRANSCRIPTION_MODEL,
            **options,
        )
        self.trace = trace
        self._register_event_handler("on_preview_event", sync=True)
        self._register_event_handler("on_latency", sync=True)
        self._first_audio: set[str] = set()
        # Turn start (speech end / typed text / notice) -> first audio of the next response.
        self._turn_mark: tuple[str, float] | None = None
        # Tool result -> first audio of the answer response created after it.
        self._tool_mark: float | None = None
        self._answer_response: str | None = None
        trace.record("configuration", model=settings.model, url=settings.url, voice=voice)

    async def send_client_event(self, event):
        payload = event.model_dump(exclude_none=True) if hasattr(event, "model_dump") else event
        # Reject runtime changes as well as constructor configuration.
        if payload.get("type") == "session.update" and payload.get("session", {}).get("model") != MODEL:
            raise ValueError("Runtime session model must remain higgs-realtime-1.5-preview")
        entry = self.trace.record("boson.send", event=payload)
        kind = payload.get("type")
        if kind == "conversation.item.create":
            item = payload.get("item") or {}
            if item.get("type") == "function_call_output":
                self._tool_mark = time.monotonic()
                self._answer_response = None
            elif item.get("role") == "user":
                text = str(((item.get("content") or [{}])[0] or {}).get("text") or "")
                origin = "notice" if text.startswith("[ClawVoice notice]") else "user_text"
                self._turn_mark = (origin, time.monotonic())
        await super().send_client_event(event)
        if kind in ("conversation.item.truncate", "response.cancel"):
            await self._call_event_handler("on_preview_event", entry)

    async def _handle_evt_response_done(self, event):
        # Boson can deliver cancelled response.done BEFORE speech_started.
        # The SDK closes generation frames and clears its audio reference here,
        # although the transport still has queued audio. Retain that native
        # reference until BotStoppedSpeakingFrame (handled by the SDK), allowing
        # its existing truncation method to run on speech_started. No new audio
        # buffering or interruption implementation is introduced.
        audio = self._current_audio_response
        await super()._handle_evt_response_done(event)
        if audio is not None and getattr(audio, "response_id", None) == getattr(event.response, "id", None):
            self._current_audio_response = audio

    async def _dispatch_boson_server_event(self, event):
        entry = self.trace.record("boson.recv", event=event)
        kind = event.get("type")
        if kind in ("session.created", "session.updated"):
            model = event.get("session", {}).get("model")
            if model != MODEL:
                self._api_session_ready = False
                self.trace.model = None
                message = f"Boson acknowledged {model!r}; required {MODEL}. No fallback."
                self.trace.failure = message
                self.trace.record("model.rejected", acknowledged=model, message=message)
                await self.push_error(message, force_treat_as_permanent=True)
                return False
            self.trace.model = model
            self.trace.record("model.verified", acknowledged=model)
        if kind == "input_audio_buffer.speech_stopped":
            self._turn_mark = ("speech_end", time.monotonic())
        if kind == "response.created" and self._tool_mark is not None and self._answer_response is None:
            self._answer_response = (event.get("response") or {}).get("id")
        important = kind in (
            "session.created",
            "session.updated",
            "response.created",
            "response.done",
            "error",
            "response.function_call_arguments.done",
            "conversation.item.added",
            "response.output_item.added",
            "input_audio_buffer.speech_started",
            "input_audio_buffer.speech_stopped",
        )
        if kind == "response.output_audio.delta" and event.get("response_id") not in self._first_audio:
            response_id = event.get("response_id")
            self._first_audio.add(response_id)
            important = True
            now = time.monotonic()
            if self._turn_mark is not None:
                origin, mark = self._turn_mark
                self._turn_mark = None
                latency = self.trace.record("latency", origin=origin, first_audio_ms=round((now - mark) * 1000))
                await self._call_event_handler("on_latency", latency)
            if self._tool_mark is not None and response_id and response_id == self._answer_response:
                ms = round((now - self._tool_mark) * 1000)
                self._tool_mark = self._answer_response = None
                latency = self.trace.record("latency", origin="tool_result", first_audio_ms=ms)
                await self._call_event_handler("on_latency", latency)
        if important:
            await self._call_event_handler("on_preview_event", sanitize(entry, self.trace.secrets))
        result = await super()._dispatch_boson_server_event(event)
        if result is False:
            self.trace.failure = "Boson terminated event processing; see diagnostic/API error"
            await self.push_error(self.trace.failure, force_treat_as_permanent=True)
        return result
