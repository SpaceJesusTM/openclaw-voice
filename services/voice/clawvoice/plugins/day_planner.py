"""Hot-context resolvers and change verifiers for OpenClaw day-planner agents.

For any agent whose workspace provides the ported Tari Public helpers:

- ``bin/day-planner show DAY --json`` and ``checklist --json``: the planner's own
  view, which already merges live Calendar commitments (with its inclusion
  rules, completeness flags and DST handling) with local blocks and commutes;
- ``bin/tari-google calendar get CALENDAR EVENT [--allow-absent]``.

The helper commands themselves come from agent configuration (``helpers.planner``
and ``helpers.google``). Nothing here recomputes Calendar inclusion, free time
or planner rules: the helpers stay authoritative and this module only shapes
their JSON into compact, speakable views and read-back checks.

Titles from Calendar and the planner are untrusted user data. They are
whitespace-normalized and length-bounded, and the voice instructions treat
tool data as data, never as instructions.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any

from clawvoice.agents import ReceiptType, TopicSpec
from clawvoice.context.models import Resolved, ResolverContext, ResolverError
from clawvoice.jobs.models import Expectations, VerificationItem
from clawvoice.jobs.verifiers import VerifyContext, compare, safe_arg
from clawvoice.timeutil import (
    day_label,
    hhmm,
    local_now,
    minutes_span,
    parse_instant,
    resolve_day,
    spoken_clock,
    spoken_span,
)

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_STATUS_LABEL = {"Priority": "priority", "In-progress": "in progress", "Start": "to start", None: "open"}
SUMMARY_ITEMS = 10


def clean_title(value: Any) -> str:
    text = " ".join(_CONTROL.sub(" ", str(value or "")).split())
    return (text[:117] + "…") if len(text) > 120 else (text or "(untitled)")


def _join(parts: list[str]) -> str:
    if len(parts) <= SUMMARY_ITEMS:
        return "; ".join(parts)
    return "; ".join(parts[:SUMMARY_ITEMS]) + f"; and {len(parts) - SUMMARY_ITEMS} more"


def _plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


# ---------------------------------------------------------------- helper reads
async def _show_day(ctx: ResolverContext, day: date, helper: str = "planner", fresh: bool = False) -> dict:
    result = await ctx.helpers.run_json(helper, ["show", day.isoformat(), "--json"], fresh=fresh)
    data = result.data
    if not isinstance(data, dict) or data.get("date") != day.isoformat():
        raise ResolverError("The day planner returned an unexpected view")
    if not isinstance(data.get("schedule"), list) or not isinstance(data.get("checklist"), list):
        raise ResolverError("The day planner returned an incomplete view")
    for item in data["schedule"]:
        if not isinstance(item, dict) or item.get("kind") not in ("calendar", "planner", "commute"):
            raise ResolverError("The day planner returned a malformed schedule item")
    return data


def _warnings(data: dict) -> list[str]:
    messages = []
    for detail in data.get("warning_details") or []:
        code = detail.get("code") if isinstance(detail, dict) else None
        if code == "CALENDAR_UNAVAILABLE":
            messages.append(
                "Google Calendar could not be read completely, so Calendar events may be missing; "
                "free time is not verified."
            )
        elif code in ("PLANNER_SCHEDULE_MALFORMED", "PLANNER_CHECKLIST_MALFORMED"):
            messages.append("Some saved planner entries could not be read.")
        elif code == "CALENDAR_NOT_CHECKED":
            messages.append("Calendar was not checked.")
    if not messages and data.get("complete") is not True:
        messages.append("The planner reported that this view is incomplete.")
    return messages


def _item_view(item: dict) -> dict[str, Any]:
    if item.get("allday"):
        when = "all day"
    elif item.get("time_note"):
        when = str(item["time_note"])[:80]
    else:
        when = minutes_span(int(item.get("start", 0)), int(item.get("end", 0)))
    view = {"title": clean_title(item.get("title")), "when": when}
    if not item.get("allday"):
        view["start"] = hhmm(int(item.get("start", 0)))
        view["end"] = hhmm(int(item.get("end", 0)))
    if item.get("free"):
        view["shows_as_free"] = True
    if item.get("counted") is False:
        view["context_only"] = True
        view["when"] += " (Calendar context, not a commitment)"
    return view


def _checklist_view(checklist: list) -> dict[str, Any]:
    open_tasks = [
        {"text": clean_title(task.get("text")), "status": task.get("status"), "section": clean_title(section.get("section"))}
        for section in checklist
        if isinstance(section, dict)
        for task in section.get("tasks") or []
        if isinstance(task, dict) and not task.get("done")
    ]
    priority = [t for t in open_tasks if t["status"] == "Priority"]
    in_progress = [t for t in open_tasks if t["status"] == "In-progress"]
    return {
        "open_count": len(open_tasks),
        "priority": [t["text"] for t in priority][:15],
        "in_progress": [t["text"] for t in in_progress][:15],
        "other_open_count": len(open_tasks) - len(priority) - len(in_progress),
    }


def _day_parts(data: dict) -> dict[str, list[dict]]:
    groups = {"calendar_events": [], "all_day_events": [], "planner_blocks": [], "commutes": []}
    for item in data["schedule"]:
        if item["kind"] == "calendar":
            groups["all_day_events" if item.get("allday") else "calendar_events"].append(_item_view(item))
        elif item["kind"] == "planner":
            groups["planner_blocks"].append(_item_view(item))
        else:
            groups["commutes"].append(_item_view(item))
    return groups


# ---------------------------------------------------------------- resolvers
async def planner_day(ctx: ResolverContext, spec: TopicSpec) -> Resolved:
    day = resolve_day(str(spec.args.get("day", "today")), ctx.tz, ctx.clock)
    data = await _show_day(ctx, day, spec.args.get("helper", "planner"))
    groups = _day_parts(data)
    checklist = _checklist_view(data["checklist"])
    label = day_label(day, ctx.tz, ctx.clock)
    sentences = []
    if not any(groups.values()):
        sentences.append(f"{label[0].upper() + label[1:]}: nothing is scheduled — no Calendar events, planner blocks or commutes.")
    else:
        sentences.append(f"{label[0].upper() + label[1:]}:")
        if groups["calendar_events"]:
            sentences.append(
                f"{_plural(len(groups['calendar_events']), 'Calendar event')}: "
                + _join([f"{e['title']}, {e['when']}" for e in groups["calendar_events"]])
                + "."
            )
        if groups["all_day_events"]:
            sentences.append("All day: " + _join([e["title"] for e in groups["all_day_events"]]) + ".")
        if groups["planner_blocks"]:
            sentences.append(
                f"{_plural(len(groups['planner_blocks']), 'planner block')}: "
                + _join([f"{e['title']}, {e['when']}" for e in groups["planner_blocks"]])
                + "."
            )
        if groups["commutes"]:
            sentences.append("Commutes: " + _join([f"{e['title']}, {e['when']}" for e in groups["commutes"]]) + ".")
    if checklist["open_count"]:
        bits = []
        if checklist["priority"]:
            bits.append(f"{len(checklist['priority'])} priority")
        if checklist["in_progress"]:
            bits.append(f"{len(checklist['in_progress'])} in progress")
        sentences.append(f"Checklist: {checklist['open_count']} open task{'s' if checklist['open_count'] != 1 else ''}" + (f" ({', '.join(bits)})." if bits else "."))
    warnings = _warnings(data)
    if warnings and data.get("calendar_complete") is False:
        sentences.insert(0, "Calendar could not be fully read, so only local planner entries are certain.")
    return Resolved(
        value={"date": day.isoformat(), "day": label, **groups, "checklist": checklist},
        summary=" ".join(sentences),
        complete=data.get("complete") is True,
        warnings=warnings,
        day=day.isoformat(),
        source="day-planner show --json",
    )


async def calendar_day(ctx: ResolverContext, spec: TopicSpec) -> Resolved:
    day = resolve_day(str(spec.args.get("day", "today")), ctx.tz, ctx.clock)
    data = await _show_day(ctx, day, spec.args.get("helper", "planner"))
    groups = _day_parts(data)
    label = day_label(day, ctx.tz, ctx.clock)
    events = groups["calendar_events"]
    all_day = groups["all_day_events"]
    calendar_ok = data.get("calendar_complete") is True
    if not calendar_ok:
        summary = f"Google Calendar could not be read completely for {label}, so I can't confirm its events."
    elif not events and not all_day:
        summary = f"No Calendar events {label}."
    else:
        parts = [f"{e['title']}, {e['when']}" for e in events] + [f"{e['title']} (all day)" for e in all_day]
        summary = f"Calendar {label}: " + _join(parts) + "."
    return Resolved(
        value={"date": day.isoformat(), "day": label, "events": events, "all_day_events": all_day},
        summary=summary,
        complete=calendar_ok,
        warnings=[] if calendar_ok else _warnings(data),
        day=day.isoformat(),
        source="day-planner show --json (Calendar entries)",
    )


async def calendar_next(ctx: ResolverContext, spec: TopicSpec) -> Resolved:
    horizon = max(1, min(14, int(spec.args.get("horizon_days", 3))))
    now = local_now(ctx.tz, ctx.clock)
    today = now.date()
    complete = True
    warnings: list[str] = []
    seen: set[str] = set()
    upcoming: list[tuple[datetime, datetime, dict]] = []
    for offset in range(horizon):
        day = today + timedelta(days=offset)
        data = await _show_day(ctx, day, spec.args.get("helper", "planner"))
        if data.get("calendar_complete") is not True:
            complete = False
            warnings.append(f"Calendar could not be fully read for {day_label(day, ctx.tz, ctx.clock)}.")
        for item in data["schedule"]:
            if item["kind"] != "calendar" or item.get("counted") is False or item.get("allday") or not item.get("start_at") or not item.get("end_at"):
                continue
            key = f"{item.get('id')}|{item.get('start_at')}"
            if key in seen:
                continue
            seen.add(key)
            try:
                start = parse_instant(item["start_at"]).astimezone(ctx.tz)
                end = parse_instant(item["end_at"]).astimezone(ctx.tz)
            except (TypeError, ValueError):
                continue
            if end > now:
                upcoming.append((start, end, item))
        if upcoming and offset >= 1:
            break
    upcoming.sort(key=lambda entry: entry[0])
    last_day = today + timedelta(days=horizon - 1)
    if not upcoming:
        summary = f"No Calendar events from now through {day_label(last_day, ctx.tz, ctx.clock)}."
        if not complete:
            summary = "I couldn't fully read the Calendar, so I can't confirm your next event. " + summary
        value = {"next": None, "horizon_end": last_day.isoformat()}
    else:
        def describe(start: datetime, end: datetime, item: dict) -> dict[str, Any]:
            return {
                "title": clean_title(item.get("title")),
                "day": day_label(start.date(), ctx.tz, ctx.clock),
                "date": start.date().isoformat(),
                "when": spoken_span(start, end) if start.date() == end.date() else f"{spoken_clock(start)} until {day_label(end.date(), ctx.tz, ctx.clock)} {spoken_clock(end)}",
                "start": start.strftime("%H:%M"),
                "end": end.strftime("%H:%M"),
                "in_progress": start <= now < end,
            }

        entries = [describe(*entry) for entry in upcoming[:3]]
        first = entries[0]
        if first["in_progress"]:
            summary = f"You're in {first['title']} right now, until {spoken_clock(upcoming[0][1])}."
            if len(entries) > 1:
                summary += f" Next: {entries[1]['title']}, {entries[1]['day']}, {entries[1]['when']}."
        else:
            summary = f"Your next Calendar event is {first['title']}, {first['day']}, {first['when']}."
        if not complete:
            summary += " (Calendar could not be fully read, so an earlier event could be missing.)"
        value = {"next": first, "following": entries[1:], "horizon_end": last_day.isoformat()}
    return Resolved(
        value=value,
        summary=summary,
        complete=complete,
        warnings=warnings,
        day=today.isoformat(),
        source="day-planner show --json (Calendar entries, multiple days)",
    )


async def checklist_focus(ctx: ResolverContext, spec: TopicSpec) -> Resolved:
    result = await ctx.helpers.run_json(spec.args.get("helper", "planner"), ["checklist", "--json"])
    data = result.data
    if not isinstance(data, list):
        raise ResolverError("The checklist returned an unexpected view")
    view = _checklist_view(data)
    if not view["open_count"]:
        summary = "Your checklist has no open tasks."
    else:
        parts = []
        if view["priority"]:
            parts.append("Priorities: " + _join(view["priority"]) + ".")
        if view["in_progress"]:
            parts.append("In progress: " + _join(view["in_progress"]) + ".")
        if not parts:
            parts.append("No priority or in-progress tasks.")
        if view["other_open_count"]:
            parts.append(f"Plus {_plural(view['other_open_count'], 'other open task')}.")
        summary = " ".join(parts)
    return Resolved(value=view, summary=summary, source="day-planner checklist --json")


# ---------------------------------------------------------------- verifiers
def _local_facts(event: dict, ctx_tz) -> dict[str, Any]:
    start, end = event.get("start") or {}, event.get("end") or {}
    facts: dict[str, Any] = {"title": clean_title(event.get("summary")), "status": event.get("status")}
    if start.get("dateTime") and end.get("dateTime"):
        a = parse_instant(start["dateTime"]).astimezone(ctx_tz)
        b = parse_instant(end["dateTime"]).astimezone(ctx_tz)
        facts.update(
            date=a.date().isoformat(),
            start=a.strftime("%H:%M"),
            end=b.strftime("%H:%M") if b.date() == a.date() else None,
            start_iso=a.isoformat(),
            end_iso=b.isoformat(),
            all_day=False,
        )
    elif start.get("date"):
        facts.update(date=start["date"], all_day=True)
    return facts


def _describe_event(facts: dict, ctx) -> str:
    if not facts.get("date"):
        return f"Calendar event '{facts.get('title')}'"
    day = date.fromisoformat(facts["date"])
    label = day_label(day, ctx.agent.tz, ctx.clock)
    if facts.get("all_day"):
        return f"Calendar event '{facts['title']}' {label} (all day)"
    when = spoken_span(datetime.fromisoformat(facts["start_iso"]), datetime.fromisoformat(facts["end_iso"]))
    return f"Calendar event '{facts['title']}' {label}, {when}"


async def verify_calendar_event(
    vctx: VerifyContext, item: dict, rtype: ReceiptType, expect: Expectations
) -> VerificationItem:
    action = str(item.get("action") or "")
    calendar_id = safe_arg(item.get("calendar_id"), "calendar_id")
    event_id = safe_arg(item.get("event_id"), "event_id")
    helper = rtype.args.get("helper", "google")
    if action in ("deleted", "removed", "cancelled"):
        result = await vctx.helpers.run_json(
            helper, ["calendar", "get", calendar_id, event_id, "--allow-absent"], fresh=True
        )
        event = (result.data or {}).get("event") if isinstance(result.data, dict) else None
        absent = isinstance(event, dict) and (event.get("absent") is True or event.get("status") == "cancelled")
        return VerificationItem(
            type=rtype.name,
            action=action,
            status="verified" if absent else "mismatch",
            facts={"absent": absent},
            mismatches=[] if absent else ["the event still exists"],
            message="the event is gone from the Calendar" if absent else "the event is still on the Calendar",
        )
    result = await vctx.helpers.run_json(helper, ["calendar", "get", calendar_id, event_id], fresh=True)
    event = (result.data or {}).get("event") if isinstance(result.data, dict) else None
    if not isinstance(event, dict) or event.get("id") != event_id:
        return VerificationItem(type=rtype.name, action=action, status="error", message="Calendar returned no event")
    facts = _local_facts(event, vctx.agent.tz)
    mismatches = compare(expect, facts)
    if facts.get("status") == "cancelled":
        mismatches.insert(0, "the event is cancelled")
    facts["calendar"] = "primary" if calendar_id == "primary" else "a shared calendar"
    return VerificationItem(
        type=rtype.name,
        action=action,
        status="mismatch" if mismatches else "verified",
        facts=facts,
        mismatches=mismatches,
        message=_describe_event(facts, vctx),
    )


async def verify_planner_block(
    vctx: VerifyContext, item: dict, rtype: ReceiptType, expect: Expectations
) -> VerificationItem:
    action = str(item.get("action") or "")
    day_text = safe_arg(item.get("day"), "day")
    block_id = safe_arg(item.get("block_id"), "block_id")
    try:
        day = date.fromisoformat(day_text)
    except ValueError:
        return VerificationItem(type=rtype.name, action=action, status="unverifiable", message="receipt day is not a date")
    ctx = ResolverContext(agent=vctx.agent, helpers=vctx.helpers, clock=vctx.clock)
    data = await _show_day(ctx, day, rtype.args.get("helper", "planner"), fresh=True)
    block = next(
        (b for b in data["schedule"] if b.get("kind") in ("planner", "commute") and b.get("id") == block_id), None
    )
    label = day_label(day, vctx.agent.tz, vctx.clock)
    if action in ("removed", "deleted"):
        return VerificationItem(
            type=rtype.name,
            action=action,
            status="verified" if block is None else "mismatch",
            facts={"absent": block is None},
            mismatches=[] if block is None else ["the block is still in the planner"],
            message=f"the planner block is removed from {label}" if block is None else "the block is still there",
        )
    if block is None:
        return VerificationItem(
            type=rtype.name, action=action, status="mismatch", mismatches=["the block was not found"],
            message=f"no such planner block on {label}",
        )
    facts = {
        "title": clean_title(block.get("title")),
        "date": day.isoformat(),
        "start": hhmm(int(block["start"])),
        "end": hhmm(int(block["end"])),
        "kind": block.get("kind"),
    }
    mismatches = compare(expect, facts)
    return VerificationItem(
        type=rtype.name,
        action=action,
        status="mismatch" if mismatches else "verified",
        facts=facts,
        mismatches=mismatches,
        message=f"planner block '{facts['title']}' {label}, {minutes_span(int(block['start']), int(block['end']))}",
    )


async def verify_checklist_task(
    vctx: VerifyContext, item: dict, rtype: ReceiptType, expect: Expectations
) -> VerificationItem:
    action = str(item.get("action") or "")
    task_id = safe_arg(item.get("task_id"), "task_id")
    result = await vctx.helpers.run_json(rtype.args.get("helper", "planner"), ["checklist", "--json", "--all"], fresh=True)
    if not isinstance(result.data, list):
        return VerificationItem(type=rtype.name, action=action, status="error", message="checklist unreadable")
    found = None
    for section in result.data:
        for task in (section.get("tasks") or []) if isinstance(section, dict) else []:
            if isinstance(task, dict) and task.get("id") == task_id:
                found = (section.get("section"), task)
    if action in ("removed", "deleted"):
        return VerificationItem(
            type=rtype.name,
            action=action,
            status="verified" if found is None else "mismatch",
            facts={"absent": found is None},
            mismatches=[] if found is None else ["the task is still on the checklist"],
            message="the checklist task is removed" if found is None else "the task is still there",
        )
    if found is None:
        return VerificationItem(
            type=rtype.name, action=action, status="mismatch", mismatches=["the task was not found"],
            message="no such checklist task",
        )
    section, task = found
    facts = {
        "title": clean_title(task.get("text")),
        "section": clean_title(section),
        "status": task.get("status"),
        "done": bool(task.get("done")),
    }
    mismatches = compare(Expectations(title=expect.title), facts)
    if action == "completed" and not facts["done"]:
        mismatches.append("the task is not marked complete")
    if action == "reopened" and facts["done"]:
        mismatches.append("the task is still marked complete")
    wanted = str(item.get("status") or "").strip().lower()
    if action == "status" and wanted:
        actual = (facts["status"] or "none").lower()
        if wanted.replace(" ", "-") != actual:
            mismatches.append(f"status is {facts['status'] or 'none'}, not {wanted}")
    state = "done" if facts["done"] else _STATUS_LABEL.get(facts["status"], "open")
    return VerificationItem(
        type=rtype.name,
        action=action,
        status="mismatch" if mismatches else "verified",
        facts=facts,
        mismatches=mismatches,
        message=f"checklist task '{facts['title']}' in {facts['section']} ({state})",
    )


def register(registry) -> None:
    registry.resolvers["planner_day"] = planner_day
    registry.resolvers["calendar_day"] = calendar_day
    registry.resolvers["calendar_next"] = calendar_next
    registry.resolvers["checklist_focus"] = checklist_focus
    registry.verifiers["calendar_event"] = verify_calendar_event
    registry.verifiers["planner_block"] = verify_planner_block
    registry.verifiers["checklist_task"] = verify_checklist_task
