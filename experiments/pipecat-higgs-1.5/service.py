"""Pinned SDK with diagnostics and a native-audio lifetime compatibility fix."""

from pipecat_boson.realtime import BosonRealtimeLLMService

from config import MODEL, Settings
from telemetry import Trace, sanitize


class VerifiedBosonService(BosonRealtimeLLMService):
    """Private dispatch hook is covered by tests and tied to pipecat-boson 0.1.4."""

    def __init__(self, settings: Settings, trace: Trace, tools):
        options = {}
        if settings.noise_reduction:
            options["input_audio_noise_reduction"] = {"type": settings.noise_reduction}
        super().__init__(
            url=settings.url,
            api_key=settings.api_key,
            model=settings.model,
            voice=settings.voice,
            output_modalities=["audio"],
            instructions="You are a concise voice assistant testing a demo planner. Speak one or two sentences without markdown. Use get_demo_planner for planner questions and answer from its result."
            + (" Search the web for current public facts." if settings.web_search else ""),
            tools=tools,
            turn_detection=settings.turn_detection,
            input_audio_transcription_model="higgs-stt-3.1",
            **options,
        )
        self.trace = trace
        self._register_event_handler("on_preview_event", sync=True)
        self._first_audio = set()
        trace.record(
            "configuration",
            model=settings.model,
            url=settings.url,
            voice=settings.voice,
            mock_worker=settings.mock_worker,
            web_search=settings.web_search,
        )

    async def send_client_event(self, event):
        payload = event.model_dump(exclude_none=True) if hasattr(event, "model_dump") else event
        # Reject runtime changes as well as constructor configuration.
        if payload.get("type") == "session.update" and payload.get("session", {}).get("model") != MODEL:
            raise ValueError("Runtime session model must remain higgs-realtime-1.5-preview")
        entry = self.trace.record("boson.send", event=payload)
        await super().send_client_event(event)
        if payload.get("type") in ("conversation.item.truncate", "response.cancel"):
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
            self._first_audio.add(event.get("response_id"))
            important = True
        if important:
            await self._call_event_handler("on_preview_event", sanitize(entry, self.trace.secret))
        result = await super()._dispatch_boson_server_event(event)
        if result is False:
            self.trace.failure = "Boson terminated event processing; see diagnostic/API error"
            await self.push_error(self.trace.failure, force_treat_as_permanent=True)
        return result
