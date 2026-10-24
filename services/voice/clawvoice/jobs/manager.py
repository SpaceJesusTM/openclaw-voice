"""Nonblocking OpenClaw jobs for one agent.

``submit`` records and queues a job and returns at once, so the voice model can
acknowledge "on it" while OpenClaw works. One lane per agent session runs jobs
in order (an OpenClaw session processes one turn at a time); the lane lives in
the server process, independent of any voice connection, so a job keeps running
and its result is kept when the user interrupts, disconnects or reconnects.

Safety rules implemented here:

- duplicate tool calls (same call ID, or the same request while it is still
  pending) return the existing job instead of dispatching again;
- dispatch is retried only when the Gateway provably never received it, or
  with the same Gateway idempotency key; a run that may have started is never
  resubmitted;
- interruption of speech never reaches this module; only an explicit
  ``cancel`` stops a run, and a cancelled change is flagged as possibly applied;
- "done" requires the agent's receipt *and* independent read-back; anything
  else is reported as unverified, mismatched, failed or uncertain;
- affected cache topics are invalidated before the completion is announced.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import secrets as pysecrets
import time
import uuid
from datetime import datetime, timedelta
from typing import Any, Callable

from loguru import logger

from clawvoice.agents import AgentConfig
from clawvoice.helpers import HelperRunner
from clawvoice.jobs.models import ACTIVE, Expectations, Job, Verification, VerificationItem, iso, utcnow
from clawvoice.jobs.store import JobStore
from clawvoice.jobs.verifiers import VerifyContext, run_verifier
from clawvoice.openclaw.adapter import DispatchError, OpenClawAgent, SubmittedRun
from clawvoice.openclaw.envelope import build_message
from clawvoice.openclaw.outcomes import RunOutcome
from clawvoice.openclaw.receipts import parse_reply
from clawvoice.timeutil import SYSTEM_CLOCK, Clock

DUPLICATE_WINDOW_SECONDS = 45
MAX_DISPATCH_ATTEMPTS = 3
ANNOUNCE_MAX_AGE_HOURS = 6.0
_VERBS = {
    "created": "created",
    "added": "added",
    "updated": "updated",
    "moved": "moved",
    "resized": "resized",
    "renamed": "renamed",
    "completed": "marked complete",
    "reopened": "reopened",
    "status": "updated",
}

Listener = Callable[[str, Job], None]


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip()


def _title(request: str, kind: str, target: str, expect: Expectations) -> str:
    if expect.title and kind == "change":
        return f"{target.capitalize() if target != 'other' else 'Change'}: {expect.title}"[:80]
    text = " ".join(request.split())
    return text if len(text) <= 70 else text[:69] + "…"


class JobManager:
    def __init__(
        self,
        agent: AgentConfig,
        backend: OpenClawAgent,
        store: JobStore,
        registry,
        helpers: HelperRunner,
        *,
        invalidate: Callable[[list[str], str], Any] | None = None,
        clock: Clock = SYSTEM_CLOCK,
    ):
        self.agent = agent
        self.backend = backend
        self.store = store
        self.registry = registry
        self.vctx = VerifyContext(agent=agent, helpers=helpers, clock=clock)
        self.clock = clock
        self._invalidate = invalidate
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._lane: asyncio.Task | None = None
        self._tasks: set[asyncio.Task] = set()
        self._running: dict[str, SubmittedRun] = {}
        self._current: str | None = None
        self._listeners: list[Listener] = []

    # ---------------------------------------------------------------- events
    def subscribe(self, listener: Listener) -> Callable[[], None]:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener) if listener in self._listeners else None

    def _emit(self, event: str, job: Job) -> None:
        for listener in list(self._listeners):
            try:
                listener(event, job)
            except Exception as error:  # noqa: BLE001 - listeners must not break jobs
                logger.debug(f"job listener failed: {type(error).__name__}")

    def _save(self, job: Job, event: str = "job.updated") -> None:
        self.store.put(job)
        self._emit(event, job)

    # ---------------------------------------------------------------- lifecycle
    async def start(self) -> None:
        await self._recover()
        if self._lane is None:
            self._lane = asyncio.create_task(self._run_lane(), name=f"jobs:{self.agent.id}")

    async def stop(self) -> None:
        tasks = [t for t in [self._lane, *self._tasks] if t]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._lane = None

    def _spawn(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def _recover(self) -> None:
        """Resolve jobs left unfinished by a previous ClawVoice process."""
        for job in self.store.ordered():
            if job.terminal:
                continue
            if job.status in ("accepted", "queued") and job.attempts == 0:
                self._finish(job, "failed", "Not started: ClawVoice restarted before sending it. Ask again if still needed.")
            elif job.status == "verifying" and job.receipt is not None:
                self._spawn(self._verify_and_finish(job, recovered=True))
            elif job.transport == "ws" and job.run_id:
                job.status = "running"
                self._save(job)
                self._spawn(self._recover_run(job))
            else:
                self._finish(
                    job,
                    "uncertain" if job.kind == "change" else "failed",
                    "ClawVoice restarted while this was running, so its outcome is unknown."
                    + (" Check before asking again." if job.kind == "change" else ""),
                    side_effects=job.kind == "change",
                )

    async def _recover_run(self, job: Job) -> None:
        timeout = self.agent.change_timeout_s if job.kind == "change" else self.agent.question_timeout_s
        outcome = await self.backend.recover(run_id=job.run_id or "", session_key=job.session_key, timeout_s=timeout)
        await self._settle(job, outcome)

    # ---------------------------------------------------------------- submit
    def submit(
        self,
        *,
        request: str,
        kind: str,
        target: str = "other",
        expectations: dict[str, Any] | None = None,
        user_words: str = "",
        origin_session: str | None = None,
        tool_call_id: str | None = None,
    ) -> tuple[Job, bool]:
        """Record and queue a job. Returns (job, is_duplicate). Never blocks on OpenClaw."""
        request = " ".join(str(request or "").split())[:1500]
        if not request:
            raise ValueError("A task needs a request")
        if kind not in ("question", "change"):
            raise ValueError("kind must be question or change")
        if target not in self.agent.targets:
            target = "other"
        expect = Expectations.from_args(expectations or {})
        fingerprint = hashlib.sha256(
            json.dumps([self.agent.id, self.agent.owner_scope, kind, target, _normalize(request), expect.present()]).encode()
        ).hexdigest()[:32]
        now = utcnow()
        for job in self.store.ordered():
            same_call = tool_call_id and job.tool_call_id == tool_call_id
            recent = (now - datetime.fromisoformat(job.created_at)).total_seconds()
            pending_same = job.fingerprint == fingerprint and (
                job.status in ACTIVE or (job.kind == "change" and recent <= DUPLICATE_WINDOW_SECONDS)
            )
            if same_call or pending_same:
                job.duplicate_count += 1
                self._save(job)
                return job, True
        job_id = "J" + pysecrets.token_hex(3).upper()
        job = Job(
            id=job_id,
            agent_id=self.agent.id,
            owner_scope=self.agent.owner_scope,
            session_key=self.agent.session_key,
            kind=kind,
            target=target,
            request=request,
            title=_title(request, kind, target, expect),
            expectations=expect.present(),
            user_words=" ".join(str(user_words or "").split())[:600],
            origin_session=origin_session,
            tool_call_id=tool_call_id,
            fingerprint=fingerprint,
            idempotency_key=f"clawvoice-{self.agent.id}-{job_id}-{uuid.uuid4().hex[:12]}",
        )
        job.timings["accepted_mono"] = time.monotonic()
        busy = self._current is not None or not self._queue.empty()
        job.status = "queued" if busy else "accepted"
        self._save(job, "job.accepted")
        self._queue.put_nowait(job.id)
        return job, False

    # ---------------------------------------------------------------- lane
    async def _run_lane(self) -> None:
        while True:
            job_id = await self._queue.get()
            job = self.store.get(job_id)
            if job is None or job.terminal:
                continue
            if job.cancel_requested:
                self._finish(job, "cancelled", "Cancelled before it started; nothing was sent to the agent.")
                continue
            self._current = job.id
            try:
                await self._execute(job)
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001 - one bad job must not stop the lane
                logger.exception(f"job {job.id} crashed: {type(error).__name__}")
                if not job.terminal:
                    self._finish(
                        job,
                        "uncertain" if job.kind == "change" else "failed",
                        f"Internal error while tracking the task ({type(error).__name__}).",
                        side_effects=job.kind == "change",
                    )
            finally:
                self._current = None

    async def _execute(self, job: Job) -> None:
        timeout = self.agent.change_timeout_s if job.kind == "change" else self.agent.question_timeout_s
        message = build_message(self.agent, job, self.clock)
        job.status = "running"
        job.timings["queue_ms"] = round((time.monotonic() - job.timings.get("accepted_mono", time.monotonic())) * 1000)
        self._save(job)
        submitted: SubmittedRun | None = None
        last_error: DispatchError | None = None
        maybe_started = False
        pinned: str | None = None
        for attempt in range(1, MAX_DISPATCH_ATTEMPTS + 1):
            job.attempts = attempt
            self.store.put(job)
            try:
                submitted = await self.backend.submit(
                    message=message,
                    session_key=job.session_key,
                    idempotency_key=job.idempotency_key,
                    timeout_s=timeout,
                    on_tool_event=lambda payload, job=job: self._tool_event(job, payload),
                    require_transport=pinned,
                )
                break
            except DispatchError as error:
                last_error = error
                if error.may_have_started:
                    maybe_started = True
                    if error.transport != "ws":
                        break  # HTTP has no idempotency key: never resubmit.
                    pinned = "ws"  # Resubmit only with the same Gateway idempotency key.
                if job.cancel_requested or attempt == MAX_DISPATCH_ATTEMPTS:
                    break
                await asyncio.sleep(1.5 * attempt)
        if submitted is None:
            started = maybe_started
            self._finish(
                job,
                "uncertain" if started and job.kind == "change" else "failed",
                f"Could not hand the task to {self.agent.display_name}: {last_error}"
                + (" It may have reached the agent; check before retrying." if started else ""),
                side_effects=started and job.kind == "change",
            )
            return
        job.transport = submitted.transport
        job.run_id = submitted.run_id
        job.timings["dispatch_ack_ms"] = submitted.ack_ms
        job.timings["dispatched_mono"] = time.monotonic()
        self._running[job.id] = submitted
        self._save(job, "job.dispatched")
        if job.cancel_requested:
            await self.backend.cancel(submitted)
        try:
            outcome = await self.backend.outcome(submitted, timeout, via_wait=maybe_started)
        finally:
            self._running.pop(job.id, None)
        await self._settle(job, outcome)

    def _tool_event(self, job: Job, payload: dict[str, Any]) -> None:
        """Record bounded tool-lifecycle evidence (names and command heads only)."""
        evidence = job.evidence
        evidence["events"] = int(evidence.get("events", 0)) + 1
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        name = data.get("name") or data.get("tool") or data.get("toolName")
        args = data.get("args") if isinstance(data.get("args"), dict) else {}
        command = args.get("command") if isinstance(args.get("command"), str) else None
        label = str(name)[:40] if name else None
        if label and command:
            label += ": " + " ".join(command.split()[:4])[:80]
        if label and label not in evidence["tools"] and len(evidence["tools"]) < 20:
            evidence["tools"].append(label)

    # ---------------------------------------------------------------- outcomes
    async def _settle(self, job: Job, outcome: RunOutcome) -> None:
        dispatched = job.timings.pop("dispatched_mono", None)
        if dispatched:
            job.timings["run_ms"] = round((time.monotonic() - dispatched) * 1000)
        name = self.agent.display_name
        if outcome.status == "completed":
            parsed = parse_reply(outcome.text)
            job.answer = parsed.speech
            job.receipt = parsed.receipt
            job.receipt_status = parsed.receipt["status"] if parsed.receipt else None
            if parsed.receipt and parsed.receipt["changes"]:
                job.status = "verifying"
                self._save(job)
                await self._verify_and_finish(job)
                return
            if job.kind == "question":
                if parsed.receipt and parsed.receipt["status"] == "needs_clarification":
                    self._finish(job, "completed", f"{name} needs more detail: {parsed.receipt['question'] or job.answer}")
                else:
                    self._finish(job, "completed", job.answer or "The agent finished without an answer.")
                return
            receipt_status = parsed.receipt["status"] if parsed.receipt else None
            if receipt_status == "needs_clarification":
                self._finish(job, "completed", f"{name} needs more detail before changing anything: "
                             f"{parsed.receipt['question'] or job.answer}")
            elif receipt_status == "no_change":
                detail = job.answer or parsed.receipt["summary"]
                self._finish(job, "completed", "No change was made." + (f" {name} said: {detail}" if detail else ""))
            elif receipt_status == "failed":
                self._finish(job, "failed", f"{name} couldn't do it: {job.answer or parsed.receipt['summary']}")
            else:
                why = "did not report what it changed" if parsed.receipt else "did not return a receipt"
                self._finish(
                    job,
                    "uncertain",
                    f"{name} says it finished, but it {why}, so I couldn't verify the change. "
                    + (f"It said: {job.answer}" if job.answer else ""),
                    side_effects=True,
                    invalidate_all=True,
                )
            return
        if outcome.status == "cancelled":
            change = job.kind == "change"
            self._finish(
                job,
                "cancelled",
                f"Cancelled '{job.title}'." + (" The change may already have been made; check before retrying." if change else ""),
                side_effects=change,
                invalidate_all=change,
            )
            return
        if outcome.status == "failed":
            if job.kind == "change":
                # The run errored after it started: a change may or may not have been applied.
                self._finish(
                    job,
                    "uncertain",
                    f"{name}'s task ended with an error ({outcome.error or 'agent error'}) before confirming the "
                    "result. Part of the change may have happened; check before asking again.",
                    side_effects=True,
                    invalidate_all=True,
                )
            else:
                self._finish(job, "failed", f"{name}'s task failed ({outcome.error or 'agent error'}).")
            return
        reason = {
            "timeout": "it ran past its time limit",
            "disconnected": "the connection to the agent dropped",
            "lost": "the agent no longer knows the task",
        }.get(outcome.status, "its outcome is unknown")
        change = job.kind == "change"
        self._finish(
            job,
            "uncertain" if change else "failed",
            f"I don't know whether '{job.title}' finished: {reason}."
            + (" Check before asking again." if change else ""),
            side_effects=change,
            invalidate_all=change,
        )

    async def _verify_and_finish(self, job: Job, recovered: bool = False) -> None:
        started = time.monotonic()
        expect = Expectations(**job.expectations) if job.expectations else Expectations()
        changes = list((job.receipt or {}).get("changes") or [])
        items: list[VerificationItem] = []
        for change in changes:
            items.append(await run_verifier(self.registry, self.vctx, change, expect))
        statuses = {item.status for item in items}
        if statuses == {"verified"}:
            overall = "verified"
        elif "mismatch" in statuses and statuses <= {"verified", "mismatch"}:
            overall = "mismatch"
        else:
            overall = "unverifiable"
        verification = Verification(status=overall, items=items, ms=round((time.monotonic() - started) * 1000))
        job.verification = verification.to_dict()
        job.timings["verify_ms"] = verification.ms
        name = self.agent.display_name
        topics = self._topics_for(changes)
        receipt_status = (job.receipt or {}).get("status")
        if overall == "verified":
            text = "Verified: " + "; ".join(self._sentence(item) for item in items) + "."
            if receipt_status == "needs_clarification":
                text += f" {name} also asks: {(job.receipt or {}).get('question') or job.answer}"
            self._finish(job, "completed", text, topics=topics)
        elif overall == "mismatch":
            problems = "; ".join(p for item in items for p in item.mismatches)
            actual = "; ".join(item.message for item in items if item.message)
            self._finish(
                job,
                "completed",
                f"{name} made a change, but it doesn't match the request: {problems}. What's there now: {actual}.",
                topics=topics,
                side_effects=True,
            )
        else:
            detail = "; ".join(item.message for item in items if item.status != "verified")
            self._finish(
                job,
                "uncertain",
                f"{name} reported a change, but I couldn't verify it ({detail}). Check before asking again.",
                side_effects=True,
                topics=topics,
                invalidate_all=True,
            )

    @staticmethod
    def _sentence(item: VerificationItem) -> str:
        message = item.message.strip()
        # Deletions and helper-written summaries are already complete statements.
        if item.action in ("deleted", "removed", "cancelled") or message.endswith((".", "!", "?")):
            return message.rstrip(".!?")
        verb = _VERBS.get(item.action, "changed")
        return f"{message} was {verb}"

    def _topics_for(self, changes: list[dict[str, Any]]) -> list[str]:
        topics: list[str] = []
        for change in changes:
            rtype = self.agent.receipt_types.get(str(change.get("type")))
            for key in rtype.invalidates if rtype else ():
                if key not in topics:
                    topics.append(key)
        return topics

    def _finish(
        self,
        job: Job,
        status: str,
        outcome_text: str,
        *,
        side_effects: bool = False,
        topics: list[str] | None = None,
        invalidate_all: bool = False,
    ) -> None:
        job.status = status
        job.outcome_text = " ".join(outcome_text.split())[:900]
        job.side_effects_possible = side_effects
        if status in ("failed", "uncertain", "cancelled") and not job.error:
            job.error = job.outcome_text[:300]
        job.timings["finished_at"] = iso(utcnow())
        accepted = job.timings.pop("accepted_mono", None)
        if accepted:
            job.timings["total_ms"] = round((time.monotonic() - accepted) * 1000)
        keys = list(topics or [])
        if invalidate_all:
            for rtype in self.agent.receipt_types.values():
                keys += [key for key in rtype.invalidates if key not in keys]
        if self._invalidate and keys:
            self._invalidate(keys, f"job {job.id} {status}")
        if job.cancel_requested and job.announcement.get("cancel_source") == "voice":
            job.announcement = {**job.announcement, "state": "suppressed"}
        else:
            job.announcement = {**job.announcement, "state": "pending", "attempts": 0}
        job.timings["finished_mono"] = time.monotonic()
        self._save(job, "job.finished")

    # ---------------------------------------------------------------- control
    async def cancel(self, job_id: str, *, source: str = "voice") -> dict[str, Any]:
        """Explicit cancellation (never triggered by speech interruption)."""
        job = self.store.get(job_id)
        if job is None:
            return {"status": "not_found", "message": "No such task."}
        if job.terminal:
            return {"status": job.status, "message": f"That task already ended ({job.status})."}
        job.cancel_requested = True
        job.announcement = {**job.announcement, "cancel_source": source}
        self._save(job)
        submitted = self._running.get(job_id)
        if submitted is None:
            # Queued: the lane will finish it as cancelled without dispatching.
            if self._current != job_id:
                self._finish(job, "cancelled", "Cancelled before it started; nothing was sent to the agent.")
            return {"status": "cancelled", "message": "Cancelled before it started."}
        stopped = await self.backend.cancel(submitted)
        return {
            "status": "cancelling" if stopped else "finishing",
            "message": (
                "Stopping it now. If it was a change, part of it may already have happened."
                if stopped
                else "It was already finishing; I'll report the actual result."
            ),
        }

    def mark_announcement(self, job_id: str, state: str, session_id: str | None = None) -> None:
        job = self.store.get(job_id)
        if job is None:
            return
        attempts = int(job.announcement.get("attempts", 0)) + (1 if state in ("delivering",) else 0)
        job.announcement = {**job.announcement, "state": state, "attempts": attempts, "session": session_id}
        if state == "delivering" and "finished_mono" in job.timings:
            job.timings["announce_delay_ms"] = round((time.monotonic() - job.timings["finished_mono"]) * 1000)
        if state == "delivered":
            job.announcement["delivered_at"] = iso(utcnow())
        self._save(job)

    # ---------------------------------------------------------------- views
    def get(self, job_id: str) -> Job | None:
        return self.store.get(job_id)

    def public(self, limit: int = 20) -> list[dict[str, Any]]:
        now = utcnow()
        return [job.public(now) for job in self.store.ordered()[:limit]]

    def pending_announcements(self, max_age_hours: float = ANNOUNCE_MAX_AGE_HOURS) -> list[Job]:
        """Unheard results to announce on the next connection (older ones stay queryable)."""
        cutoff = utcnow() - timedelta(hours=max_age_hours)
        pending = []
        for job in reversed(self.store.ordered()):
            if not job.terminal or job.announcement.get("state") not in ("pending", "interrupted_unheard"):
                continue
            finished = job.timings.get("finished_at")
            if finished and datetime.fromisoformat(finished) < cutoff:
                continue
            pending.append(job)
        return pending

    @property
    def busy(self) -> bool:
        return self._current is not None
