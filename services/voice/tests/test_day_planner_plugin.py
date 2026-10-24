from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from clawvoice.context.models import ResolverContext
from clawvoice.helpers import HelperRunner
from clawvoice.jobs.models import Expectations
from clawvoice.jobs.verifiers import VerifyContext, run_verifier
from clawvoice.plugins import day_planner
from clawvoice.timeutil import FrozenClock
from conftest import tari_fixture_config, write_fixture

TORONTO = ZoneInfo("America/Toronto")
NOON = FrozenClock(datetime(2026, 10, 23, 12, 0, tzinfo=TORONTO))

PLANNER = {
    "time_zone": "America/Toronto",
    "calendar_complete": True,
    "days": {
        "2026-10-23": [
            {"kind": "calendar", "id": "allday1", "title": "Reading week", "allday": True, "start": 0, "end": 0},
            {"kind": "planner", "id": "b1", "title": "Deep work", "start": 540, "end": 660},
            {"kind": "commute", "id": "b2", "title": "Commute", "start": 840, "end": 900},
            {"kind": "calendar", "id": "evt1", "title": "Project\nMeeting\x07", "start": 900, "end": 960,
             "start_at": "2026-10-23T15:00:00-04:00", "end_at": "2026-10-23T16:00:00-04:00"},
        ],
        "2026-10-24": [
            {"kind": "calendar", "id": "evt2", "title": "Study group", "start": 600, "end": 690,
             "start_at": "2026-10-24T10:00:00-04:00", "end_at": "2026-10-24T11:30:00-04:00"},
        ],
    },
    "checklist": [
        {"section": "Personal", "tasks": [
            {"id": "t1", "done": False, "status": "Priority", "text": "Review prototype"},
            {"id": "t2", "done": False, "status": "In-progress", "text": "Book dentist"},
            {"id": "t3", "done": True, "status": None, "text": "Old task"},
            {"id": "t4", "done": False, "status": None, "text": "Read paper"},
        ]},
    ],
}


@pytest.fixture
def agent(fixture_dir):
    write_fixture(fixture_dir, "planner", PLANNER)
    return tari_fixture_config(calendar=True)


def ctx(agent, clock=NOON):
    return ResolverContext(agent=agent, helpers=HelperRunner(agent.helpers, memo_seconds=0), clock=clock)


async def test_planner_day_is_an_accurate_spoken_view(agent):
    resolved = await day_planner.planner_day(ctx(agent), agent.topic("planner.today"))
    assert resolved.complete and resolved.day == "2026-10-23"
    summary = resolved.summary
    assert summary.startswith("Today, Friday, October 23:")
    assert "1 Calendar event: Project Meeting, 3 to 4 PM" in summary
    assert "1 planner block: Deep work, 9 to 11 AM" in summary
    assert "Commutes: Commute, 2 to 3 PM" in summary and "All day: Reading week" in summary
    assert "Checklist: 3 open tasks (1 priority, 1 in progress)" in summary
    assert resolved.value["calendar_events"][0]["title"] == "Project Meeting"  # control chars removed


async def test_tomorrow_and_empty_days(agent):
    tomorrow = await day_planner.planner_day(ctx(agent), agent.topic("planner.tomorrow"))
    assert tomorrow.summary.startswith("Tomorrow, Saturday, October 24:") and "Study group, 10 to 11:30 AM" in tomorrow.summary
    later = FrozenClock(datetime(2026, 10, 25, 9, 0, tzinfo=TORONTO))
    empty = await day_planner.planner_day(ctx(agent, later), agent.topic("planner.today"))
    assert "nothing is scheduled" in empty.summary


async def test_unreadable_calendar_is_never_reported_as_free(agent, fixture_dir):
    write_fixture(fixture_dir, "planner", {**PLANNER, "calendar_complete": False})
    planner = await day_planner.planner_day(ctx(agent), agent.topic("planner.today"))
    assert not planner.complete and planner.summary.startswith("Calendar could not be fully read")
    assert any("not verified" in w for w in planner.warnings)
    calendar = await day_planner.calendar_day(ctx(agent), agent.topic("calendar.today"))
    assert not calendar.complete and "can't confirm" in calendar.summary
    nxt = await day_planner.calendar_next(ctx(agent), agent.topic("calendar.next"))
    assert not nxt.complete and "can't confirm" in nxt.summary


async def test_calendar_views(agent):
    today = await day_planner.calendar_day(ctx(agent), agent.topic("calendar.today"))
    assert today.summary == "Calendar today, Friday, October 23: Project Meeting, 3 to 4 PM; Reading week (all day)."
    nxt = await day_planner.calendar_next(ctx(agent), agent.topic("calendar.next"))
    assert nxt.summary == "Your next Calendar event is Project Meeting, today, Friday, October 23, 3 to 4 PM."
    during = FrozenClock(datetime(2026, 10, 23, 15, 30, tzinfo=TORONTO))
    current = await day_planner.calendar_next(ctx(agent, during), agent.topic("calendar.next"))
    assert current.summary.startswith("You're in Project Meeting right now, until 4 PM.")
    assert "Next: Study group, tomorrow, Saturday, October 24, 10 to 11:30 AM." in current.summary


async def test_checklist_focus(agent):
    resolved = await day_planner.checklist_focus(ctx(agent), agent.topic("checklist.focus"))
    assert resolved.summary == "Priorities: Review prototype. In progress: Book dentist. Plus 1 other open task."


# ---------------------------------------------------------------- verifiers
def vctx(agent):
    return VerifyContext(agent=agent, helpers=HelperRunner(agent.helpers, memo_seconds=0), clock=NOON)


async def verify(agent, components, item, expect=Expectations()):
    return await run_verifier(components, vctx(agent), item, expect)


EVENTS = {
    "events": {
        # Stored in UTC like the documented smoke-test event: 19:00Z == 3 PM Toronto (EDT).
        "primary/evtA": {"id": "evtA", "summary": "Project Meeting", "status": "confirmed",
                         "start": {"dateTime": "2026-10-24T19:00:00Z"}, "end": {"dateTime": "2026-10-24T20:00:00Z"}},
        # After DST ends (EST, -05:00): 20:00Z == 3 PM Toronto.
        "primary/evtB": {"id": "evtB", "summary": "Post-DST check", "status": "confirmed",
                         "start": {"dateTime": "2026-11-02T20:00:00Z"}, "end": {"dateTime": "2026-11-02T21:00:00Z"}},
        "primary/evtC": {"id": "evtC", "summary": "Cancelled one", "status": "cancelled",
                         "start": {"dateTime": "2026-10-24T19:00:00Z"}, "end": {"dateTime": "2026-10-24T20:00:00Z"}},
    }
}


async def test_calendar_event_verified_in_toronto_time(agent, components, fixture_dir):
    write_fixture(fixture_dir, "calendar", EVENTS)
    expect = Expectations(title="project meeting", date="2026-10-24", start_time="15:00", end_time="16:00")
    item = await verify(agent, components, {"type": "calendar_event", "action": "created", "calendar_id": "primary", "event_id": "evtA"}, expect)
    assert item.status == "verified", item.mismatches
    assert item.facts["start"] == "15:00" and item.facts["date"] == "2026-10-24"
    assert item.message == "Calendar event 'Project Meeting' tomorrow, Saturday, October 24, 3 to 4 PM"
    dst = await verify(agent, components, {"type": "calendar_event", "action": "created", "calendar_id": "primary", "event_id": "evtB"},
                       Expectations(date="2026-11-02", start_time="15:00", end_time="16:00"))
    assert dst.status == "verified" and dst.facts["start_iso"].endswith("-05:00")


async def test_calendar_mismatch_and_cancelled_are_not_verified(agent, components, fixture_dir):
    write_fixture(fixture_dir, "calendar", EVENTS)
    wrong = await verify(agent, components, {"type": "calendar_event", "action": "created", "calendar_id": "primary", "event_id": "evtA"},
                         Expectations(start_time="19:00"))
    assert wrong.status == "mismatch" and wrong.mismatches == ["start is 15:00, not 19:00"]
    cancelled = await verify(agent, components, {"type": "calendar_event", "action": "updated", "calendar_id": "primary", "event_id": "evtC"})
    assert cancelled.status == "mismatch" and "cancelled" in cancelled.mismatches[0]


async def test_calendar_delete_requires_absence(agent, components, fixture_dir):
    write_fixture(fixture_dir, "calendar", EVENTS)
    gone = await verify(agent, components, {"type": "calendar_event", "action": "deleted", "calendar_id": "primary", "event_id": "missing1"})
    assert gone.status == "verified"
    still = await verify(agent, components, {"type": "calendar_event", "action": "deleted", "calendar_id": "primary", "event_id": "evtA"})
    assert still.status == "mismatch"


async def test_planner_block_and_checklist_verifiers(agent, components):
    block = await verify(agent, components, {"type": "planner_block", "action": "added", "day": "2026-10-23", "block_id": "b1"},
                         Expectations(title="Deep work", start_time="09:00", end_time="11:00"))
    assert block.status == "verified" and block.message.startswith("planner block 'Deep work'")
    removed = await verify(agent, components, {"type": "planner_block", "action": "removed", "day": "2026-10-23", "block_id": "b9"})
    assert removed.status == "verified"
    task = await verify(agent, components, {"type": "checklist_task", "action": "status", "task_id": "t2", "status": "in-progress"})
    assert task.status == "verified"
    not_done = await verify(agent, components, {"type": "checklist_task", "action": "completed", "task_id": "t1"})
    assert not_done.status == "mismatch" and "not marked complete" in not_done.mismatches[0]
    done = await verify(agent, components, {"type": "checklist_task", "action": "completed", "task_id": "t3"})
    assert done.status == "verified"


@pytest.mark.parametrize("bad", ["-rf", "", "a b", "../x", None])
async def test_unsafe_receipt_identifiers_are_unverifiable(agent, components, bad):
    item = await verify(agent, components, {"type": "calendar_event", "action": "created", "calendar_id": "primary", "event_id": bad})
    assert item.status == "unverifiable"


async def test_unknown_receipt_type_is_unverifiable(agent, components):
    item = await verify(agent, components, {"type": "email_sent", "action": "created"})
    assert item.status == "unverifiable"
