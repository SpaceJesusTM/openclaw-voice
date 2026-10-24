"""Direct Higgs Realtime sessions for a Team room.

A Team room runs several Higgs sessions behind one browser connection: a
silent listener (turn detection and transcription) and one voice per agent.
Pipecat's realtime service models exactly one session per pipeline, so the room
drives these sessions over WebSocket itself, the way the agent-selector
experiment did, and keeps Pipecat for WebRTC, RTVI and playback.

Same endpoint and pinned model as the single-agent path: every
``session.created`` / ``session.updated`` must acknowledge
``higgs-realtime-1.5-preview`` or the session is closed. The Boson key stays in
this process.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Awaitable, Callable

from loguru import logger
from websockets.asyncio.client import connect as websocket_connect
from websockets.exceptions import ConnectionClosed

from clawvoice.config import MODEL, TRANSCRIPTION_MODEL, Settings
from clawvoice.voice.telemetry import Trace

AUDIO_FORMAT = {"type": "audio/pcm", "rate": 24000}
# High-volume events kept out of the trace file.
_UNTRACED = frozenset(
    {
        "input_audio_buffer.append",
        "response.output_audio.delta",
        "response.output_audio_transcript.delta",
        "conversation.item.input_audio_transcription.delta",
        "response.output_text.delta",
    }
)

EventHandler = Callable[[dict[str, Any]], Awaitable[None]]


class SessionError(RuntimeError):
    pass


def session_config(
    *,
    instructions: str,
    modality: str,
    voice: str = "default",
    turn_detection: dict[str, Any] | None = None,
    transcription: bool = False,
    noise_reduction: str | None = None,
    tools: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    audio_input: dict[str, Any] = {"format": AUDIO_FORMAT, "turn_detection": turn_detection}
    if transcription:
        audio_input["transcription"] = {"model": TRANSCRIPTION_MODEL, "language": "en"}
    if noise_reduction:
        audio_input["noise_reduction"] = {"type": noise_reduction}
    return {
        "type": "realtime",
        "model": MODEL,
        "instructions": instructions,
        "output_modalities": [modality],
        "audio": {"input": audio_input, "output": {"format": AUDIO_FORMAT, "voice": voice}},
        "tools": tools or [],
        "tool_choice": "auto",
    }


def text_item(text: str) -> dict[str, Any]:
    return {
        "type": "conversation.item.create",
        "item": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]},
    }


def function_output(call_id: str, output: Any) -> dict[str, Any]:
    return {
        "type": "conversation.item.create",
        "item": {"type": "function_call_output", "call_id": call_id, "output": json.dumps(output, default=str)},
    }


def truncate(item_id: str, audio_end_ms: int) -> dict[str, Any]:
    return {"type": "conversation.item.truncate", "item_id": item_id, "content_index": 0, "audio_end_ms": max(0, int(audio_end_ms))}


class RealtimeSession:
    """One Higgs Realtime WebSocket with a single event listener for its lifetime."""

    def __init__(
        self,
        settings: Settings,
        *,
        label: str,
        config: dict[str, Any],
        on_event: EventHandler,
        trace: Trace,
        on_close: Callable[[RealtimeSession], Awaitable[None]] | None = None,
    ):
        self.settings = settings
        self.label = label
        self.config = config
        self._on_event = on_event
        self._on_close = on_close
        self.trace = trace
        self.ws = None
        self.resolved: dict[str, Any] = {}
        self.closed = True
        self.close_reason = ""
        self._reader: asyncio.Task | None = None
        self._closing = False

    async def connect(self, attempts: int = 4) -> None:
        last: Exception | None = None
        for attempt in range(attempts):
            if attempt:
                await asyncio.sleep(1.5 * attempt)  # voice validation is rate limited on first use
            try:
                ws = await websocket_connect(
                    self.settings.url,
                    additional_headers={"Authorization": f"Bearer {self.settings.api_key}", "User-Agent": "ClawVoice team room"},
                    max_size=None,
                    open_timeout=15,
                )
            except Exception as error:  # noqa: BLE001 - retried, then reported generically
                last = error
                continue
            try:
                await ws.send(json.dumps({"type": "session.update", "session": self.config}))
                event = await asyncio.wait_for(self._first(ws), timeout=15)
            except Exception as error:  # noqa: BLE001
                last = error
                await ws.close()
                continue
            if event.get("type") == "error":
                last = SessionError(str((event.get("error") or {}).get("message") or "session rejected")[:300])
                self.trace.record("team.session", label=self.label, phase="rejected", error=str(last), attempt=attempt)
                await ws.close()
                continue
            model = (event.get("session") or {}).get("model")
            if model != MODEL:
                await ws.close()
                raise SessionError(f"{self.label}: Boson acknowledged {model!r}; required {MODEL}. No fallback.")
            self.ws, self.resolved, self.closed, self.close_reason = ws, event.get("session") or {}, False, ""
            self.trace.record("team.session", label=self.label, phase="ready", model=model, voice=self.voice)
            self._reader = asyncio.create_task(self._read(ws), name=f"higgs:{self.label}")
            return
        raise SessionError(f"{self.label}: could not start a Higgs session ({type(last).__name__}: {last})")

    @property
    def voice(self) -> str | None:
        return ((self.resolved.get("audio") or {}).get("output") or {}).get("voice")

    @staticmethod
    async def _first(ws) -> dict[str, Any]:
        async for raw in ws:
            event = json.loads(raw)
            if event.get("type") in ("session.created", "session.updated", "error"):
                return event
        raise SessionError("closed before session.created")

    async def _read(self, ws) -> None:
        try:
            async for raw in ws:
                try:
                    event = json.loads(raw)
                except ValueError:
                    continue
                kind = event.get("type")
                if kind in ("session.created", "session.updated") and (event.get("session") or {}).get("model") != MODEL:
                    self.close_reason = "model changed"
                    logger.error(f"{self.label}: session model changed; closing (no fallback)")
                    break
                if kind not in _UNTRACED:
                    self.trace.record("team.recv", label=self.label, event=event)
                if kind in ("session.idle_timeout", "session.max_duration_reached"):
                    self.close_reason = kind
                try:
                    await self._on_event(event)
                except Exception as error:  # noqa: BLE001 - one bad event must not end the session
                    logger.warning(f"{self.label}: event handler failed on {kind}: {type(error).__name__}: {error}")
        except ConnectionClosed as error:
            self.close_reason = self.close_reason or f"closed ({error.rcvd.code if error.rcvd else 'no code'})"
        except asyncio.CancelledError:
            raise
        finally:
            self.closed = True
            if not self._closing:
                self.trace.record("team.session", label=self.label, phase="closed", reason=self.close_reason)
                if self._on_close is not None:
                    try:
                        await self._on_close(self)
                    except Exception as error:  # noqa: BLE001
                        logger.warning(f"{self.label}: close handler failed: {type(error).__name__}")

    async def send(self, event: dict[str, Any]) -> bool:
        if self.closed or self.ws is None:
            return False
        if event.get("type") not in _UNTRACED:
            self.trace.record("team.send", label=self.label, event=event)
        try:
            await self.ws.send(json.dumps(event))
            return True
        except ConnectionClosed:
            self.closed = True
            return False

    async def close(self) -> None:
        self._closing = True
        self.closed = True
        if self.ws is not None:
            try:
                await asyncio.wait_for(self.ws.close(), timeout=3)
            except Exception:  # noqa: BLE001 - best effort
                pass
        if self._reader is not None and self._reader is not asyncio.current_task():
            self._reader.cancel()
            await asyncio.gather(self._reader, return_exceptions=True)
