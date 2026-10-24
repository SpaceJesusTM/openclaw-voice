"""Hot context for day-planner agents without a Calendar (``day-planner`` local-only mode).

Reads the same ``show DAY --json`` view as :mod:`clawvoice.plugins.day_planner`, but
speaks only planner blocks, commutes and the checklist, and never mentions a
Calendar the agent does not have. Verifiers and the checklist resolver are reused
from ``day_planner``; list both plugins in the agent file.
"""

from __future__ import annotations

from clawvoice.agents import TopicSpec
from clawvoice.context.models import Resolved, ResolverContext
from clawvoice.plugins.day_planner import _checklist_view, _day_parts, _join, _plural, _show_day, _warnings
from clawvoice.timeutil import day_label, resolve_day


async def local_planner_day(ctx: ResolverContext, spec: TopicSpec) -> Resolved:
    day = resolve_day(str(spec.args.get("day", "today")), ctx.tz, ctx.clock)
    data = await _show_day(ctx, day, spec.args.get("helper", "planner"))
    groups = _day_parts(data)
    blocks, commutes = groups["planner_blocks"], groups["commutes"]
    checklist = _checklist_view(data["checklist"])
    label = day_label(day, ctx.tz, ctx.clock)
    heading = label[0].upper() + label[1:]
    sentences = []
    if not (blocks or commutes):
        sentences.append(f"{heading}: no planner blocks or commutes.")
    else:
        sentences.append(f"{heading}:")
        if blocks:
            sentences.append(
                f"{_plural(len(blocks), 'planner block')}: "
                + _join([f"{e['title']}, {e['when']}" for e in blocks])
                + "."
            )
        if commutes:
            sentences.append("Commutes: " + _join([f"{e['title']}, {e['when']}" for e in commutes]) + ".")
    if checklist["open_count"]:
        bits = []
        if checklist["priority"]:
            bits.append(f"{len(checklist['priority'])} priority")
        if checklist["in_progress"]:
            bits.append(f"{len(checklist['in_progress'])} in progress")
        count = checklist["open_count"]
        sentences.append(
            f"Checklist: {count} open task{'s' if count != 1 else ''}" + (f" ({', '.join(bits)})." if bits else ".")
        )
    return Resolved(
        value={"date": day.isoformat(), "day": label, "planner_blocks": blocks, "commutes": commutes,
               "checklist": checklist},
        summary=" ".join(sentences),
        complete=data.get("complete") is True,
        warnings=_warnings(data),
        day=day.isoformat(),
        source="day-planner show --json",
    )


def register(registry) -> None:
    registry.resolvers["local_planner_day"] = local_planner_day
