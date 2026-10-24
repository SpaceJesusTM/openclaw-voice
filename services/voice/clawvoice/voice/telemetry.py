"""Sanitized session diagnostics (adapted from experiments/pipecat-higgs-1.5).

Traces go to ignored ``services/voice/captures/session-*.jsonl``. They contain
conversation text (private, never committed) but no audio payloads or
credentials.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger
from pipecat.frames.frames import ErrorFrame, InterruptionFrame, TranscriptionFrame
from pipecat.observers.base_observer import BaseObserver, FramePushed

from clawvoice.paths import captures_dir

_SECRET_KEYS = ("authorization", "api_key", "client_secret", "token", "password")
_LOGGED = (
    "model.verified",
    "model.rejected",
    "tool.started",
    "tool.result",
    "job.event",
    "latency",
    "announcement",
    "diagnostic.error",
)


def sanitize(value, secrets: tuple[str, ...] = ()):
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if str(key).lower() in _SECRET_KEYS:
                continue
            if key in ("audio", "delta") and value.get("type") in (
                "input_audio_buffer.append",
                "response.output_audio.delta",
            ):
                result[key] = f"[{len(item)} base64 characters]"
            else:
                result[key] = sanitize(item, secrets)
        return result
    if isinstance(value, (tuple, list)):
        return [sanitize(item, secrets) for item in value]
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, "[REDACTED]")
        return value
    return value


_REGISTERED_SECRETS: set[str] = set()


def register_secret(value: str | None) -> None:
    """Redact this value from every log line from now on."""
    if value and len(value) >= 8:
        _REGISTERED_SECRETS.add(value)


def configure_logging(*secrets: str, level: str = "INFO") -> None:
    for secret in secrets:
        register_secret(secret)

    def redact(record):
        record["message"] = sanitize(record["message"], tuple(_REGISTERED_SECRETS))

    logger.configure(patcher=redact)
    logger.remove()
    logger.add(sys.stderr, level=level, backtrace=False, diagnose=False)


class Trace:
    def __init__(self, *secrets: str, label: str = "session", path: Path | None = None, persist: bool = True):
        self.secrets = tuple(s for s in secrets if s)
        self.started = time.monotonic()
        self.model: str | None = None
        self.failure: str | None = None
        self.events: list[dict] = []
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        self.path = path or captures_dir() / f"{label}-{stamp}.jsonl"
        self.persist = persist

    def record(self, kind: str, **data) -> dict:
        entry = sanitize(
            {"kind": kind, "elapsed_ms": round((time.monotonic() - self.started) * 1000), **data},
            self.secrets + tuple(_REGISTERED_SECRETS),
        )
        self.events.append(entry)
        if len(self.events) > 3000:
            del self.events[:1500]
        if self.persist:
            with self.path.open("a") as handle:
                handle.write(json.dumps(entry, default=str) + "\n")
        if kind in _LOGGED:
            logger.info(json.dumps(entry, default=str)[:1500])
        return entry


class DiagnosticObserver(BaseObserver):
    def __init__(self, trace: Trace):
        super().__init__()
        self.trace = trace

    async def on_push_frame(self, data: FramePushed):
        if not data.first_push:
            return
        frame = data.frame
        if isinstance(frame, ErrorFrame):
            self.trace.failure = frame.error
            self.trace.record("diagnostic.error", message=frame.error)
        elif isinstance(frame, TranscriptionFrame):
            self.trace.record("transcript.user", text=frame.text)
        elif isinstance(frame, InterruptionFrame):
            self.trace.record("interruption")
