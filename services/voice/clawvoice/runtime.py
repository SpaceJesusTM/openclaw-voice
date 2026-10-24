"""Process-level ClawVoice runtime: one AgentRuntime per configured agent.

Voice connections come and go; agent adapters, caches and jobs live here for
the life of the server process, so background work continues and finished
results are kept when a browser disconnects. Each agent is isolated: its own
Gateway adapter, helper runner, cache namespace and job store.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from clawvoice.agents import AgentConfig, AgentRegistry, ConfigError
from clawvoice.context.cache import HotContextCache
from clawvoice.helpers import HelperRunner
from clawvoice.jobs.manager import JobManager
from clawvoice.jobs.models import Job
from clawvoice.jobs.store import JobStore
from clawvoice.openclaw.adapter import OpenClawAgent
from clawvoice.registry import ComponentRegistry, default_registry
from clawvoice.timeutil import SYSTEM_CLOCK, Clock

if TYPE_CHECKING:
    from clawvoice.voice.session import VoiceSession


class AgentRuntime:
    def __init__(
        self,
        agent: AgentConfig,
        components: ComponentRegistry,
        *,
        state_directory: Path,
        clock: Clock = SYSTEM_CLOCK,
        persist_jobs: bool = True,
    ):
        self.agent = agent
        for plugin in agent.plugins:
            components.load_plugin(plugin)
        missing = components.check_agent(agent)
        if missing:
            raise ConfigError(f"{agent.id}: no component provides {', '.join(missing)}")
        self.components = components
        self.helpers = HelperRunner(agent.helpers, extra_env=dict(agent.extra.get("helper_env") or {}))
        self.backend = OpenClawAgent(agent, state_directory=state_directory)
        self.store = JobStore(state_directory / "jobs", agent.id, persist=persist_jobs)
        self.cache = HotContextCache(
            agent, components, self.helpers, clock=clock, jobs_view=self._jobs_view, on_change=self._cache_changed
        )
        self.jobs = JobManager(
            agent, self.backend, self.store, components, self.helpers, invalidate=self.cache.invalidate, clock=clock
        )
        self.jobs.subscribe(self._job_event)
        self.sessions: dict[str, VoiceSession] = {}
        self._order: list[str] = []
        self.started = False

    def _jobs_view(self) -> list[dict[str, Any]]:
        return self.jobs.public(limit=10)

    async def start(self) -> None:
        mode = await self.backend.select_transport()
        logger.info(f"Agent {self.agent.id}: OpenClaw transport {mode or 'unavailable'}")
        await self.cache.start(warm=True)
        await self.jobs.start()
        self.started = True

    async def stop(self) -> None:
        await self.jobs.stop()
        await self.cache.stop()
        await self.backend.close()

    # ------------------------------------------------------------ sessions
    def attach(self, session: VoiceSession) -> None:
        self.sessions[session.id] = session
        self._order.append(session.id)
        self.cache.session_started()
        for job in self.jobs.pending_announcements():
            session.announce(job, earlier=job.origin_session != session.id)

    def detach(self, session: VoiceSession) -> None:
        if self.sessions.pop(session.id, None) is not None:
            self.cache.session_ended()
        if session.id in self._order:
            self._order.remove(session.id)

    def _target_session(self, job: Job) -> VoiceSession | None:
        if job.origin_session and job.origin_session in self.sessions:
            return self.sessions[job.origin_session]
        return self.sessions.get(self._order[-1]) if self._order else None

    # ------------------------------------------------------------ events
    def _job_event(self, event: str, job: Job) -> None:
        public = job.public()
        if event in ("job.accepted", "job.dispatched", "job.finished"):
            recent = [t.key for t in self.agent.topics if t.resolver == "recent_jobs"]
            if recent:
                self.cache.invalidate(recent, f"job {job.id} {job.status}")
        for session in list(self.sessions.values()):
            session.ui("job", {"event": event, "job": public})
        if event == "job.finished" and job.announcement.get("state") == "pending":
            target = self._target_session(job)
            if target is not None:
                target.announce(job, earlier=False)

    def _cache_changed(self, key: str) -> None:
        if not self.sessions:
            return
        row = next((r for r in self.cache.status() if r["topic"] == key), None)
        for session in list(self.sessions.values()):
            session.ui("cache", {"topic": key, "row": row})

    # ------------------------------------------------------------ status
    def state(self) -> dict[str, Any]:
        return {
            "agent": self.agent.public_identity(),
            "gateway": self.backend.health(),
            "cache": {"topics": self.cache.status(), "metrics": self.cache.metrics.snapshot(), "active": self.cache.active},
            "jobs": self.jobs.public(limit=20),
            "helpers": {"calls": self.helpers.calls, "shared": self.helpers.shared},
            "voice_sessions": len(self.sessions),
        }


class Runtime:
    def __init__(
        self,
        registry: AgentRegistry,
        *,
        state_directory: Path,
        components: ComponentRegistry | None = None,
        clock: Clock = SYSTEM_CLOCK,
        persist_jobs: bool = True,
    ):
        self.registry = registry
        self.components = components or default_registry()
        self.agents: dict[str, AgentRuntime] = {
            agent.id: AgentRuntime(
                agent, self.components, state_directory=state_directory, clock=clock, persist_jobs=persist_jobs
            )
            for agent in registry.all()
        }

    async def start(self) -> None:
        await asyncio.gather(*(agent.start() for agent in self.agents.values()))

    async def stop(self) -> None:
        await asyncio.gather(*(agent.stop() for agent in self.agents.values()), return_exceptions=True)

    def agent(self, agent_id: str | None) -> AgentRuntime:
        config = self.registry.get(agent_id)
        return self.agents[config.id]


_RUNTIME: Runtime | None = None


def set_runtime(runtime: Runtime | None) -> None:
    global _RUNTIME
    _RUNTIME = runtime


def get_runtime() -> Runtime:
    if _RUNTIME is None:
        raise RuntimeError("ClawVoice runtime is not started")
    return _RUNTIME
