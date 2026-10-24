"""Generic per-agent hot-context cache.

A materialized, read-only view of authoritative agent data, never a second
source of truth. Topics, resolvers and timing come from agent configuration;
this module contains no agent- or domain-specific logic.

Read semantics (``get``):

- fresh   age <= ttl, same local day, not invalidated: returned immediately.
- stale   age <= max_staleness: returned immediately with an age disclosure,
          and a background refresh starts.
- otherwise (missing, expired, invalidated by a write, or a new local day):
          a refresh is started/joined and awaited for at most
          ``read_wait_seconds``. If it does not succeed, the reading says so
          honestly: last-known data is clearly labelled and never presented
          as current; an invalidated or previous-day snapshot is withheld.

Refresh semantics: initial warm-up, active cadence (~0.8 x ttl, jittered)
while a voice session uses the agent, a slower idle cadence otherwise,
exponential backoff on failure, one in-flight refresh per topic, and forced
refresh at local midnight (IANA zone, DST-safe). An invalidation that arrives
while a refresh is running forces another refresh, so a read that started
before a write can never clear the write's invalidation (read-your-writes).
"""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from loguru import logger

from clawvoice.agents import AgentConfig, TopicSpec
from clawvoice.context.models import Resolved, ResolverContext, ResolverError, Snapshot
from clawvoice.helpers import HelperError, HelperRunner
from clawvoice.registry import ComponentRegistry
from clawvoice.timeutil import SYSTEM_CLOCK, Clock, age_phrase, local_now, resolve_day, spoken_clock

MAX_BACKOFF_SECONDS = 300.0


@dataclass
class _TopicState:
    spec: TopicSpec
    snapshot: Snapshot | None = None
    task: asyncio.Task | None = None
    invalidated: str | None = None
    invalidation_gen: int = 0
    due: float = 0.0
    failures: int = 0
    last_error: str | None = None
    last_attempt_mono: float | None = None
    last_refresh_ms: int | None = None
    version: int = 0


@dataclass
class CacheMetrics:
    hits_fresh: int = 0
    hits_stale: int = 0
    refreshed_on_read: int = 0
    unavailable: int = 0
    refresh_ok: int = 0
    refresh_failed: int = 0
    read_ms: list[float] = field(default_factory=list)

    def record_read(self, ms: float) -> None:
        self.read_ms.append(ms)
        del self.read_ms[:-200]

    def snapshot(self) -> dict[str, Any]:
        reads = sorted(self.read_ms)
        p50 = reads[len(reads) // 2] if reads else None
        p95 = reads[min(len(reads) - 1, int(len(reads) * 0.95))] if reads else None
        return {
            "hits_fresh": self.hits_fresh,
            "hits_stale": self.hits_stale,
            "refreshed_on_read": self.refreshed_on_read,
            "unavailable": self.unavailable,
            "refresh_ok": self.refresh_ok,
            "refresh_failed": self.refresh_failed,
            "read_ms_p50": round(p50, 2) if p50 is not None else None,
            "read_ms_p95": round(p95, 2) if p95 is not None else None,
        }


def _safe_error(error: BaseException) -> str:
    if isinstance(error, (ResolverError, HelperError)):
        return str(error)[:240]
    return f"{type(error).__name__} while refreshing"


class HotContextCache:
    def __init__(
        self,
        agent: AgentConfig,
        registry: ComponentRegistry,
        helpers: HelperRunner,
        *,
        clock: Clock = SYSTEM_CLOCK,
        jobs_view: Callable[[], list[dict[str, Any]]] | None = None,
        on_change: Callable[[str], None] | None = None,
        tick_seconds: float = 1.0,
    ):
        self.agent = agent
        self.registry = registry
        self.clock = clock
        self.ctx = ResolverContext(agent=agent, helpers=helpers, clock=clock, jobs=jobs_view)
        self._topics = {spec.key: _TopicState(spec) for spec in agent.topics}
        self._loop_task: asyncio.Task | None = None
        self._active_sessions = 0
        self._today: str | None = None
        self._on_change = on_change
        self._tick = tick_seconds
        self.metrics = CacheMetrics()

    # ------------------------------------------------------------- lifecycle
    @property
    def topic_keys(self) -> list[str]:
        return list(self._topics)

    async def start(self, *, warm: bool = True) -> None:
        now = self.clock.monotonic()
        for index, state in enumerate(self._topics.values()):
            state.due = now + (index * 0.25 if warm else state.spec.ttl_seconds)
        self._today = local_now(self.agent.tz, self.clock).date().isoformat()
        if self._loop_task is None:
            self._loop_task = asyncio.create_task(self._loop(), name=f"hot-context:{self.agent.id}")

    async def stop(self) -> None:
        tasks = [state.task for state in self._topics.values() if state.task and not state.task.done()]
        if self._loop_task:
            tasks.append(self._loop_task)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._loop_task = None

    def session_started(self) -> None:
        self._active_sessions += 1
        if self._active_sessions == 1:
            # Pull forward anything that would read stale in an active session.
            now = self.clock.monotonic()
            for state in self._topics.values():
                snap = state.snapshot
                if snap is None or now - snap.fetched_mono > 0.8 * state.spec.ttl_seconds:
                    state.due = min(state.due, now)

    def session_ended(self) -> None:
        self._active_sessions = max(0, self._active_sessions - 1)

    @property
    def active(self) -> bool:
        return self._active_sessions > 0

    # ------------------------------------------------------------- scheduling
    async def _loop(self) -> None:
        while True:
            try:
                self._check_rollover()
                now = self.clock.monotonic()
                for state in self._topics.values():
                    if (state.task is None or state.task.done()) and now >= state.due:
                        self._ensure_refresh(state, "scheduled")
            except Exception as error:  # noqa: BLE001 - keep the scheduler alive
                logger.warning(f"hot-context scheduler error for {self.agent.id}: {type(error).__name__}")
            await asyncio.sleep(self._tick)

    def _check_rollover(self) -> None:
        today = local_now(self.agent.tz, self.clock).date().isoformat()
        if self._today is not None and today != self._today:
            now = self.clock.monotonic()
            for state in self._topics.values():
                if state.snapshot is not None and state.snapshot.day is not None:
                    state.due = now
            logger.info(f"hot-context {self.agent.id}: local day rolled over to {today}")
        self._today = today

    def _cadence(self, spec: TopicSpec) -> float:
        base = 0.8 * spec.ttl_seconds if self.active else spec.idle_refresh_seconds
        return base * random.uniform(0.9, 1.1)

    def _ensure_refresh(self, state: _TopicState, reason: str) -> asyncio.Task:
        if state.task is None or state.task.done():
            state.task = asyncio.create_task(self._refresh(state, reason), name=f"refresh:{state.spec.key}")
        return state.task

    async def _refresh(self, state: _TopicState, reason: str) -> bool:
        spec = state.spec
        generation = state.invalidation_gen
        state.last_attempt_mono = self.clock.monotonic()
        wall_started = time.monotonic()
        try:
            resolver = self.registry.resolvers.get(spec.resolver)
            if resolver is None:
                raise ResolverError(f"No resolver named {spec.resolver!r}")
            resolved = await resolver(self.ctx, spec)
            if not isinstance(resolved, Resolved):
                raise ResolverError(f"Resolver {spec.resolver!r} returned an invalid value")
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - a failed refresh is reported, never fatal
            state.failures += 1
            state.last_error = _safe_error(error)
            state.due = self.clock.monotonic() + min(MAX_BACKOFF_SECONDS, 5.0 * 2 ** (state.failures - 1))
            self.metrics.refresh_failed += 1
            logger.warning(f"hot-context {self.agent.id}/{spec.key} refresh failed ({reason}): {state.last_error}")
            self._changed(spec.key)
            return False
        elapsed_ms = round((time.monotonic() - wall_started) * 1000)
        state.version += 1
        state.snapshot = Snapshot(
            topic=spec.key,
            agent_id=self.agent.id,
            owner_scope=self.agent.owner_scope,
            value=resolved.value,
            summary=resolved.summary,
            complete=resolved.complete,
            warnings=list(resolved.warnings),
            day=resolved.day,
            source=resolved.source or spec.resolver,
            coverage=resolved.coverage or spec.coverage,
            fetched_at=self.clock.now(),
            fetched_mono=self.clock.monotonic(),
            fetch_ms=elapsed_ms,
            version=state.version,
        )
        state.failures = 0
        state.last_error = None
        state.last_refresh_ms = elapsed_ms
        if state.invalidation_gen != generation:
            # A write was reported while this read was running: it may predate
            # the write, so keep the topic invalidated and read again.
            state.due = self.clock.monotonic()
        else:
            state.invalidated = None
            state.due = self.clock.monotonic() + self._cadence(spec)
        self.metrics.refresh_ok += 1
        self._changed(spec.key)
        return True

    def _changed(self, key: str) -> None:
        if self._on_change:
            try:
                self._on_change(key)
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------- writes
    def invalidate(self, keys: list[str] | tuple[str, ...], reason: str) -> list[str]:
        """Mark topics as not-current after a (verified or attempted) write."""
        done = []
        now = self.clock.monotonic()
        for key in keys:
            state = self._topics.get(key)
            if state is None:
                continue
            state.invalidated = reason
            state.invalidation_gen += 1
            state.due = now
            if state.task is None or state.task.done():
                self._ensure_refresh(state, f"invalidated: {reason}")
            done.append(key)
            self._changed(key)
        return done

    async def refresh_now(self, key: str, reason: str = "manual") -> bool:
        state = self._topics[key]
        task = self._ensure_refresh(state, reason)
        return bool(await asyncio.shield(task))

    # ------------------------------------------------------------- reads
    def _expected_day(self, state: _TopicState) -> str | None:
        day_arg = state.spec.args.get("day")
        if day_arg is not None:
            return resolve_day(str(day_arg), self.agent.tz, self.clock).isoformat()
        if state.snapshot is not None and state.snapshot.day is not None:
            return local_now(self.agent.tz, self.clock).date().isoformat()
        return None

    def _classify(self, state: _TopicState) -> tuple[str, float | None, bool]:
        """(state, age_seconds, same_day) for the current snapshot."""
        snap = state.snapshot
        if snap is None:
            return "missing", None, True
        expected = self._expected_day(state)
        same_day = expected is None or snap.day is None or snap.day == expected
        age = self.clock.monotonic() - snap.fetched_mono
        if state.invalidated is not None:
            return "invalidated", age, same_day
        if not same_day:
            return "previous_day", age, same_day
        if age <= state.spec.ttl_seconds:
            return "fresh", age, same_day
        if age <= state.spec.max_staleness_seconds:
            return "stale", age, same_day
        return "expired", age, same_day

    async def get(self, key: str) -> dict[str, Any]:
        started = time.perf_counter()
        state = self._topics.get(key)
        if state is None:
            return {
                "topic": key,
                "status": "unknown_topic",
                "available_topics": self.topic_keys,
                "note": "This topic is not configured for this agent.",
            }
        kind, _, _ = self._classify(state)
        cache = "hit"
        if kind == "fresh":
            self.metrics.hits_fresh += 1
        elif kind == "stale":
            self.metrics.hits_stale += 1
            self._ensure_refresh(state, "stale read")
        else:
            loop = asyncio.get_running_loop()
            deadline = loop.time() + state.spec.read_wait_seconds
            while True:
                task = self._ensure_refresh(state, f"read ({kind})")
                remaining = deadline - loop.time()
                if remaining <= 0:
                    break
                try:
                    succeeded = await asyncio.wait_for(asyncio.shield(task), timeout=remaining)
                except (asyncio.TimeoutError, Exception):  # noqa: BLE001 - reported below
                    break
                kind, _, _ = self._classify(state)
                # A successful read that began before a newer write keeps the topic
                # invalidated; read again while the budget allows.
                if kind == "fresh" or not succeeded:
                    break
            if self._classify(state)[0] == "fresh":
                self.metrics.refreshed_on_read += 1
                cache = "refreshed"
            else:
                cache = "miss"
        reading = self._reading(state, cache)
        if reading["status"] in ("unavailable", "refreshing"):
            self.metrics.unavailable += 1
        elapsed = (time.perf_counter() - started) * 1000
        self.metrics.record_read(elapsed)
        reading["read_ms"] = round(elapsed, 2)
        return reading

    def _reading(self, state: _TopicState, cache: str) -> dict[str, Any]:
        kind, age, same_day = self._classify(state)
        now_local = local_now(self.agent.tz, self.clock)
        base: dict[str, Any] = {
            "topic": state.spec.key,
            "agent": self.agent.display_name,
            "now_local": now_local.strftime("%A, %B ") + str(now_local.day) + ", " + spoken_clock(now_local),
            "timezone": self.agent.timezone,
            "cache": cache,
            "covers": (state.snapshot.coverage if state.snapshot else "") or state.spec.coverage,
        }
        snap = state.snapshot
        if kind in ("fresh", "stale", "expired") and snap is not None:
            status = {"fresh": "fresh", "stale": "stale", "expired": "expired"}[kind]
            base.update(
                status=status,
                as_of=spoken_clock(snap.fetched_at.astimezone(self.agent.tz)),
                age_seconds=round(age or 0),
                age=age_phrase(age or 0),
                complete=snap.complete,
                warnings=list(snap.warnings),
                summary=snap.summary,
                data=snap.value,
            )
            notes = []
            if status == "stale":
                notes.append(f"This information is from {age_phrase(age or 0)}; mention that it may not be current.")
            if status == "expired":
                notes.append(
                    f"Refreshing failed; this is last-known data from {age_phrase(age or 0)}. "
                    "Say clearly that it may be out of date."
                )
            if not snap.complete:
                notes.append("Some sources were unavailable: say what may be missing. Missing data is not free time.")
            notes.append(self._beyond(base["covers"]))
            base["note"] = " ".join(notes)
            return base
        if kind == "invalidated" and snap is not None:
            base.update(
                status="refreshing",
                reason="A recent change is still being re-read from the source.",
                last_known_summary=f"(outdated, before the latest change) {snap.summary}",
                note=(
                    "Do not present outdated data as current. Say the update is still loading "
                    "and offer to check again in a moment, or call delegate_task for a live lookup."
                ),
            )
            return base
        if kind == "previous_day":
            base.update(
                status="refreshing",
                reason="A new day started; today's data is still loading.",
                note="Say it is still loading and offer to check again in a moment.",
            )
            return base
        base.update(
            status="unavailable",
            reason=state.last_error or "The data has not loaded yet.",
            note=(
                "The fast snapshot is unavailable; that says nothing about the source. Never guess or treat it "
                f"as empty. Call delegate_task (kind question) so {self.agent.display_name} can look it up live, "
                "or say you could not check it right now."
            ),
        )
        return base

    def _beyond(self, covers: str) -> str:
        return (
            f"This snapshot covers {covers}. Answer from it only for questions inside that scope. For anything "
            f"outside it (other dates or periods, more detail, or a live check the owner asks for), call "
            f"delegate_task (kind question): {self.agent.display_name} can look it up with its own tools. Never "
            "say something does not exist or cannot be accessed just because this snapshot does not show it."
        )

    # ------------------------------------------------------------- status
    def status(self) -> list[dict[str, Any]]:
        rows = []
        for state in self._topics.values():
            kind, age, _ = self._classify(state)
            refreshing = state.task is not None and not state.task.done()
            snap = state.snapshot
            rows.append(
                {
                    "topic": state.spec.key,
                    "description": state.spec.description,
                    "state": kind,
                    "refreshing": refreshing,
                    "age_seconds": round(age) if age is not None else None,
                    "ttl_seconds": state.spec.ttl_seconds,
                    "max_staleness_seconds": state.spec.max_staleness_seconds,
                    "complete": snap.complete if snap else None,
                    "day": snap.day if snap else None,
                    "version": state.version,
                    "last_refresh_ms": state.last_refresh_ms,
                    "failures": state.failures,
                    "last_error": state.last_error,
                    "invalidated": state.invalidated,
                    "summary": snap.summary if snap else None,
                }
            )
        return rows
