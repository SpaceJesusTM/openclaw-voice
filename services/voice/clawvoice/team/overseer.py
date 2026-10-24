"""Silent, raw-audio Higgs routing. One bounded decision at a time; no agent memory."""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from clawvoice.team.realtime import function_output, text_item

if TYPE_CHECKING:
    from clawvoice.agents import AgentConfig
    from clawvoice.team.room import TeamRoom

DECISION_SECONDS = 8.0  # includes one upstream_error retry; never an unlimited floor hold
RESPONSE_SECONDS = 3.5
QUIET_SECONDS = 0.3  # drain unsolicited tool-output continuations before another request


def router_mode() -> str:
    mode = os.environ.get("CLAWVOICE_TEAM_ROUTER_MODE", "overseer").strip().lower()
    if mode not in ("overseer", "names"):
        raise ValueError("CLAWVOICE_TEAM_ROUTER_MODE must be overseer or names")
    return mode


def instructions(agents: list[AgentConfig], active: str) -> str:
    roster = "\n".join(
        f"- {a.id} ({a.display_name}; aliases: {', '.join(a.aliases)}): {a.public_identity()['role']}"
        for a in agents
    )
    return (
        "You are the silent routing overseer of a voice room. Never answer the user. "
        "Judge only the latest user turn (audio or typed text), never earlier requests or tool outputs. "
        "Use call_agent to switch agents when explicitly addressed or clearly needed by specialty. "
        "Otherwise produce no tool call: keep the current agent. Follow-ups, thanks, small talk and "
        "uncertainty stay with the current agent. Never call the already active agent. "
        "Do not perform tasks, speak, explain, or invent an agent reply.\n"
        f"Current active agent: {active}.\nConfigured agents:\n{roster}"
    )


def tools(agents: list[str]) -> list[dict[str, Any]]:
    return [{
        "type": "function", "name": "call_agent",
        "description": (
            "Select another agent for the latest user turn. Call for an explicit address or a clear "
            "change of specialty. Do not call for acknowledgements, continuations, ambiguity, or when "
            "the current agent is appropriate. The reason must be a brief routing explanation."
        ),
        "parameters": {
            "type": "object", "properties": {
                "agent": {"type": "string", "enum": agents}, "reason": {"type": "string"},
            }, "required": ["agent", "reason"], "additionalProperties": False,
        },
    }]


@dataclass
class Turn:
    seq: int
    item: str = ""
    text: str = ""
    typed: bool = False
    started: float = 0
    ended: float = 0
    submitted: bool = False
    settled: bool = False

    @property
    def hold(self) -> str:
        return f"route:{self.seq}"


@dataclass
class Response:
    turn: Turn
    id: str = ""
    status: str = ""
    code: str = ""
    agent: str | None = None
    reason: str = ""
    invalid: bool = False
    done: asyncio.Event = field(default_factory=asyncio.Event)


class Overseer:
    def __init__(self, room: TeamRoom):
        self.room = room
        self.seq = 0
        self.latest: Turn | None = None
        self.pending: Turn | None = None
        self.response: Response | None = None
        self.worker: asyncio.Task | None = None
        self.broken = False
        self.live: set[str] = set()
        self.retired: set[str] = set()
        self.acked: set[tuple[str, str]] = set()
        self.last_event = 0.0

    def connected(self) -> None:
        self.broken = False
        self.live.clear()
        self.retired.clear()
        self.acked.clear()

    def _new(self, **kwargs) -> Turn:
        if self.latest is not None and not self.latest.settled:
            self._release(self.latest)
            if self.latest.text:
                self.room.ui("team.user", {"text": self.latest.text, "agent": None,
                                          "status": "superseded", "typed": self.latest.typed})
        self.seq += 1
        turn = Turn(self.seq, **kwargs)
        self.latest = turn
        self.pending = None
        return turn

    def speech_started(self, item: str) -> None:
        self._new(item=item, started=time.monotonic())

    def speech_stopped(self, item: str) -> None:
        turn = self.latest
        if turn is not None and not turn.typed and turn.item == item:
            turn.ended = time.monotonic()
            self.tick()

    def tick(self) -> None:
        turn = self.latest
        if turn is None or turn.settled or not turn.ended:
            return
        now = time.monotonic()
        if now - turn.ended >= DECISION_SECONDS:
            # The response worker also has a deadline. Missing ASR must be bounded too.
            self._fail(turn, "Routing timed out. Please repeat, or address an agent by name.")
        else:
            self.room.floor.awaiting[turn.hold] = now

    def _release(self, turn: Turn) -> None:
        turn.settled = True
        self.room.floor.transcript_done(turn.hold)

    def _fresh(self, turn: Turn) -> bool:
        return turn is self.latest and not turn.settled

    def _fail(self, turn: Turn, message: str) -> None:
        if not self._fresh(turn):
            return
        self._release(turn)
        self.room.ui("team.user", {"text": turn.text, "agent": None, "status": "routing_failed", "typed": turn.typed})
        self.room.ui("team.status", {"text": message})
        self.room.trace.record("team.overseer.failed", turn=turn.seq, message=message)
        self.room.changed()

    def stop(self) -> None:
        if self.latest is not None:
            self._release(self.latest)
        self.pending = None

    def disconnected(self) -> None:
        self.broken = True
        if self.response is not None:
            self.response.status = "disconnected"
            self.response.done.set()
        if self.latest is not None and not self.latest.submitted:
            self._fail(self.latest, "The routing connection was lost. Reconnecting; please repeat afterwards.")

    def submit(self, text: str, *, started: float, ended: float, typed: bool) -> None:
        if typed and self.room.floor.user_speaking:
            self.room.ui("team.status", {"text": "Finish speaking before sending a typed Team message."})
            return
        turn = self._new(typed=True, started=started, ended=ended) if typed else self.latest
        if turn is None:  # transcript without a VAD boundary: do not guess which audio it belongs to
            self.room.ui("team.status", {"text": "Missed the speech boundary. Please repeat."})
            return
        if turn.submitted or turn.settled:
            return  # cumulative/repeated ASR for the same VAD turn
        turn.text, turn.started, turn.submitted = text, started, True
        turn.ended = turn.ended or ended
        self.pending = turn
        self.tick()
        if self.worker is None or self.worker.done():
            self.worker = self.room._spawn(self._work())

    async def _idle(self, deadline: float) -> bool:
        # Tool outputs can produce unsolicited responses. Drain them, never adopt them as a turn.
        while self.live or time.monotonic() - self.last_event < QUIET_SECONDS:
            if time.monotonic() >= deadline:
                return False
            await asyncio.sleep(0.02)
        return True

    async def _work(self) -> None:
        while self.pending is not None and not self.room.closed:
            turn, self.pending = self.pending, None
            if not self._fresh(turn):
                continue
            deadline = turn.ended + DECISION_SECONDS
            session = self.room.listener
            failed = self.broken or session is None or session.closed
            choice, reason = None, ""
            try:
                if not failed and not await self._idle(deadline):
                    failed = True
                    if self._fresh(turn):
                        await self.room._routed_utterance(turn, choice=None, reason="", failed=True)
                        self._release(turn)
                    await self._discard_session()
                if not failed and self._fresh(turn):
                    agents = [v.agent for v in self.room.voices.values()]
                    await session.send({"type": "session.update", "session": {
                        "instructions": instructions(agents, self.room.addressed),
                    }})
                    if turn.typed:
                        await session.send(text_item(turn.text))
                    for attempt in range(2):
                        if not self._fresh(turn):
                            break
                        response = Response(turn)
                        self.response = response
                        if not await session.send({"type": "response.create"}):
                            response.status = "disconnected"
                        else:
                            await asyncio.wait_for(response.done.wait(), min(RESPONSE_SECONDS, max(0.01, deadline - time.monotonic())))
                        self.retired.add(response.id)
                        if response.status == "failed" and response.code == "upstream_error" and attempt == 0:
                            self.room.trace.record("team.overseer.retry", turn=turn.seq, response=response.id)
                            continue
                        failed = response.status != "completed" or response.invalid
                        if not failed:
                            choice, reason = response.agent, response.reason
                        break
                if self._fresh(turn):
                    await self.room._routed_utterance(turn, choice=choice, reason=reason, failed=failed)
                    self._release(turn)
            except (TimeoutError, OSError):
                # Text-only early cancellation can omit response.done indefinitely. Replace the
                # socket instead of letting late response.created attach to the next turn.
                if self._fresh(turn):
                    await self.room._routed_utterance(turn, choice=None, reason="", failed=True)
                    self._release(turn)
                await self._discard_session()
            finally:
                self.response = None
                self.room.changed()

    async def _discard_session(self) -> None:
        self.broken = True
        session = self.room.listener
        if session is not None:
            # close() is intentional and suppresses on_close; ask the room to reconnect explicitly.
            await session.close()
            await self.room._listener_closed(session)

    async def event(self, event: dict[str, Any]) -> None:
        kind = event.get("type", "")
        if not (kind.startswith("response.") or kind == "error"):
            return
        self.last_event = time.monotonic()
        response = self.response
        rid = str(event.get("response_id") or (event.get("response") or {}).get("id") or "")
        if kind == "response.created":
            self.live.add(rid)
            if response is not None and not response.id and rid and rid not in self.retired:
                response.id = rid
            return
        if kind == "error":
            error = event.get("error") or {}
            if response is not None and not response.done.is_set():
                response.status, response.code = "failed", str(error.get("code") or error.get("type") or "")
                response.done.set()
            return
        matching = response is not None and bool(rid) and rid == response.id
        if kind == "response.function_call_arguments.done":
            await self._call(event, response if matching else None, rid)
        elif kind == "response.done":
            data = event.get("response") or {}
            # Some servers include a call only in the completed output. Same dedup path.
            for item in data.get("output") or []:
                if item.get("type") == "function_call":
                    await self._call(item, response if matching else None, rid)
            self.live.discard(rid)
            self.retired.add(rid)
            if len(self.retired) > 500:
                self.retired = {rid}  # completed IDs are only a recent replay guard
            if matching:
                response.status = str(data.get("status") or "completed")
                response.code = str(((data.get("status_details") or {}).get("error") or {}).get("code") or "")
                response.done.set()
        # Ordinary overseer text is deliberately ignored, including final text.

    async def _call(self, event: dict[str, Any], response: Response | None, rid: str) -> None:
        call_id = str(event.get("call_id") or "")
        if not call_id or (rid, call_id) in self.acked:
            return
        self.acked.add((rid, call_id))
        if len(self.acked) > 500:
            self.acked = {(rid, call_id)}
        result: dict[str, Any] = {"ok": False, "error": "stale_or_unsolicited_call"}
        if response is not None and self._fresh(response.turn) and not response.done.is_set():
            try:
                args = json.loads(str(event.get("arguments") or "")[:4096])
                if event.get("name") != "call_agent" or not isinstance(args, dict):
                    raise ValueError("invalid_tool")
                agent, reason = args.get("agent"), args.get("reason")
                if not isinstance(agent, str) or agent not in self.room.voices or not isinstance(reason, str) or not reason.strip():
                    raise ValueError("invalid_arguments")
                explicit = {r.agent for r in self.room.book.parse(response.turn.text) if r.agent is not None}
                if explicit and (len(explicit) != 1 or agent not in explicit):
                    raise ValueError("explicit_address_takes_priority")
                if response.agent is not None and response.agent != agent:
                    raise ValueError("conflicting_calls")
                response.agent, response.reason = agent, " ".join(reason.split())[:160]
                result = {"ok": True, "selected": agent, "pending_response_completion": True}
            except (ValueError, TypeError) as error:
                response.invalid = True
                result = {"ok": False, "error": str(error) if isinstance(error, ValueError) else "invalid_arguments"}
        self.room.trace.record("team.overseer.call", response=rid, call_id=call_id,
                               turn=response.turn.seq if response else None, result=result)
        if self.room.listener is not None:
            await self.room.listener.send(function_output(call_id, result))
