"""Team speaking floor: one audible reply at a time, queued replies, notices at pauses."""

import asyncio

import pytest

from clawvoice.team import floor as floor_module
from clawvoice.team.floor import BYTES_PER_SECOND, SpeakingFloor


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


class Room:
    """Collects what the floor plays, flushes and reports."""

    def __init__(self):
        self.clock = Clock()
        self.played = bytearray()
        self.flushes = 0
        self.events: list[tuple[str, str, int]] = []
        self.notices_allowed = True
        self.floor = SpeakingFloor(
            emit=self.emit, flush=self.flush, on_event=self.on_event, clock=self.clock,
            notice_ok=lambda agent: self.notices_allowed,
        )

    async def emit(self, chunk: bytes):
        self.played.extend(chunk)

    async def flush(self):
        self.flushes += 1

    def on_event(self, kind, reply):
        self.events.append((kind, reply.agent, reply.seq))

    async def run(self, seconds: float, step: float = 0.02):
        for _ in range(int(round(seconds / step))):
            self.clock.now += step
            await self.floor.tick()

    def reply(self, agent: str, seconds: float, *, kind: str = "answer", marker: int = 1, done: bool = True):
        reply = self.floor.new_reply(agent, kind)
        reply.add_item(f"{agent}-item-{reply.seq}")
        reply.audio.extend(bytes([marker]) * int(seconds * BYTES_PER_SECOND))
        self.floor.audio_arrived(reply)
        if done:
            self.floor.generation_done(reply)
        return reply

    def runs(self) -> list[int]:
        """The played stream as a list of reply markers, one per run (overlap would interleave them)."""
        markers: list[int] = []
        for byte in self.played:
            if not markers or markers[-1] != byte:
                markers.append(byte)
        return markers


def test_two_ready_replies_play_one_after_the_other_in_ready_order():
    async def scenario():
        room = Room()
        tari = room.reply("tari", 1.0, marker=1)
        room.clock.now += 0.01
        astra = room.reply("astra", 0.5, marker=2)
        await room.run(0.5)
        assert room.floor.current is tari and room.floor.waiting("astra")
        await room.run(2.0)
        assert room.runs() == [1, 2]  # sequential, never interleaved
        assert len(room.played) == int(1.5 * BYTES_PER_SECOND)
        assert tari.outcome == astra.outcome == "played"
        assert [e[0] for e in room.events if e[0] in ("started", "finished")] == ["started", "finished", "started", "finished"]

    asyncio.run(scenario())


def test_playback_is_paced_at_real_time_with_a_small_lead():
    async def scenario():
        room = Room()
        room.reply("tari", 3.0)
        await room.run(1.0)
        sent_seconds = len(room.played) / BYTES_PER_SECOND
        assert 1.0 <= sent_seconds <= 1.0 + floor_module.LEAD_SECONDS + 0.05

    asyncio.run(scenario())


def test_nothing_starts_while_the_user_speaks_or_their_turn_is_being_transcribed():
    async def scenario():
        room = Room()
        await room.floor.user_started()
        room.reply("tari", 0.5)
        await room.run(1.0)
        assert not room.played
        room.floor.user_stopped("turn-1")
        await room.run(1.0)
        assert not room.played  # transcript of turn-1 not in yet (it may be "stop")
        room.floor.transcript_done("turn-1")
        await room.run(0.4)
        assert room.played

    asyncio.run(scenario())


def test_waiting_for_a_lost_transcript_is_bounded():
    async def scenario():
        room = Room()
        room.floor.user_stopped("turn-1")
        room.reply("tari", 0.5)
        await room.run(floor_module.TRANSCRIPT_WAIT + 0.2)
        assert room.played

    asyncio.run(scenario())


def test_barge_in_cuts_the_audible_reply_and_keeps_the_queue():
    async def scenario():
        room = Room()
        tari = room.reply("tari", 3.0, marker=1)
        astra = room.reply("astra", 0.5, marker=2)
        await room.run(1.0)
        cut = await room.floor.user_started()
        assert cut is tari and tari.outcome == "cut" and room.flushes == 1
        heard_seconds = tari.heard / BYTES_PER_SECOND
        assert 0.8 <= heard_seconds <= 1.05  # what was sent minus what was still ahead of the clock
        assert tari.truncations() == [(tari.items[0][0], tari.heard // 48)]
        assert astra.state == "ready"
        room.floor.user_stopped(None)
        await room.run(1.0)
        assert room.floor.current is astra or astra.outcome == "played"
        assert room.runs() == [1, 2]

    asyncio.run(scenario())


def test_notices_wait_for_a_pause_and_answers_go_first():
    async def scenario():
        room = Room()
        notice = room.reply("astra", 0.5, kind="notice", marker=2)
        answer = room.reply("tari", 0.5, marker=1)
        await room.run(0.2)
        # The room was busy a moment ago: the answer may start, the notice may not.
        assert room.floor.current is answer
        await room.run(0.6)
        assert answer.outcome == "played" and notice.state == "ready"
        await room.run(floor_module.NOTICE_GAP - 0.3)
        assert notice.state == "ready"  # no cut-in right after someone spoke
        await room.run(0.5)
        assert room.floor.current is notice or notice.outcome == "played"

    asyncio.run(scenario())


def test_notices_are_held_while_not_allowed():
    async def scenario():
        room = Room()
        room.notices_allowed = False  # e.g. after "stop", or no client listening
        notice = room.reply("tari", 0.3, kind="notice")
        await room.run(3.0)
        assert notice.state == "ready" and not room.played
        room.notices_allowed = True
        await room.run(0.2)
        assert room.played

    asyncio.run(scenario())


def test_drop_removes_unplayed_replies_but_never_the_audible_one():
    async def scenario():
        room = Room()
        playing = room.reply("tari", 2.0)
        queued = room.reply("tari", 1.0)
        pending = room.floor.new_reply("tari", "answer")
        other = room.reply("astra", 1.0)
        await room.run(0.2)
        dropped = room.floor.drop(lambda r: r.agent == "tari", "superseded")
        assert set(dropped) == {queued, pending}
        assert playing is room.floor.current and other.state == "ready"
        assert queued.cancelled and pending.cancelled and queued.outcome == "superseded"
        assert queued.truncations() == [(queued.items[0][0], 0)]  # memory reset to nothing heard

    asyncio.run(scenario())


def test_a_stale_answer_is_dropped_not_played_late():
    async def scenario():
        room = Room()
        answer = room.reply("tari", 0.5)
        for _ in range(int(floor_module.STALE_SECONDS // 10) + 1):  # the user keeps taking turn after turn
            await room.floor.user_started()
            await room.run(10.0, step=0.5)
            room.floor.user_stopped(None)
        await room.run(0.1)
        assert answer.outcome == "stale" and not room.played

    asyncio.run(scenario())


def test_a_reply_waiting_on_its_own_tool_call_yields_the_floor():
    async def scenario():
        room = Room()
        slow = room.reply("tari", 0.3, marker=1, done=False)  # "Let me check..." then a tool call
        await room.run(0.5)
        other = room.reply("astra", 0.3, marker=2)
        await room.run(floor_module.MAX_HOLD_SECONDS + 0.5)
        assert other.outcome == "played" and slow.state == "pending"
        slow.audio.extend(bytes([3]) * int(0.3 * BYTES_PER_SECOND))  # the answer after the tool
        room.floor.audio_arrived(slow)
        room.floor.generation_done(slow)
        await room.run(1.0)
        assert slow.outcome == "played"
        assert room.runs() == [1, 2, 3]

    asyncio.run(scenario())


def test_a_reply_that_never_spoke_closes_silently():
    room = Room()
    silent = room.floor.new_reply("tari", "answer")
    room.floor.generation_done(silent)
    assert silent.outcome == "silent" and silent not in room.floor.replies


@pytest.mark.parametrize("played_seconds", [0.5, 1.5])
def test_heard_text_follows_the_audio_heard(played_seconds):
    room = Room()
    reply = room.reply("tari", 3.0)
    item = reply.items[0][0]
    reply.add_fragment(item, "One two three four.", 1000)
    reply.add_fragment(item, "Five six seven eight.", 3000)
    reply.heard = int(played_seconds * BYTES_PER_SECOND)
    text = reply.heard_text()
    if played_seconds < 1:
        assert text.startswith("One two") and text.endswith("…") and "Five" not in text
    else:
        assert text.startswith("One two three four.") and "Five" in text and "eight" not in text
