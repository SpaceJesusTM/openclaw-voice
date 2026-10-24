"""Hot-context data contract shared by the cache, resolvers, tools and UI."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from clawvoice.agents import AgentConfig, TopicSpec
from clawvoice.helpers import HelperRunner
from clawvoice.timeutil import Clock, local_now


class ResolverError(RuntimeError):
    """A resolver could not produce a trustworthy value (message is owner-safe)."""


@dataclass
class Resolved:
    """What a resolver returns: a bounded, voice-safe view of authoritative data."""

    value: dict[str, Any]
    summary: str
    complete: bool = True
    warnings: list[str] = field(default_factory=list)
    day: str | None = None  # ISO date for day-scoped topics (enables rollover checks)
    source: str = ""
    coverage: str | None = None  # exact scope of this value, e.g. a date window; defaults to the topic's


@dataclass
class Snapshot:
    topic: str
    agent_id: str
    owner_scope: str
    value: dict[str, Any]
    summary: str
    complete: bool
    warnings: list[str]
    day: str | None
    source: str
    fetched_at: datetime
    fetched_mono: float
    fetch_ms: int
    version: int
    coverage: str = ""


@dataclass
class ResolverContext:
    agent: AgentConfig
    helpers: HelperRunner
    clock: Clock
    jobs: Callable[[], list[dict[str, Any]]] | None = None

    @property
    def tz(self):
        return self.agent.tz

    def now_local(self) -> datetime:
        return local_now(self.agent.tz, self.clock)


ResolverFn = Callable[[ResolverContext, TopicSpec], Any]
