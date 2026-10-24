"""Sanitized experiment diagnostics; audio stays in Pipecat's transport."""

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger
from pipecat.frames.frames import ErrorFrame, InterruptionFrame, LLMTextFrame, TranscriptionFrame
from pipecat.observers.base_observer import BaseObserver, FramePushed

from config import ROOT


def sanitize(value, secret=""):
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if key.lower() in ("authorization", "api_key", "client_secret"):
                continue
            if key in ("audio", "delta") and value.get("type") in (
                "input_audio_buffer.append",
                "response.output_audio.delta",
            ):
                result[key] = f"[{len(item)} base64 characters]"
            else:
                result[key] = sanitize(item, secret)
        return result
    if isinstance(value, (tuple, list)):
        return [sanitize(item, secret) for item in value]
    if isinstance(value, str) and secret:
        return value.replace(secret, "[REDACTED]")
    return value


def configure_logging(secret=""):
    def redact(record):
        record["message"] = sanitize(record["message"], secret)

    logger.configure(patcher=redact)
    logger.remove()
    logger.add(sys.stderr, level="INFO", backtrace=False, diagnose=False)


class Trace:
    def __init__(self, secret="", path: Path | None = None, persist=True):
        self.secret = secret
        self.started = time.monotonic()
        self.model = None
        self.failure = None
        self.events = []
        self.path = (
            path
            or ROOT / "captures" / f"session-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}.jsonl"
        )
        self.persist = persist
        if persist:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, kind, **data):
        entry = sanitize(
            {"kind": kind, "elapsed_ms": round((time.monotonic() - self.started) * 1000), **data}, self.secret
        )
        self.events.append(entry)
        if len(self.events) > 2000:
            del self.events[:1000]
        if self.persist:
            with self.path.open("a") as file:
                file.write(json.dumps(entry) + "\n")
        if kind in (
            "model.verified",
            "model.rejected",
            "tool.started",
            "tool.result",
            "worker.result",
            "diagnostic.error",
        ):
            logger.info(json.dumps(entry))
        return entry


class DiagnosticObserver(BaseObserver):
    def __init__(self, trace):
        super().__init__()
        self.trace = trace

    async def on_push_frame(self, data: FramePushed):
        if not data.first_push:
            return
        frame = data.frame
        if isinstance(frame, ErrorFrame):
            self.trace.failure = frame.error
            self.trace.record("diagnostic.error", message=frame.error)
        elif isinstance(frame, (TranscriptionFrame, LLMTextFrame)):
            self.trace.record("transcript", frame=type(frame).__name__, text=frame.text)
        elif isinstance(frame, InterruptionFrame):
            self.trace.record("interruption", frame=type(frame).__name__)
