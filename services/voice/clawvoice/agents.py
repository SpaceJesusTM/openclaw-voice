"""Declarative agent configuration: identity, OpenClaw backend, hot context, delegation.

An agent with ``backend: voice`` is voice-only: a Higgs conversation with its own
personality and voice, and no OpenClaw Gateway, tools, cache topics or jobs.

Agents are described in ``services/voice/agents/<id>.yaml`` and listed in
``agents/team.yaml``. Nothing in the runtime branches on a particular agent ID:
names, colors, voices, Gateway endpoints, helper commands, cache topics and
receipt verifiers all come from configuration. See docs/voice.md.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

from clawvoice.paths import AGENTS_DIR

_ID = re.compile(r"[a-z][a-z0-9-]{0,31}")
_TOPIC = re.compile(r"[a-z][a-z0-9_.-]{0,63}")
_COLOR = re.compile(r"#[0-9a-fA-F]{6}")
# A spoken name or a likely transcription of it, matched as one lowercase word.
_ALIAS = re.compile(r"[a-z][a-z']{1,23}")
_LOOPBACK = {"127.0.0.1", "localhost", "::1"}


class ConfigError(ValueError):
    """Agent configuration is invalid; the message names the file and field."""


DEFAULT_COVERAGE = "only what this snapshot shows"


@dataclass(frozen=True)
class TopicSpec:
    key: str
    description: str
    resolver: str
    args: dict[str, Any]
    ttl_seconds: float
    max_staleness_seconds: float
    idle_refresh_seconds: float
    read_wait_seconds: float
    # What the snapshot can answer. Anything outside it is a live lookup, not "nothing".
    coverage: str = DEFAULT_COVERAGE


@dataclass(frozen=True)
class ReceiptType:
    name: str
    verifier: str
    args: dict[str, Any]
    invalidates: tuple[str, ...]
    format: str


@dataclass(frozen=True)
class GatewaySpec:
    ws_url: str
    http_url: str
    token_ref: str
    transport: str  # auto | ws | http
    device_file: str
    allow_remote: bool = False
    pairing_hint: str = "openclaw devices approve {request_id}"


@dataclass(frozen=True)
class AgentConfig:
    id: str
    display_name: str
    color: str
    emoji: str
    role_markdown: str
    owner_scope: str
    timezone: str
    voice: str
    greeting: str
    openclaw_agent_id: str
    gateway: GatewaySpec | None  # None for a voice-only agent
    session_key_template: str
    question_timeout_s: float
    change_timeout_s: float
    helpers: dict[str, tuple[str, ...]]
    topics: tuple[TopicSpec, ...]
    targets: tuple[str, ...]
    receipt_types: dict[str, ReceiptType]
    plugins: tuple[str, ...]
    clarify_hint: str = "what exactly should change, and any date, time and title it needs"
    # Words that address this agent in a Team room: its name plus likely transcriptions.
    aliases: tuple[str, ...] = ()
    # Voice-only: no OpenClaw backend, so no tools, cache topics, jobs or delegation.
    voice_only: bool = False
    tagline: str = ""  # short role label for the UI, e.g. "Entertainment"
    fixture: bool = False
    source_path: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def session_key(self) -> str:
        return self.session_key_template.format(
            agent_id=self.openclaw_agent_id, owner_scope=self.owner_scope, id=self.id
        )

    def topic(self, key: str) -> TopicSpec | None:
        return next((topic for topic in self.topics if topic.key == key), None)

    def public_identity(self) -> dict[str, Any]:
        """Browser-safe identity: no endpoints, references or helper commands."""
        return {
            "id": self.id,
            "display_name": self.display_name,
            "color": self.color,
            "emoji": self.emoji,
            "role": _first_paragraph(self.role_markdown),
            "timezone": self.timezone,
            "owner_scope": self.owner_scope,
            "topics": [{"key": t.key, "description": t.description} for t in self.topics],
            "aliases": list(self.aliases),
            "voice_only": self.voice_only,
            "tagline": self.tagline,
            "fixture": self.fixture,
        }


def _first_paragraph(markdown: str) -> str:
    for block in markdown.split("\n\n"):
        text = " ".join(line.strip() for line in block.splitlines() if not line.startswith("#")).strip()
        if text:
            return text[:280]
    return ""


def _require(data: dict, key: str, where: str, kind: type | tuple = str):
    value = data.get(key)
    if value is None or not isinstance(value, kind) or (isinstance(value, str) and not value.strip()):
        raise ConfigError(f"{where}: '{key}' is required")
    return value


def _number(data: dict, key: str, default: float, where: str, minimum: float = 0) -> float:
    value = data.get(key, default)
    if not isinstance(value, (int, float)) or value < minimum:
        raise ConfigError(f"{where}: '{key}' must be a number >= {minimum}")
    return float(value)


def _check_url(url: str, schemes: tuple[str, ...], allow_remote: bool, where: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in schemes or not parsed.hostname:
        raise ConfigError(f"{where}: {url!r} must use {' or '.join(schemes)}")
    if not allow_remote and parsed.hostname not in _LOOPBACK:
        raise ConfigError(f"{where}: Gateway must be loopback-only unless allow_remote is set")
    return url.rstrip("/")


def parse_agent(data: dict[str, Any], *, source: Path, base: Path) -> AgentConfig:
    where = str(source.name)
    if not isinstance(data, dict):
        raise ConfigError(f"{where}: expected a mapping")
    agent_id = _require(data, "id", where)
    if not _ID.fullmatch(agent_id):
        raise ConfigError(f"{where}: id must match {_ID.pattern}")
    color = data.get("color", "#64748b")
    if not _COLOR.fullmatch(str(color)):
        raise ConfigError(f"{where}: color must be #RRGGBB")
    timezone_name = data.get("timezone", "UTC")
    try:
        ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError):
        raise ConfigError(f"{where}: unknown IANA timezone {timezone_name!r}") from None
    role_markdown = ""
    if data.get("role_file"):
        role_path = (base / data["role_file"]).resolve()
        if not role_path.is_relative_to(base.resolve()) or not role_path.is_file():
            raise ConfigError(f"{where}: role_file must be a file inside {base}")
        role_markdown = role_path.read_text(encoding="utf-8").strip()

    backend = str(data.get("backend") or "openclaw")
    if backend not in ("openclaw", "voice"):
        raise ConfigError(f"{where}: backend must be openclaw or voice")
    voice_only = backend == "voice"
    if voice_only:
        # Honest by construction: a voice-only agent declares nothing it cannot do.
        declared = [key for key in ("openclaw", "helpers", "hot_context", "delegation", "plugins") if data.get(key)]
        if declared:
            raise ConfigError(f"{where}: a voice-only agent (backend: voice) cannot declare {', '.join(declared)}")
    openclaw = data.get("openclaw") or {}
    gateway = openclaw.get("gateway") or {}
    gateway_spec: GatewaySpec | None = None
    if not voice_only:
        allow_remote = bool(gateway.get("allow_remote", False))
        transport = gateway.get("transport", "auto")
        if transport not in ("auto", "ws", "http"):
            raise ConfigError(f"{where}: openclaw.gateway.transport must be auto, ws or http")
        token_ref = _require(gateway, "token_ref", f"{where} openclaw.gateway")
        if not token_ref.startswith(("env:", "envfile:")):
            raise ConfigError(f"{where}: token_ref must be a server-side reference (env: or envfile:)")
        gateway_spec = GatewaySpec(
            ws_url=_check_url(_require(gateway, "ws_url", where), ("ws", "wss"), allow_remote, where),
            http_url=_check_url(_require(gateway, "http_url", where), ("http", "https"), allow_remote, where),
            token_ref=token_ref,
            transport=transport,
            device_file=str(gateway.get("device_file", "gateway-device.json")),
            allow_remote=allow_remote,
            pairing_hint=str(gateway.get("pairing_hint") or "openclaw devices approve {request_id}"),
        )
    session = openclaw.get("session") or {}
    timeouts = openclaw.get("timeouts") or {}

    helpers: dict[str, tuple[str, ...]] = {}
    for name, argv in (data.get("helpers") or {}).items():
        if not isinstance(argv, list) or not argv or not all(isinstance(a, str) and a for a in argv):
            raise ConfigError(f"{where}: helper {name!r} must be a non-empty argv list")
        helpers[str(name)] = tuple(argv)

    hot = data.get("hot_context") or {}
    defaults = hot.get("defaults") or {}
    topics: list[TopicSpec] = []
    for index, raw in enumerate(hot.get("topics") or []):
        at = f"{where} hot_context.topics[{index}]"
        key = _require(raw, "key", at)
        if not _TOPIC.fullmatch(key):
            raise ConfigError(f"{at}: key must match {_TOPIC.pattern}")
        if any(topic.key == key for topic in topics):
            raise ConfigError(f"{at}: duplicate topic {key!r}")
        merged = {**defaults, **raw}
        ttl = _number(merged, "ttl_seconds", 120, at, 1)
        max_stale = _number(merged, "max_staleness_seconds", max(ttl * 5, 600), at, 1)
        if max_stale < ttl:
            raise ConfigError(f"{at}: max_staleness_seconds must be >= ttl_seconds")
        topics.append(
            TopicSpec(
                key=key,
                description=str(raw.get("description") or key),
                resolver=_require(raw, "resolver", at),
                args=dict(raw.get("args") or {}),
                ttl_seconds=ttl,
                max_staleness_seconds=max_stale,
                idle_refresh_seconds=_number(merged, "idle_refresh_seconds", max(ttl, 900), at, 1),
                read_wait_seconds=_number(merged, "read_wait_seconds", 3.0, at, 0),
                coverage=" ".join(str(raw.get("coverage") or DEFAULT_COVERAGE).split()),
            )
        )

    delegation = data.get("delegation") or {}
    receipt_types: dict[str, ReceiptType] = {}
    topic_keys = {topic.key for topic in topics}
    for name, raw in (delegation.get("receipt_types") or {}).items():
        at = f"{where} delegation.receipt_types.{name}"
        invalidates = tuple(raw.get("invalidates") or ())
        unknown = [key for key in invalidates if key not in topic_keys]
        if unknown:
            raise ConfigError(f"{at}: invalidates unknown topics {unknown}")
        receipt_types[str(name)] = ReceiptType(
            name=str(name),
            verifier=_require(raw, "verifier", at),
            args=dict(raw.get("args") or {}),
            invalidates=invalidates,
            format=str(raw.get("format") or f'{{"type":"{name}"}}'),
        )
    targets = tuple(str(t) for t in (delegation.get("targets") or ["other"]))
    if "other" not in targets:
        targets = (*targets, "other")
    display_name = str(data.get("display_name") or agent_id.title())
    aliases = [display_name.lower()]
    for raw in (data.get("addressing") or {}).get("aliases") or []:
        alias = str(raw).strip().lower()
        if not _ALIAS.fullmatch(alias):
            raise ConfigError(f"{where}: addressing alias {raw!r} must be one word (letters only)")
        if alias not in aliases:
            aliases.append(alias)

    return AgentConfig(
        id=agent_id,
        display_name=display_name,
        color=str(color),
        emoji=str(data.get("emoji") or ""),
        role_markdown=role_markdown,
        owner_scope=str(data.get("owner_scope") or "local-owner"),
        timezone=timezone_name,
        voice=str((data.get("voice") or {}).get("boson_voice") or "default"),
        greeting=str(data.get("greeting") or f"Hi, it's {data.get('display_name') or agent_id}. What can I do?"),
        openclaw_agent_id=str(openclaw.get("agent_id") or agent_id),
        gateway=gateway_spec,
        session_key_template=str(session.get("key_template") or "agent:{agent_id}:clawvoice-{owner_scope}"),
        question_timeout_s=_number(timeouts, "question_seconds", 180, where, 5),
        change_timeout_s=_number(timeouts, "change_seconds", 300, where, 5),
        helpers=helpers,
        topics=tuple(topics),
        targets=targets,
        receipt_types=receipt_types,
        plugins=tuple(str(p) for p in (data.get("plugins") or ())),
        clarify_hint=str(
            delegation.get("clarify") or "what exactly should change, and any date, time and title it needs"
        ),
        aliases=tuple(aliases),
        voice_only=voice_only,
        tagline=str(data.get("tagline") or "")[:40],
        fixture=bool(data.get("fixture", False)),
        source_path=str(source),
        extra=dict(data.get("extra") or {}),
    )


@dataclass
class AgentRegistry:
    agents: dict[str, AgentConfig]
    default_id: str

    def get(self, agent_id: str | None) -> AgentConfig:
        key = agent_id or self.default_id
        if key not in self.agents:
            raise KeyError(f"Unknown or disabled agent {key!r}")
        return self.agents[key]

    @property
    def default(self) -> AgentConfig:
        return self.agents[self.default_id]

    def all(self) -> list[AgentConfig]:
        return list(self.agents.values())


def load_agent_file(path: Path) -> AgentConfig:
    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    return parse_agent(data, source=path, base=path.parent)


def load_registry(directory: Path = AGENTS_DIR, environ: dict[str, str] | None = None) -> AgentRegistry:
    """Load agents listed in team.yaml (or CLAWVOICE_AGENTS) from ``directory``."""
    environ = os.environ if environ is None else environ
    team_path = directory / "team.yaml"
    team = yaml.safe_load(team_path.read_text(encoding="utf-8")) if team_path.is_file() else {}
    requested = environ.get("CLAWVOICE_AGENTS")
    ids = [item.strip() for item in requested.split(",") if item.strip()] if requested else list(
        team.get("agents") or []
    )
    if environ.get("CLAWVOICE_ENABLE_FIXTURES", "").lower() in ("1", "true", "yes") and not requested:
        ids += [item for item in team.get("fixture_agents") or [] if item not in ids]
    if not ids:
        raise ConfigError("No agents configured; list them in agents/team.yaml or CLAWVOICE_AGENTS")
    agents: dict[str, AgentConfig] = {}
    for agent_id in ids:
        if not _ID.fullmatch(agent_id):
            raise ConfigError(f"Invalid agent id {agent_id!r}")
        config = load_agent_file(directory / f"{agent_id}.yaml")
        if config.id != agent_id:
            raise ConfigError(f"{agent_id}.yaml declares id {config.id!r}")
        agents[agent_id] = config
    default_id = environ.get("CLAWVOICE_DEFAULT_AGENT") or team.get("default_agent") or ids[0]
    if default_id not in agents:
        default_id = ids[0]
    return AgentRegistry(agents=agents, default_id=default_id)
