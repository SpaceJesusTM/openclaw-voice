"""The Team room's speaking floor: many replies can be ready, one is audible.

Several agents generate replies at the same time, each in its own Higgs
session. Their audio is buffered here and played to the one browser
connection one reply at a time. Playback is paced at real time from the
server (a small lead ahead of the clock), so the room always knows how much of
a reply was actually heard, as in the agent-selector experiment.

Rules
- One reply is audible at a time. It keeps the floor until its audio has
  played out and its response chain is done (speech, tool call, answer).
- Ready replies wait in one room-level queue: first ready, first played
  (ties by creation order). The next starts by itself when the floor frees.
- User speech comes first: nothing starts while the user is speaking, or while
  a user turn that just ended is still being transcribed (it may be "stop", or
  a request), and talking over a reply cuts it. Queued replies keep their place.
- Addressing an agent hands it the floor (the foreground agent): its replies go
  before older queued speech, and other agents' older speech waits (at most
  FOREGROUND_WAIT) while it prepares its reply.
- Background-task notices also need a natural pause (NOTICE_GAP of quiet) and
  never cut anyone off.
- An answer left waiting longer than STALE_SECONDS is dropped, not played late.

Generating a reply and owning the floor are separate: an agent keeps working
(and other agents keep generating) whoever is speaking.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from loguru import logger

BYTES_PER_SECOND = 24000 * 2  # PCM16 mono at 24 kHz, Higgs's output format
BYTES_PER_MS = BYTES_PER_SECOND // 1000
CHUNK_BYTES = 1920  # 40 ms, Pipecat's output chunk size
LEAD_SECONDS = 0.16  # audio sent ahead of the clock to absorb timer jitter
POST_SPEECH_GAP = 0.25  # after the user stops, before a reply starts
TRANSCRIPT_WAIT = 2.5  # longest wait for a finished user turn's transcript
NOTICE_GAP = 1.2  # quiet needed before a background-task notice
STALE_SECONDS = 60.0  # an answer not played by then is dropped
MAX_HOLD_SECONDS = 6.0  # a reply waiting on its own tool call yields the floor after this
MIN_HEARD_BYTES = int(0.3 * BYTES_PER_SECOND)  # a notice heard this far counts as delivered
FOREGROUND_WAIT = 8.0  # longest that older speech waits for the newly addressed agent's reply


@dataclass(eq=False)
class Reply:
    """One agent's spoken reply: an answer, a greeting, or a background-task notice."""

    seq: int
    agent: str
    kind: str  # answer | greeting | notice
    created_at: float
    job_ids: tuple[str, ...] = ()
    audio: bytearray = field(default_factory=bytearray)
    items: list[list[Any]] = field(default_factory=list)  # [item_id, first audio byte]
    fragments: list[tuple[str, int]] = field(default_factory=list)  # (text, end byte)
    texts: dict[str, str] = field(default_factory=dict)  # item_id -> transcript
    state: str = "pending"  # pending (no unplayed audio) -> ready -> playing -> done
    outcome: str = ""  # played | cut | dismissed | superseded | handed_over | stale | silent
    generating: bool = True
    cancelled: bool = False  # stop its response chain (dismissed or superseded)
    played: int = 0
    heard: int = 0
    ready_at: float | None = None
    started_at: float | None = None
    truncated: bool = False
    notice_items: list[dict[str, Any]] = field(default_factory=list)

    # ------------------------------------------------------------ text
    def add_item(self, item_id: str) -> None:
        if item_id and not any(entry[0] == item_id for entry in self.items):
            self.items.append([item_id, len(self.audio)])

    def _item_start(self, item_id: str) -> int:
        return next((start for known, start in self.items if known == item_id), len(self.audio))

    def add_fragment(self, item_id: str, text: str, length_ms: int) -> None:
        if text:
            self.fragments.append((text, self._item_start(item_id) + int(length_ms) * BYTES_PER_MS))

    def add_text(self, item_id: str, text: str, *, final: bool) -> None:
        if not item_id or not text:
            return
        self.texts[item_id] = text if final else self.texts.get(item_id, "") + text

    @property
    def text(self) -> str:
        parts = [self.texts[item_id] for item_id, _ in self.items if self.texts.get(item_id)]
        if not parts:
            parts = [text for text, _ in self.fragments]
        return " ".join(" ".join(parts).split())

    def heard_text(self) -> str:
        """The words heard before a cut: whole fragments, plus a share of the one playing."""
        if self.heard >= len(self.audio) and not self.generating:
            return self.text
        out: list[str] = []
        previous = 0
        for text, end in sorted(self.fragments, key=lambda f: f[1]):
            if end <= self.heard:
                out.append(text)
            else:
                share = (self.heard - previous) / max(1, end - previous)
                words = text.split()
                count = int(len(words) * share)
                if count > 0:
                    out.append(" ".join(words[:count]) + "…")
                break
            previous = end
        return " ".join(" ".join(out).split())

    def truncations(self) -> list[tuple[str, int]]:
        """(item_id, audio_end_ms) for each spoken item not heard in full."""
        result = []
        for index, (item_id, start) in enumerate(self.items):
            end = self.items[index + 1][1] if index + 1 < len(self.items) else len(self.audio)
            if self.heard < end:
                result.append((item_id, max(0, self.heard - start) // BYTES_PER_MS))
        return result

    def public(self) -> dict[str, Any]:
        return {"reply": self.seq, "agent": self.agent, "reply_kind": self.kind, "state": self.state, "outcome": self.outcome}


Emit = Callable[[bytes], Awaitable[None]]
Flush = Callable[[], Awaitable[None]]
OnEvent = Callable[[str, Reply], None]


class SpeakingFloor:
    def __init__(
        self,
        *,
        emit: Emit,
        flush: Flush,
        on_event: OnEvent,
        clock: Callable[[], float] = time.monotonic,
        notice_ok: Callable[[str], bool] | None = None,
        answer_due: Callable[[str], bool] | None = None,
    ):
        self._emit = emit
        self._flush = flush
        self._on_event = on_event
        self.clock = clock
        self.notice_ok = notice_ok or (lambda agent: True)
        self.answer_due = answer_due or (lambda agent: False)  # agent still owes a spoken answer
        self.foreground: str | None = None  # the agent the user addressed last
        self.foreground_at = float("-inf")
        self.replies: list[Reply] = []
        self.current: Reply | None = None
        self.user_speaking = False
        self.user_started_at = 0.0
        self.user_stopped_at = float("-inf")
        self.awaiting: dict[str, float] = {}  # finished user turns waiting for their transcript
        self.last_activity = clock()
        self._deadline = 0.0  # when the audio sent so far finishes playing
        self._dry_since: float | None = None
        self._seq = 0

    def _event(self, kind: str, reply: Reply) -> None:
        try:
            self._on_event(kind, reply)
        except Exception as error:  # noqa: BLE001 - a failing listener must not stall the floor
            logger.warning(f"Team floor: {kind} handler failed: {type(error).__name__}: {error}")

    # ------------------------------------------------------------ replies
    def new_reply(self, agent: str, kind: str, job_ids: tuple[str, ...] = ()) -> Reply:
        self._seq += 1
        reply = Reply(seq=self._seq, agent=agent, kind=kind, created_at=self.clock(), job_ids=tuple(job_ids))
        self.replies.append(reply)
        return reply

    def background_reply(self, agent: str, kind: str, job_ids: tuple[str, ...] = ()) -> Reply:
        """A reply that is never played: its agent handed the floor over, but its tool chain runs on."""
        self._seq += 1
        return Reply(seq=self._seq, agent=agent, kind=kind, created_at=self.clock(), job_ids=tuple(job_ids),
                     state="done", outcome="handed_over")

    def hand_over(self, agent: str) -> None:
        self.foreground = agent
        self.foreground_at = self.clock()

    def audio_arrived(self, reply: Reply) -> None:
        if reply.state == "pending" and reply.played < len(reply.audio):
            reply.state = "ready"
            reply.ready_at = self.clock()
            self._event("ready", reply)

    def generation_done(self, reply: Reply) -> None:
        reply.generating = False
        if reply.state == "pending" and reply.played >= len(reply.audio):
            # Nothing (more) to say. A reply that never spoke at all is "silent".
            self._close(reply, "silent" if reply.played == 0 else "played", "finished")

    def ready(self) -> list[Reply]:
        """Ready replies in play order: the foreground agent's answers first, then first ready."""
        def order(r: Reply) -> tuple:
            return (not (r.agent == self.foreground and r.kind != "notice"), r.ready_at or 0.0, r.seq)
        return sorted((r for r in self.replies if r.state == "ready"), key=order)

    def waiting(self, agent: str) -> bool:
        return any(r.agent == agent and r.state == "ready" for r in self.replies)

    def thinking(self, agent: str) -> bool:
        return any(r.agent == agent and r.state == "pending" and r.generating for r in self.replies)

    def drop(self, predicate: Callable[[Reply], bool], outcome: str, *, cancel: bool = True) -> list[Reply]:
        """Drop replies that have not started playing and match ``predicate``.

        ``cancel`` also ends their response chains; without it the chain (its
        tool calls) runs on and nothing more of it is played.
        """
        dropped = [r for r in self.replies if r is not self.current and r.state in ("pending", "ready") and predicate(r)]
        for reply in dropped:
            reply.cancelled = reply.cancelled or cancel
            self._close(reply, outcome, "dropped")
        return dropped

    def _close(self, reply: Reply, outcome: str, event: str) -> None:
        reply.state = "done"
        reply.outcome = outcome
        if reply is self.current:
            self.current = None
        if reply in self.replies:
            self.replies.remove(reply)
        self._event(event, reply)

    # ------------------------------------------------------------ the user
    async def user_started(self) -> Reply | None:
        now = self.clock()
        self.user_speaking = True
        self.user_started_at = now
        self.last_activity = now
        return await self.cut()

    def user_stopped(self, turn_id: str | None = None) -> None:
        now = self.clock()
        self.user_speaking = False
        self.user_stopped_at = now
        self.last_activity = now
        if turn_id:
            self.awaiting[turn_id] = now

    def transcript_done(self, turn_id: str | None) -> None:
        if turn_id:
            self.awaiting.pop(turn_id, None)

    def heard_now(self, reply: Reply) -> int:
        """Bytes of the audible reply heard so far: sent, minus what is still ahead of the clock."""
        if reply is not self.current:
            return reply.heard
        ahead = max(0.0, self._deadline - self.clock())
        return max(0, reply.played - int(ahead * BYTES_PER_SECOND))

    async def cut(self) -> Reply | None:
        """Stop the audible reply now (barge-in or stop). Returns it, with ``heard`` set.

        Only the audio stops: an agent whose tool chain is still running answers
        afterwards in a new reply, as single-agent mode does. "Stop" and
        superseding requests cancel the chain separately (``cancelled``).
        """
        reply = self.current
        if reply is None:
            return None
        now = self.clock()
        reply.heard = self.heard_now(reply)
        reply.heard -= reply.heard % 2
        self.current = None
        self.last_activity = now
        await self._flush()  # drop what the transport has queued but not played
        self._close(reply, "cut", "cut")
        return reply

    # ------------------------------------------------------------ clock
    async def tick(self) -> None:
        now = self.clock()
        await self._play(now)
        if self.current is None:
            self._expire(now)
            if self._start_next(now):
                await self._play(now)

    def _can_start(self, now: float) -> bool:
        if self.user_speaking:
            # A lost "speech stopped" must not block the floor forever.
            if now - self.user_started_at < 45:
                return False
            self.user_speaking = False
        if now - self.user_stopped_at < POST_SPEECH_GAP:
            return False
        for turn_id, ended in list(self.awaiting.items()):
            if now - ended >= TRANSCRIPT_WAIT:
                del self.awaiting[turn_id]
        return not self.awaiting

    def _start_next(self, now: float) -> bool:
        if not self._can_start(now):
            return False
        for reply in self.ready():
            if reply.kind == "notice" and (now - self.last_activity < NOTICE_GAP or not self.notice_ok(reply.agent)):
                continue
            if self._held(reply, now):
                continue
            reply.state = "playing"
            reply.started_at = reply.started_at or now
            self.current = reply
            self._deadline = now
            self._dry_since = None
            self._event("started", reply)
            return True
        return False

    def _held(self, reply: Reply, now: float) -> bool:
        """Another agent's speech waits while the newly addressed agent prepares its reply (bounded)."""
        agent = self.foreground
        if agent is None or reply.agent == agent or now - self.foreground_at >= FOREGROUND_WAIT:
            return False
        return self.thinking(agent) or self.answer_due(agent)

    def _expire(self, now: float) -> None:
        self.drop(lambda r: r.kind != "notice" and r.state == "ready" and now - (r.ready_at or now) > STALE_SECONDS, "stale")

    async def _play(self, now: float) -> None:
        reply = self.current
        if reply is None:
            return
        if self._deadline < now:
            self._deadline = now  # starting, or the buffer ran dry: restart the clock
        while reply.played < len(reply.audio) and self._deadline - now < LEAD_SECONDS:
            size = min(CHUNK_BYTES, len(reply.audio) - reply.played)
            size -= size % 2
            if size <= 0:
                break
            chunk = bytes(reply.audio[reply.played : reply.played + size])
            reply.played += size
            self._deadline += size / BYTES_PER_SECOND
            await self._emit(chunk)
            if self.current is not reply:
                return  # cut while sending
        if reply.played < len(reply.audio):
            self._dry_since = None
            return
        if not reply.generating:
            if now >= self._deadline:
                reply.heard = reply.played
                self.last_activity = now
                self._close(reply, "played", "finished")
            return
        # Played everything so far, but its chain continues (a tool call, then the answer).
        if self._dry_since is None:
            self._dry_since = max(now, self._deadline)
        elif now - self._dry_since > MAX_HOLD_SECONDS:
            # Do not hold the room for a slow tool: yield, and rejoin the queue when more audio arrives.
            reply.state = "pending"
            self.current = None
            self.last_activity = now
            self._event("released", reply)

    def snapshot(self) -> dict[str, Any]:
        return {
            "speaking": self.current.agent if self.current else None,
            "speaking_reply": self.current.seq if self.current else None,
            "queue": [{"agent": r.agent, "kind": r.kind, "reply": r.seq} for r in self.ready()],
            "user_speaking": self.user_speaking,
        }
