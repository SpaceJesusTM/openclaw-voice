import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from clawvoice.agents import parse_agent
from clawvoice.context.cache import HotContextCache
from clawvoice.context.models import Resolved, ResolverError
from clawvoice.helpers import HelperRunner
from clawvoice.paths import AGENTS_DIR
from clawvoice.registry import ComponentRegistry
from clawvoice.timeutil import FrozenClock, resolve_day

TORONTO = ZoneInfo("America/Toronto")


class Source:
    def __init__(self):
        self.value = "v1"
        self.calls = 0
        self.fail = False
        self.delay = 0.0


def mini_agent(topics, agent_id="tester"):
    data = {
        "id": agent_id,
        "timezone": "America/Toronto",
        "openclaw": {"gateway": {"ws_url": "ws://127.0.0.1:9", "http_url": "http://127.0.0.1:9", "token_ref": "env:NOPE"}},
        "hot_context": {"topics": topics},
    }
    return parse_agent(data, source=Path("mini.yaml"), base=AGENTS_DIR)


def build(topics, source, clock, agent_id="tester"):
    registry = ComponentRegistry()

    async def stub(ctx, spec):
        source.calls += 1
        if source.delay:
            await asyncio.sleep(source.delay)
        if source.fail:
            raise ResolverError("source down")
        day = resolve_day(spec.args["day"], ctx.tz, ctx.clock).isoformat() if "day" in spec.args else None
        return Resolved(value={"v": source.value, "day": day}, summary=f"value {source.value}", day=day)

    registry.resolvers["stub"] = stub
    agent = mini_agent(topics, agent_id)
    return HotContextCache(agent, registry, HelperRunner({}), clock=clock, tick_seconds=0.01)


TOPIC = {"key": "t.one", "resolver": "stub", "ttl_seconds": 60, "max_staleness_seconds": 300, "read_wait_seconds": 0.5}


@pytest.fixture
def clock():
    return FrozenClock(datetime(2026, 10, 23, 18, 0, tzinfo=timezone.utc))


async def test_fresh_hit_never_calls_the_source(clock):
    source = Source()
    cache = build([TOPIC], source, clock)
    assert await cache.refresh_now("t.one")
    for _ in range(5):
        reading = await cache.get("t.one")
        assert reading["status"] == "fresh" and reading["cache"] == "hit" and reading["data"]["v"] == "v1"
    assert source.calls == 1
    assert cache.metrics.hits_fresh == 5 and reading["read_ms"] < 50


async def test_stale_is_disclosed_and_refreshed_in_background(clock):
    source = Source()
    cache = build([TOPIC], source, clock)
    await cache.refresh_now("t.one")
    clock.advance(120)
    source.value = "v2"
    reading = await cache.get("t.one")
    assert reading["status"] == "stale" and reading["data"]["v"] == "v1"
    assert "minutes ago" in reading["note"] or "may not be current" in reading["note"]
    await asyncio.sleep(0.05)
    assert source.calls == 2
    assert (await cache.get("t.one"))["data"]["v"] == "v2"


async def test_expired_with_failing_source_is_labelled_last_known(clock):
    source = Source()
    cache = build([TOPIC], source, clock)
    await cache.refresh_now("t.one")
    clock.advance(1000)
    source.fail = True
    reading = await cache.get("t.one")
    assert reading["status"] == "expired" and reading["data"]["v"] == "v1"
    assert "out of date" in reading["note"]


async def test_missing_and_failing_is_unavailable_never_empty(clock):
    source = Source()
    source.fail = True
    cache = build([TOPIC], source, clock)
    reading = await cache.get("t.one")
    assert reading["status"] == "unavailable" and "data" not in reading
    assert "source down" in reading["reason"]
    assert "Never guess" in reading["note"]


async def test_unknown_topic_lists_available_topics(clock):
    cache = build([TOPIC], Source(), clock)
    reading = await cache.get("nope")
    assert reading["status"] == "unknown_topic" and reading["available_topics"] == ["t.one"]


async def test_invalidation_gives_read_your_writes(clock):
    source = Source()
    cache = build([TOPIC], source, clock)
    await cache.refresh_now("t.one")
    source.value = "after-write"
    source.delay = 0.05
    cache.invalidate(["t.one"], "verified job J1")
    reading = await cache.get("t.one")
    assert reading["status"] == "fresh" and reading["data"]["v"] == "after-write"
    assert reading["cache"] == "refreshed"


async def test_invalidation_during_refresh_forces_another_refresh(clock):
    source = Source()
    cache = build([TOPIC], source, clock)
    await cache.refresh_now("t.one")
    source.delay = 0.1
    source.value = "pre-write read"
    task = asyncio.create_task(cache.refresh_now("t.one", "scheduled"))
    await asyncio.sleep(0.02)
    clock.advance(1)  # the write lands after that read began
    cache.invalidate(["t.one"], "verified job J2")
    await task
    reading_state = next(r for r in cache.status() if r["topic"] == "t.one")
    assert reading_state["state"] == "invalidated"  # stale read did not clear it
    source.value = "post-write"
    reading = await cache.get("t.one")
    assert reading["data"]["v"] == "post-write"


async def test_invalidated_snapshot_is_withheld_if_refresh_is_slow(clock):
    source = Source()
    cache = build([{**TOPIC, "read_wait_seconds": 0.01}], source, clock)
    await cache.refresh_now("t.one")
    source.delay = 0.5
    cache.invalidate(["t.one"], "verified job J3")
    reading = await cache.get("t.one")
    assert reading["status"] == "refreshing" and "data" not in reading
    assert reading["last_known_summary"].startswith("(outdated")


async def test_concurrent_reads_share_one_refresh(clock):
    source = Source()
    source.delay = 0.05
    cache = build([TOPIC], source, clock)
    readings = await asyncio.gather(*(cache.get("t.one") for _ in range(8)))
    assert source.calls == 1
    assert all(r["status"] == "fresh" for r in readings)


async def test_failures_back_off_exponentially(clock):
    source = Source()
    source.fail = True
    cache = build([TOPIC], source, clock)
    state = cache._topics["t.one"]
    dues = []
    for _ in range(4):
        await cache.refresh_now("t.one")
        dues.append(state.due - clock.monotonic())
    assert dues == [5.0, 10.0, 20.0, 40.0]


async def test_active_cadence_is_faster_than_idle(clock):
    source = Source()
    cache = build([{**TOPIC, "idle_refresh_seconds": 900}], source, clock)
    await cache.refresh_now("t.one")
    idle_gap = cache._topics["t.one"].due - clock.monotonic()
    cache.session_started()
    await cache.refresh_now("t.one")
    active_gap = cache._topics["t.one"].due - clock.monotonic()
    assert 800 <= idle_gap <= 1000 and 40 <= active_gap <= 55


async def test_agents_have_isolated_namespaces(clock):
    a, b = Source(), Source()
    b.value = "other agent"
    cache_a = build([TOPIC], a, clock, "agent-a")
    cache_b = build([TOPIC], b, clock, "agent-b")
    await cache_a.refresh_now("t.one")
    await cache_b.refresh_now("t.one")
    assert (await cache_a.get("t.one"))["data"]["v"] == "v1"
    assert (await cache_b.get("t.one"))["data"]["v"] == "other agent"
    cache_a.invalidate(["t.one"], "write in a")
    assert next(r for r in cache_b.status())["state"] == "fresh"


@pytest.mark.parametrize(
    "start_local, expected_next_day",
    [
        (datetime(2026, 10, 23, 23, 59, tzinfo=TORONTO), "2026-10-24"),
        # DST ends in Toronto on 2026-11-01 (a 25-hour day).
        (datetime(2026, 10, 31, 23, 59, tzinfo=TORONTO), "2026-11-01"),
        (datetime(2026, 11, 1, 23, 59, tzinfo=TORONTO), "2026-11-02"),
    ],
)
async def test_local_midnight_rollover_never_serves_yesterday_as_today(start_local, expected_next_day):
    clock = FrozenClock(start_local.astimezone(timezone.utc))
    source = Source()
    cache = build([{**TOPIC, "key": "planner.today", "args": {"day": "today"}}], source, clock)
    await cache.refresh_now("planner.today")
    assert (await cache.get("planner.today"))["data"]["day"] == start_local.date().isoformat()
    clock.advance(120)  # 00:01 local, next civil day
    source.delay = 0.0
    reading = await cache.get("planner.today")
    assert reading["status"] == "fresh" and reading["data"]["day"] == expected_next_day
    assert source.calls == 2


async def test_scheduler_warms_topics_and_handles_rollover(clock):
    source = Source()
    cache = build([TOPIC, {**TOPIC, "key": "t.two", "args": {"day": "today"}}], source, clock)
    await cache.start(warm=True)
    try:
        clock.advance(1)
        await asyncio.sleep(0.1)
        assert all(row["state"] == "fresh" for row in cache.status())
        calls = source.calls
        clock.advance(timedelta(hours=12).total_seconds())  # crosses local midnight and the ttl
        await asyncio.sleep(0.1)
        assert source.calls > calls
    finally:
        await cache.stop()


# --------------------------------------------------- coverage and live fallback
async def test_reading_states_its_coverage_and_the_live_fallback(clock):
    """The cache is an accelerator: every answer says what it covers and how to go beyond it."""
    source = Source()
    cache = build([{**TOPIC, "coverage": "today's widgets only"}], source, clock)
    await cache.refresh_now("t.one")
    reading = await cache.get("t.one")
    assert reading["status"] == "fresh" and reading["covers"] == "today's widgets only"
    assert "covers today's widgets only" in reading["note"]
    assert "delegate_task (kind question)" in reading["note"]
    assert "Never say something does not exist or cannot be accessed" in reading["note"]
    assert "Answer from this data only" not in reading["note"]


async def test_resolver_can_narrow_coverage_to_the_exact_window(clock):
    source = Source()
    cache = build([TOPIC], source, clock)
    original = cache.registry.resolvers["stub"]

    async def windowed(ctx, spec):
        resolved = await original(ctx, spec)
        resolved.coverage = "events through Monday, October 26 only"
        return resolved

    cache.registry.resolvers["stub"] = windowed
    await cache.refresh_now("t.one")
    assert (await cache.get("t.one"))["covers"] == "events through Monday, October 26 only"


async def test_default_coverage_never_claims_completeness(clock):
    cache = build([TOPIC], Source(), clock)
    await cache.refresh_now("t.one")
    reading = await cache.get("t.one")
    assert reading["covers"] == "only what this snapshot shows"


async def test_incomplete_or_unavailable_is_not_proof_of_absence(clock):
    source = Source()
    cache = build([TOPIC], source, clock)
    original = cache.registry.resolvers["stub"]

    async def partial(ctx, spec):
        resolved = await original(ctx, spec)
        resolved.complete = False
        return resolved

    cache.registry.resolvers["stub"] = partial
    await cache.refresh_now("t.one")
    reading = await cache.get("t.one")
    assert reading["complete"] is False and "may be missing" in reading["note"] and "delegate_task" in reading["note"]
    down_source = Source()
    down = build([TOPIC], down_source, clock)
    down_source.fail = True
    await down.refresh_now("t.one")
    gone = await down.get("t.one")
    assert gone["status"] == "unavailable" and "data" not in gone
    assert "says nothing about the source" in gone["note"] and "delegate_task" in gone["note"]
