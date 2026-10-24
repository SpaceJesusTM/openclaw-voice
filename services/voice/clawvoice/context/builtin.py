"""Generic resolvers usable by any agent.

- ``recent_jobs``  bounded recent background-task outcomes (verified flags kept)
- ``command_json`` any configured helper that prints JSON, e.g. a coding agent's
                   ``project status --json``; no code changes needed to add one
"""

from __future__ import annotations

from typing import Any

from clawvoice.agents import TopicSpec
from clawvoice.context.models import Resolved, ResolverContext, ResolverError
from clawvoice.timeutil import resolve_day

MAX_STRING = 300
MAX_LIST = 25


def bounded(value: Any, depth: int = 0) -> Any:
    """Keep structured data compact and speakable (no huge blobs in voice context)."""
    if depth > 6:
        return None
    if isinstance(value, dict):
        return {str(k)[:60]: bounded(v, depth + 1) for k, v in list(value.items())[:40]}
    if isinstance(value, list):
        return [bounded(v, depth + 1) for v in value[:MAX_LIST]]
    if isinstance(value, str):
        text = " ".join(value.split())
        return text if len(text) <= MAX_STRING else text[: MAX_STRING - 1] + "…"
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:MAX_STRING]


def lookup(data: Any, path: str | None) -> Any:
    if not path:
        return None
    current = data
    for part in str(path).split("."):
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            return None
    return current


async def recent_jobs(ctx: ResolverContext, spec: TopicSpec) -> Resolved:
    limit = int(spec.args.get("limit", 5))
    jobs = (ctx.jobs() if ctx.jobs else [])[:limit]
    running = [job for job in jobs if job.get("status") in ("accepted", "queued", "running", "verifying")]
    finished = [job for job in jobs if job not in running]
    parts = []
    if running:
        parts.append(
            "In progress: "
            + "; ".join(f"{job['title']} ({job['status']}, {job.get('age', 'just now')})" for job in running)
            + "."
        )
    if finished:
        parts.append(
            "Recently finished: "
            + "; ".join(f"{job['title']}: {job.get('outcome_text') or job['status']}" for job in finished)
            + "."
        )
    summary = " ".join(parts) or "There are no recent background tasks."
    return Resolved(
        value={"running": running, "finished": finished},
        summary=summary,
        source="ClawVoice job store",
    )


async def command_json(ctx: ResolverContext, spec: TopicSpec) -> Resolved:
    helper = spec.args.get("helper")
    argv = spec.args.get("argv") or []
    if not helper or not isinstance(argv, list):
        raise ResolverError(f"Topic {spec.key} needs args.helper and args.argv")
    day = None
    if spec.args.get("day") is not None:
        day = resolve_day(str(spec.args["day"]), ctx.tz, ctx.clock).isoformat()
        argv = [str(part).replace("{day}", day) for part in argv]
    result = await ctx.helpers.run_json(str(helper), [str(part) for part in argv])
    data = result.data
    keep = spec.args.get("value_fields")
    value = {field: lookup(data, field) for field in keep} if keep else data
    summary = lookup(data, spec.args.get("summary_field")) or spec.args.get("default_summary") or "Loaded."
    complete_field = spec.args.get("complete_field")
    complete = bool(lookup(data, complete_field)) if complete_field else True
    warnings = lookup(data, spec.args.get("warnings_field")) or []
    if not isinstance(value, dict):
        value = {"value": value}
    return Resolved(
        value=bounded(value),
        summary=bounded(str(summary)),
        complete=complete,
        warnings=[bounded(str(w)) for w in warnings][:5] if isinstance(warnings, list) else [],
        day=day,
        source=f"helper {helper}",
    )


def register(registry) -> None:
    registry.resolvers["recent_jobs"] = recent_jobs
    registry.resolvers["command_json"] = command_json
