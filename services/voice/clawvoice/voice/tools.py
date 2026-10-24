"""Realtime tools offered to Higgs, generated from the agent's configuration.

- ``get_context``   fast hot-context read; never runs an OpenClaw model turn
- ``delegate_task`` records a background OpenClaw job and returns at once
- ``check_tasks``   tracked job status (no OpenClaw call)
- ``cancel_task``   explicit cancellation, separate from speech interruption

Handlers are registered with ``cancel_on_interruption=False`` (as in the
validated experiment): barge-in stops speech, never a tool or a job.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from pipecat.adapters.schemas.direct_function import tool_options
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.frames.frames import FunctionCallResultProperties
from pipecat.services.llm_service import FunctionCallParams

from clawvoice.timeutil import local_now, spoken_clock

if TYPE_CHECKING:
    from clawvoice.runtime import AgentRuntime
    from clawvoice.voice.session import VoiceSession


def _now_label(agent) -> str:
    now = local_now(agent.tz)
    return f"{now.strftime('%A, %B')} {now.day}, {spoken_clock(now)}"


class VoiceTools:
    def __init__(self, runtime: AgentRuntime, session: VoiceSession):
        self.runtime = runtime
        self.session = session
        self.agent = runtime.agent

    # ------------------------------------------------------------ schemas
    def schemas(self) -> list[FunctionSchema]:
        topics = self.runtime.agent.topics
        topic_help = "; ".join(f"{t.key} = {t.description} (covers {t.coverage})" for t in topics)
        targets = list(self.agent.targets)
        return [
            FunctionSchema(
                name="get_context",
                description=(
                    f"Instant cached snapshot of part of {self.agent.display_name}'s context; a shortcut, not the "
                    f"limit of what can be checked. Topics: {topic_help}. Call it for every question inside a "
                    f"topic's coverage, including repeated ones (the data changes; never reuse an earlier answer), "
                    f"and answer from the result. For anything outside that coverage, or when the user asks for a "
                    f"live check, use delegate_task (kind question) instead. Questions only: it never does "
                    f"anything, so a request to do something goes to delegate_task."
                ),
                properties={"topic": {"type": "string", "enum": [t.key for t in topics], "description": "Topic key."}},
                required=["topic"],
                handler=self._wrap(self.get_context),
            ),
            FunctionSchema(
                name="delegate_task",
                description=(
                    f"Start a background task for {self.agent.display_name}'s OpenClaw agent: anything the user asks "
                    f"to be done or changed ({', '.join(t for t in targets if t != 'other')} or other: create, "
                    "update, delete, run, mark), or a read-only question needing its memory, reasoning or tools, "
                    "including any lookup beyond get_context's coverage (other dates or periods, more detail, a "
                    "live check). Returns "
                    "immediately with status 'accepted' (NOT done). Call once per request, and only after the "
                    "details of a change are unambiguous."
                ),
                properties={
                    "request": {
                        "type": "string",
                        "description": "The full request in plain words, with resolved specifics (what, which day, times, title).",
                    },
                    "kind": {
                        "type": "string",
                        "enum": ["change", "question"],
                        "description": "change = does or changes something (create, update, delete, run); question = only needs an answer.",
                    },
                    "target": {"type": "string", "enum": targets, "description": "What the change is about."},
                    "title": {
                        "type": "string",
                        "description": (
                            "Expected name AFTER the change (use the NEW title for a rename); for deletion, the current name. The event, block or "
                            "task itself, as the user said it. Never a section, list or calendar name. Omit if none."
                        ),
                    },
                    "date": {"type": "string", "description": "Local date YYYY-MM-DD, if any."},
                    "start_time": {"type": "string", "description": "Local start time HH:MM (24h), if any."},
                    "end_time": {"type": "string", "description": "Local end time HH:MM (24h), if any."},
                },
                required=["request", "kind", "target"],
                handler=self._wrap(self.delegate_task),
            ),
            FunctionSchema(
                name="check_tasks",
                description="Status and results of background tasks (running and recent). No new work is started.",
                properties={"job_id": {"type": "string", "description": "Optional task id from an earlier result."}},
                required=[],
                handler=self._wrap(self.check_tasks),
            ),
            FunctionSchema(
                name="cancel_task",
                description=(
                    "Cancel a running background task. Only when the user explicitly asks to cancel or stop a task; "
                    "never because the user interrupted your speech."
                ),
                properties={"job_id": {"type": "string", "description": "Task id from an earlier result."}},
                required=["job_id"],
                handler=self._wrap(self.cancel_task),
            ),
        ]

    def _wrap(self, method):
        @tool_options(cancel_on_interruption=False)
        async def handler(params: FunctionCallParams) -> None:
            started = time.perf_counter()
            name = params.function_name
            arguments = dict(params.arguments or {})
            self.session.trace.record("tool.started", call_id=params.tool_call_id, name=name, arguments=arguments)
            self.session.ui("tool", {"phase": "started", "name": name, "call_id": params.tool_call_id, "arguments": arguments})
            try:
                result = await method(params, arguments)
            except Exception as error:  # noqa: BLE001 - tool failures become honest results
                result = {"status": "error", "error": type(error).__name__, "note": "Tell the user this failed."}
            ms = round((time.perf_counter() - started) * 1000, 1)
            meta = {k: result.get(k) for k in ("status", "cache", "age_seconds", "job_id", "topic", "read_ms") if k in result}
            self.session.trace.record("tool.result", call_id=params.tool_call_id, name=name, result=result, ms=ms)
            self.session.ui("tool", {"phase": "result", "name": name, "call_id": params.tool_call_id, "ms": ms, **meta})
            await params.result_callback(result, properties=FunctionCallResultProperties(run_llm=True))

        handler.__name__ = method.__name__
        return handler

    # ------------------------------------------------------------ handlers
    async def get_context(self, params: FunctionCallParams, arguments: dict[str, Any]) -> dict[str, Any]:
        topic = str(arguments.get("topic") or "")
        reading = await self.runtime.cache.get(topic)
        return reading

    async def delegate_task(self, params: FunctionCallParams, arguments: dict[str, Any]) -> dict[str, Any]:
        try:
            job, duplicate = self.runtime.jobs.submit(
                request=str(arguments.get("request") or ""),
                kind=str(arguments.get("kind") or "question"),
                target=str(arguments.get("target") or "other"),
                expectations={k: arguments.get(k) for k in ("title", "date", "start_time", "end_time")},
                user_words=" ".join(self.session.recent_user_words()),
                origin_session=self.session.id,
                tool_call_id=params.tool_call_id,
            )
        except ValueError as error:
            return {"status": "rejected", "reason": str(error), "note": "Ask the user for the missing detail."}
        base = {
            "job_id": job.id,
            "agent": self.agent.display_name,
            "task": job.title,
            "now_local": _now_label(self.agent),
        }
        if duplicate and not job.terminal:
            return {
                **base,
                "status": "already_in_progress",
                "note": "This exact task is already running. Don't start it again; say it's in progress.",
            }
        if duplicate:
            return {
                **base,
                "status": "already_finished",
                "result": job.outcome_text,
                "note": "This exact task just finished; report its result instead of repeating it.",
            }
        return {
            **base,
            "status": "accepted",
            "queued_behind_other_tasks": job.status == "queued",
            "note": (
                "Accepted, NOT done. Give a very short acknowledgement only. A [ClawVoice notice] will arrive "
                "with the verified result. Keep talking with the user meanwhile."
            ),
        }

    async def check_tasks(self, params: FunctionCallParams, arguments: dict[str, Any]) -> dict[str, Any]:
        job_id = str(arguments.get("job_id") or "").strip().upper()
        jobs = self.runtime.jobs
        if job_id:
            job = jobs.get(job_id)
            if job is None:
                return {"status": "not_found", "note": "No such task id. Call check_tasks without an id to list tasks."}
            return {"status": "ok", "task": _brief(job.public()), "now_local": _now_label(self.agent)}
        tasks = [_brief(item) for item in jobs.public(limit=6)]
        return {
            "status": "ok",
            "tasks": tasks,
            "note": "accepted/queued/running/verifying mean NOT finished yet. Report results truthfully.",
            "now_local": _now_label(self.agent),
        }

    async def cancel_task(self, params: FunctionCallParams, arguments: dict[str, Any]) -> dict[str, Any]:
        job_id = str(arguments.get("job_id") or "").strip().upper()
        if not job_id:
            return {"status": "rejected", "note": "Which task? Use check_tasks to find it."}
        result = await self.runtime.jobs.cancel(job_id, source="voice")
        return {**result, "job_id": job_id}


def _brief(job: dict[str, Any]) -> dict[str, Any]:
    if job["kind"] == "question":
        verification = "not needed (answer only, nothing was changed)"
    else:
        verification = {
            "verified": "verified: the change was read back from the source",
            "mismatch": "checked: the actual state differs from the request",
            "unverifiable": "could not be verified",
        }.get(job["verification"] or "", "not verified yet" if job["status"] not in ("failed", "cancelled") else "none")
    return {
        "job_id": job["job_id"],
        "task": job["title"],
        "kind": job["kind"],
        "status": job["status"],
        "verification": verification,
        "result": job["outcome_text"] or None,
        "started": job["age"],
        "change_may_have_happened": job["side_effects_possible"] or None,
    }
