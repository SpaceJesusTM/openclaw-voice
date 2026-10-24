import asyncio
import json
import os
import stat
import time

import pytest

from clawvoice.jobs.models import Job
from clawvoice.jobs.store import MAX_JOBS, JobStore
from clawvoice.registry import default_registry
from clawvoice.runtime import AgentRuntime
from conftest import astra_config, tari_fixture_config, write_fixture


async def runtime_for(agent, fixture_dir):
    components = default_registry()
    runtime = AgentRuntime(agent, components, state_directory=fixture_dir.parent / "state")
    await runtime.start()
    return runtime


async def until(predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("condition not reached in time")


def finished(runtime, job_id):
    return lambda: runtime.jobs.get(job_id).terminal


async def test_delegation_is_nonblocking_and_cache_answers_meanwhile(gateway, fixture_dir):
    gateway.delay_scale = 1.0
    runtime = await runtime_for(astra_config(gateway.port), fixture_dir)
    try:
        started = time.perf_counter()
        job, duplicate = runtime.jobs.submit(request="Please run the tests (delay 1.0 seconds)", kind="change", target="tests")
        accept_ms = (time.perf_counter() - started) * 1000
        assert not duplicate and job.status == "accepted" and accept_ms < 50
        await until(lambda: runtime.jobs.get(job.id).status == "running")
        # A cached answer while the job is still running.
        reading = await runtime.cache.get("project.active")
        assert reading["status"] == "fresh" and reading["read_ms"] < 50
        assert runtime.jobs.get(job.id).status == "running"
        await until(finished(runtime, job.id))
        done = runtime.jobs.get(job.id)
        assert done.status == "completed" and done.verification["status"] == "verified"
        assert "42 of 42 passed" in done.outcome_text and done.outcome_text.startswith("Verified:")
        assert done.timings["dispatch_ack_ms"] < 1000 and done.timings["run_ms"] >= 900
        assert "exec: pytest -q tests" in done.evidence["tools"]
        # Read-your-writes: the verified change invalidated tests.last, so the next read is current.
        after = await runtime.cache.get("tests.last")
        assert after["status"] == "fresh" and "42 of 42" in after["summary"]
        assert done.announcement["state"] == "pending"  # kept for the next voice session
        assert gateway.runs_started == 1
    finally:
        await runtime.stop()


async def test_duplicate_tool_calls_never_dispatch_twice(gateway, fixture_dir):
    gateway.delay_scale = 1.0
    runtime = await runtime_for(astra_config(gateway.port), fixture_dir)
    try:
        first, dup1 = runtime.jobs.submit(request="run the tests delay 0.3 s", kind="change", target="tests", tool_call_id="call-1")
        again, dup2 = runtime.jobs.submit(request="run the tests delay 0.3 s", kind="change", target="tests", tool_call_id="call-1")
        same, dup3 = runtime.jobs.submit(request="Run the tests, delay 0.3 s!", kind="change", target="tests", tool_call_id="call-2")
        assert (dup1, dup2, dup3) == (False, True, True) and first.id == again.id == same.id
        await until(finished(runtime, first.id))
        late, dup4 = runtime.jobs.submit(request="run the tests delay 0.3 s", kind="change", target="tests", tool_call_id="call-3")
        assert dup4 and late.id == first.id  # just completed: not repeated
        assert gateway.runs_started == 1
    finally:
        await runtime.stop()


@pytest.mark.parametrize(
    "request_text, status, verification, phrase",
    [
        ("claim change about the release", "completed", "mismatch", "doesn't match"),
        ("no receipt for this change", "uncertain", None, "couldn't verify"),
        ("fail on purpose", "uncertain", None, "Part of the change may have happened"),
        ("clarify the suite", "completed", None, "needs more detail"),
        ("tidy the release notes", "completed", None, "No change was made."),
    ],
)
async def test_outcomes_are_never_overstated(gateway, fixture_dir, request_text, status, verification, phrase):
    runtime = await runtime_for(astra_config(gateway.port), fixture_dir)
    try:
        job, _ = runtime.jobs.submit(request=request_text, kind="change", target="project")
        await until(finished(runtime, job.id))
        done = runtime.jobs.get(job.id)
        assert done.status == status, done.outcome_text
        assert (done.verification or {}).get("status") == verification
        assert phrase in done.outcome_text
        assert not done.outcome_text.startswith("Verified") or status == "completed"
    finally:
        await runtime.stop()


async def test_failed_question_is_failed_not_uncertain(gateway, fixture_dir):
    runtime = await runtime_for(astra_config(gateway.port), fixture_dir)
    try:
        job, _ = runtime.jobs.submit(request="please fail this question", kind="question", target="other")
        await until(finished(runtime, job.id))
        done = runtime.jobs.get(job.id)
        assert done.status == "failed" and not done.side_effects_possible
    finally:
        await runtime.stop()


async def test_question_returns_agent_answer(gateway, fixture_dir):
    runtime = await runtime_for(astra_config(gateway.port), fixture_dir)
    try:
        job, _ = runtime.jobs.submit(request="What is the build status", kind="question", target="other")
        await until(finished(runtime, job.id))
        done = runtime.jobs.get(job.id)
        assert done.status == "completed" and done.outcome_text.startswith("Fixture answer about")
        assert "CLAWVOICE_RECEIPT" not in done.outcome_text and done.verification is None
    finally:
        await runtime.stop()


async def test_explicit_cancel_aborts_and_flags_possible_side_effects(gateway, fixture_dir):
    gateway.delay_scale = 1.0
    runtime = await runtime_for(astra_config(gateway.port), fixture_dir)
    try:
        job, _ = runtime.jobs.submit(request="run the tests delay 5 seconds", kind="change", target="tests")
        await until(lambda: runtime.jobs.get(job.id).run_id is not None)
        result = await runtime.jobs.cancel(job.id, source="voice")
        assert result["status"] == "cancelling"
        await until(finished(runtime, job.id))
        done = runtime.jobs.get(job.id)
        assert done.status == "cancelled" and done.side_effects_possible
        assert "may already have been made" in done.outcome_text
        assert done.announcement["state"] == "suppressed"  # the voice tool already said so
        assert gateway.runs[done.run_id].status == "aborted"
    finally:
        await runtime.stop()


async def test_jobs_queue_in_order_and_queued_cancel_never_dispatches(gateway, fixture_dir):
    gateway.delay_scale = 1.0
    runtime = await runtime_for(astra_config(gateway.port), fixture_dir)
    try:
        first, _ = runtime.jobs.submit(request="first: delay 0.5 seconds", kind="question", target="other")
        second, _ = runtime.jobs.submit(request="second question", kind="question", target="other")
        third, _ = runtime.jobs.submit(request="third question", kind="question", target="other")
        assert second.status == "queued" and third.status == "queued"
        assert (await runtime.jobs.cancel(second.id))["status"] == "cancelled"
        await until(finished(runtime, third.id))
        assert runtime.jobs.get(second.id).status == "cancelled" and runtime.jobs.get(second.id).run_id is None
        assert runtime.jobs.get(first.id).status == runtime.jobs.get(third.id).status == "completed"
        assert gateway.runs_started == 2
    finally:
        await runtime.stop()


async def test_socket_drop_mid_run_recovers_exactly_once(gateway, fixture_dir):
    gateway.delay_scale = 1.0
    gateway.drop_next_after_send = True
    runtime = await runtime_for(astra_config(gateway.port), fixture_dir)
    try:
        job, _ = runtime.jobs.submit(request="run the tests delay 0.4 seconds", kind="change", target="tests")
        await until(finished(runtime, job.id))
        done = runtime.jobs.get(job.id)
        assert done.status == "completed" and done.verification["status"] == "verified"
        assert gateway.runs_started == 1
    finally:
        await runtime.stop()


async def test_lost_acknowledgement_is_redispatched_with_same_idempotency_key(gateway, fixture_dir):
    gateway.drop_next_before_reply = True
    runtime = await runtime_for(astra_config(gateway.port), fixture_dir)
    try:
        job, _ = runtime.jobs.submit(request="run the tests", kind="change", target="tests")
        await until(finished(runtime, job.id), timeout=15)
        done = runtime.jobs.get(job.id)
        assert done.status == "completed" and done.attempts == 2
        assert gateway.runs_started == 1  # Gateway deduplicated the resubmission
    finally:
        await runtime.stop()


async def test_http_transport_completes_and_verifies(gateway, fixture_dir):
    runtime = await runtime_for(astra_config(gateway.port, transport="http"), fixture_dir)
    try:
        assert runtime.backend.mode == "http"
        job, _ = runtime.jobs.submit(request="run the tests", kind="change", target="tests")
        await until(finished(runtime, job.id))
        done = runtime.jobs.get(job.id)
        assert done.transport == "http" and done.status == "completed" and done.verification["status"] == "verified"
    finally:
        await runtime.stop()


async def test_http_ambiguous_failure_is_uncertain_and_never_retried(gateway, fixture_dir):
    gateway.http_fail_status = 503
    runtime = await runtime_for(astra_config(gateway.port, transport="http"), fixture_dir)
    try:
        job, _ = runtime.jobs.submit(request="run the tests", kind="change", target="tests")
        await until(finished(runtime, job.id))
        done = runtime.jobs.get(job.id)
        assert done.status == "uncertain" and done.attempts == 1 and done.side_effects_possible
        gateway.http_fail_status = 400
        job2, _ = runtime.jobs.submit(request="run the tests again", kind="change", target="tests")
        await until(finished(runtime, job2.id))
        assert runtime.jobs.get(job2.id).status == "failed" and not runtime.jobs.get(job2.id).side_effects_possible
    finally:
        await runtime.stop()


@pytest.mark.parametrize("how", ["gateway shutdown", "network loss"])
async def test_http_stream_lost_mid_run_is_uncertain_not_failed(gateway, fixture_dir, how):
    gateway.delay_scale = 1.0
    runtime = await runtime_for(astra_config(gateway.port, transport="http"), fixture_dir)
    try:
        job, _ = runtime.jobs.submit(request="run the tests delay 3 seconds", kind="change", target="tests")
        await until(lambda: runtime.jobs.get(job.id).run_id is not None)
        await asyncio.sleep(0.2)
        if how == "gateway shutdown":
            await gateway.stop()  # the run is aborted mid-way and the stream reports an error
        else:
            gateway.break_http_streams()  # the connection simply drops: no error, no [DONE]
        await until(finished(runtime, job.id))
        done = runtime.jobs.get(job.id)
        assert done.status == "uncertain" and done.side_effects_possible and done.attempts == 1
        assert "check before asking again" in done.outcome_text.lower()
    finally:
        await runtime.stop()


async def test_explicit_cancel_of_http_job_stops_the_gateway_run(gateway, fixture_dir):
    gateway.delay_scale = 1.0
    runtime = await runtime_for(astra_config(gateway.port, transport="http"), fixture_dir)
    try:
        job, _ = runtime.jobs.submit(request="run the tests delay 5 seconds", kind="change", target="tests")
        await until(lambda: runtime.jobs.get(job.id).run_id is not None)
        await asyncio.sleep(0.2)
        assert (await runtime.jobs.cancel(job.id, source="ui"))["status"] == "cancelling"
        await until(finished(runtime, job.id))
        done = runtime.jobs.get(job.id)
        assert done.status == "cancelled" and done.side_effects_possible
        assert done.announcement["state"] == "pending"  # cancelled from the UI: still announced
        await until(lambda: all(run.status != "running" for run in gateway.runs.values()))
        assert [run.status for run in gateway.runs.values()] == ["aborted"]
    finally:
        await runtime.stop()


async def test_unreachable_gateway_fails_without_side_effects(fixture_dir):
    runtime = await runtime_for(astra_config(9, transport="http"), fixture_dir)  # nothing listens on port 9
    try:
        assert runtime.backend.mode is None
        job, _ = runtime.jobs.submit(request="run the tests", kind="change", target="tests")
        await until(finished(runtime, job.id))
        done = runtime.jobs.get(job.id)
        assert done.status == "failed" and not done.side_effects_possible and done.attempts == 3
    finally:
        await runtime.stop()


async def test_restart_recovers_or_marks_uncertain(gateway, fixture_dir):
    gateway.delay_scale = 1.0
    agent = astra_config(gateway.port)
    first = await runtime_for(agent, fixture_dir)
    job, _ = first.jobs.submit(request="run the tests delay 0.8 seconds", kind="change", target="tests")
    await until(lambda: first.jobs.get(job.id).run_id is not None)
    await first.stop()  # ClawVoice restarts while the agent keeps working
    store = JobStore(fixture_dir.parent / "state" / "jobs", agent.id)
    assert store.get(job.id).status == "running"
    store.put(Job(id="JHTTP1", agent_id=agent.id, owner_scope="o", session_key="k", kind="change", target="tests",
                  request="x", title="http job", expectations={}, user_words="", origin_session=None, tool_call_id=None,
                  fingerprint="f1", idempotency_key="k1", status="running", transport="http", run_id="http-1", attempts=1))
    store.put(Job(id="JNEW01", agent_id=agent.id, owner_scope="o", session_key="k", kind="change", target="tests",
                  request="y", title="never sent", expectations={}, user_words="", origin_session=None, tool_call_id=None,
                  fingerprint="f2", idempotency_key="k2", status="accepted"))
    second = await runtime_for(agent, fixture_dir)
    try:
        await until(finished(second, job.id))
        assert second.jobs.get(job.id).status == "completed"
        assert second.jobs.get("JHTTP1").status == "uncertain"
        assert second.jobs.get("JNEW01").status == "failed" and "Not started" in second.jobs.get("JNEW01").outcome_text
        assert gateway.runs_started == 1
    finally:
        await second.stop()


def test_store_is_private_bounded_and_reloadable(tmp_path):
    store = JobStore(tmp_path / "jobs", "astra")
    for index in range(MAX_JOBS + 20):
        store.put(Job(id=f"J{index:05d}", agent_id="astra", owner_scope="o", session_key="k", kind="question",
                      target="other", request="r", title="t", expectations={}, user_words="", origin_session=None,
                      tool_call_id=None, fingerprint=str(index), idempotency_key=str(index), status="completed"))
    assert stat.S_IMODE(os.stat(store.path).st_mode) == 0o600
    reloaded = JobStore(tmp_path / "jobs", "astra")
    assert len(reloaded.jobs) == MAX_JOBS
    assert "agent:" not in json.dumps([j.public() for j in reloaded.ordered()])  # no session keys in public views


async def test_tari_calendar_flow_verifies_and_invalidates_calendar_topics(gateway, fixture_dir):
    write_fixture(fixture_dir, "planner", {"time_zone": "America/Toronto", "days": {}, "checklist": [], "calendar_complete": True})
    write_fixture(fixture_dir, "calendar", {"events": {"primary/evt42": {
        "id": "evt42", "summary": "Project Meeting", "status": "confirmed",
        "start": {"dateTime": "2026-10-24T19:00:00Z"}, "end": {"dateTime": "2026-10-24T20:00:00Z"}}}})
    runtime = await runtime_for(tari_fixture_config(gateway.port, transport="ws"), fixture_dir)
    try:
        await until(lambda: all(r["state"] == "fresh" for r in runtime.cache.status()))
        job, _ = runtime.jobs.submit(
            request="Create a Calendar event called Project Meeting (scripted calendar event evt42)",
            kind="change", target="calendar",
            expectations={"title": "Project Meeting", "date": "2026-10-24", "start_time": "3pm", "end_time": "16:00"},
        )
        await until(finished(runtime, job.id))
        done = runtime.jobs.get(job.id)
        assert done.status == "completed" and done.verification["status"] == "verified", done.outcome_text
        assert "Calendar event 'Project Meeting'" in done.outcome_text and "3 to 4 PM" in done.outcome_text
        states = {r["topic"]: r for r in runtime.cache.status()}
        assert states["calendar.today"]["version"] >= 2 and states["planner.tomorrow"]["version"] >= 2
    finally:
        await runtime.stop()


async def test_only_recent_unheard_results_are_announced_on_reconnect(gateway, fixture_dir):
    from datetime import datetime, timedelta, timezone

    runtime = await runtime_for(astra_config(gateway.port), fixture_dir)
    try:
        now = datetime.now(timezone.utc)
        for job_id, hours in (("JOLD001", 30), ("JNEW001", 1)):
            job = Job(id=job_id, agent_id="astra", owner_scope="o", session_key="k", kind="question", target="other",
                      request="r", title=job_id, expectations={}, user_words="", origin_session=None,
                      tool_call_id=None, fingerprint=job_id, idempotency_key=job_id, status="completed",
                      announcement={"state": "pending", "attempts": 0},
                      timings={"finished_at": (now - timedelta(hours=hours)).isoformat()})
            runtime.store.put(job)
        assert [job.id for job in runtime.jobs.pending_announcements()] == ["JNEW001"]
        assert runtime.jobs.get("JOLD001").status == "completed"  # still retrievable via check_tasks
    finally:
        await runtime.stop()
