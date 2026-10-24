"""Local configuration. Never prints or returns credentials to the client."""

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent
MODEL = "higgs-realtime-1.5-preview"
URL = f"wss://api.boson.ai/v1/realtime?model={MODEL}"


def utf8_console():
    """Windows consoles default to a legacy code page that cannot print Pipecat's banner or model text."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")


def preview_model(value: str) -> str:
    if value != MODEL:
        raise ValueError(f"Required model is {MODEL}; rejected {value!r}. No fallback.")
    return value


@dataclass(frozen=True)
class Settings:
    api_key: str = field(repr=False)
    model: str = MODEL
    url: str = URL
    voice: str = "default"
    web_search: bool = False
    mock_worker: bool = False
    vad_threshold: float | None = None
    vad_silence_ms: int | None = None
    noise_reduction: str | None = None

    def __post_init__(self):
        preview_model(self.model)
        parsed = urlparse(self.url)
        if (
            parsed.scheme != "wss"
            or parsed.netloc != "api.boson.ai"
            or parsed.path.rstrip("/") != "/v1/realtime"
            or parsed.fragment
            or parse_qs(parsed.query) != {"model": [MODEL]}
        ):
            raise ValueError("BOSON_REALTIME_URL must be the official endpoint with the preview model query")
        if self.vad_threshold is not None and not 0 <= self.vad_threshold <= 1:
            raise ValueError("BOSON_VAD_THRESHOLD must be between 0 and 1")
        if self.vad_silence_ms is not None and not 100 <= self.vad_silence_ms <= 10000:
            raise ValueError("BOSON_VAD_SILENCE_MS must be between 100 and 10000")
        if self.noise_reduction not in (None, "near_field", "far_field"):
            raise ValueError("BOSON_NOISE_REDUCTION must be blank, near_field, or far_field")

    @property
    def turn_detection(self) -> dict:
        result = {"type": "server_vad"}
        if self.vad_threshold is not None:
            result["threshold"] = self.vad_threshold
        if self.vad_silence_ms is not None:
            result["silence_duration_ms"] = self.vad_silence_ms
        return result


def load_settings(environ=None, root=ROOT, require_key=True) -> Settings:
    values = {}
    # Empty optional values do not erase the already configured shared key.
    for source in (
        dotenv_values(root.parent / "higgs-realtime-1.5" / ".env"),
        dotenv_values(root / ".env"),
        os.environ if environ is None else environ,
    ):
        values.update({key: str(value).strip() for key, value in source.items() if value})

    def flag(name):
        raw = values.get(name, "false").lower()
        if raw not in ("true", "false", "1", "0"):
            raise ValueError(f"{name} must be true or false")
        return raw in ("true", "1")

    settings = Settings(
        api_key=values.get("BOSON_API_KEY", ""),
        model=values.get("BOSON_REALTIME_MODEL", MODEL),
        url=values.get("BOSON_REALTIME_URL", URL),
        voice=values.get("BOSON_VOICE", "default"),
        web_search=flag("BOSON_WEB_SEARCH"),
        mock_worker=flag("CLAWVOICE_MOCK_WORKER"),
        vad_threshold=float(values["BOSON_VAD_THRESHOLD"]) if "BOSON_VAD_THRESHOLD" in values else None,
        vad_silence_ms=int(values["BOSON_VAD_SILENCE_MS"]) if "BOSON_VAD_SILENCE_MS" in values else None,
        noise_reduction=values.get("BOSON_NOISE_REDUCTION"),
    )
    if require_key and not settings.api_key:
        raise ValueError("BOSON_API_KEY missing: configure this experiment or ../higgs-realtime-1.5/.env")
    return settings
