"""Team room routing: named requests reach only the named agent; nothing else is inferred."""

import asyncio
import time

from clawvoice.team.addressing import AddressBook
from clawvoice.team.floor import BYTES_PER_SECOND, FOREGROUND_WAIT, SpeakingFloor
from clawvoice.team.room import BACKGROUND_NOTICE_GAP, TeamRoom
from clawvoice.team.voice import AgentVoice
from clawvoice.voice.telemetry import Trace


class FakeVoice:
    yield_floor = AgentVoice.yield_floor  # the real handover, on the real floor
    answer_pending = AgentVoice.answer_pending

    def __init__(self, room, agent_id):
        self.room = room
        self.id = agent_id
        self.asked: list[str] = []
        self.silenced = False
        self.stopped = 0
        self.spoke = False
        self.backgrounded = False
        self._queued_requests = 0
        self._answering = False

    def ask(self, text):
        self.asked.append(text)
        self.spoke = self.backgrounded = False
        self._queued_requests += 1  # answered when a test says so

    def on_stop(self):
        self.stopped += 1
        self.silenced = True


def make_room():
    room = object.__new__(TeamRoom)  # routing only: no Higgs sessions, no pipeline
    room.trace = Trace(label="test", persist=False)
    room.book = AddressBook({"tari": ["tari", "tarry", "atari"], "astra": ["astra", "astro", "ashra"]})
    room.voices = {"tari": FakeVoice(room, "tari"), "astra": FakeVoice(room, "astra")}
    room.messages = []
    room.ui = lambda kind, data: room.messages.append((kind, data))
    room.changed = lambda: None
    room.addressed = None
    room._follow_up = None
    room._last_named = None
    room._turns = {}
    room._item_text = {}
    room._last_transcript = ""
    room._deferred = None
    room._dispatched = []
    room.last_client_audio = time.monotonic()

    async def emit(_chunk):
        return None

    async def flush():
        return None

    room.floor = SpeakingFloor(emit=emit, flush=flush, on_event=lambda *_: None, answer_due=room._answer_due)
    return room


def ready_reply(room, agent, kind="answer"):
    reply = room.floor.new_reply(agent, kind)
    reply.audio.extend(bytes(BYTES_PER_SECOND))
    room.floor.audio_arrived(reply)
    return reply


def say(room, text, *, started=None, ended=None, typed=False):
    now = time.monotonic()
    asyncio.run(room.utterance(text, started=started or now, ended=ended or now, typed=typed))


def test_named_requests_reach_only_the_named_agent():
    room = make_room()
    say(room, "Hey Tari, what's on my calendar tomorrow?")
    say(room, "Okay Astra, what coding projects are you working on?", started=time.monotonic() + 5)
    assert room.voices["tari"].asked == ["Hey Tari, what's on my calendar tomorrow?"]
    assert room.voices["astra"].asked == ["Okay Astra, what coding projects are you working on?"]


def test_an_unnamed_request_reaches_nobody():
    room = make_room()
    say(room, "Build me a website.", typed=True)
    assert not room.voices["tari"].asked and not room.voices["astra"].asked
    assert room.messages[-1][1]["status"] == "unaddressed"


def test_a_bare_name_makes_the_next_utterance_a_follow_up():
    room = make_room()
    say(room, "Hey Astra.")
    assert not room.voices["astra"].asked
    say(room, "Change the main heading to My Personal Portfolio.", started=time.monotonic() + 4)
    assert room.voices["astra"].asked == ["Change the main heading to My Personal Portfolio."]
    say(room, "And what about tomorrow?", started=time.monotonic() + 30)  # the follow-up was used up
    assert len(room.voices["astra"].asked) == 1


def test_a_fragment_right_after_a_named_request_continues_it():
    room = make_room()
    ended = time.monotonic()
    say(room, "Tari, what's on my calendar", ended=ended)
    say(room, "tomorrow afternoon?", started=ended + 0.9)
    assert room.voices["tari"].asked == ["Tari, what's on my calendar", "tomorrow afternoon?"]
    say(room, "and the weather?", started=ended + 20)  # long after: not a continuation
    assert len(room.voices["tari"].asked) == 2


def test_stop_ends_speech_and_queued_replies_but_asks_nobody():
    async def scenario():
        room = make_room()
        playing = room.floor.new_reply("tari", "answer")
        playing.audio.extend(bytes(BYTES_PER_SECOND))
        room.floor.audio_arrived(playing)
        queued = room.floor.new_reply("astra", "answer")
        queued.audio.extend(bytes(BYTES_PER_SECOND))
        room.floor.audio_arrived(queued)
        await room.floor.tick()
        assert room.floor.current is playing
        now = time.monotonic()
        await room.utterance("Stop.", started=now, ended=now)
        assert playing.outcome == "cut" and playing.cancelled
        assert queued.outcome == "dismissed"
        assert all(v.stopped == 1 and not v.asked for v in room.voices.values())

    asyncio.run(scenario())


def test_a_named_stop_leaves_the_other_agent_alone():
    async def scenario():
        room = make_room()
        queued = room.floor.new_reply("astra", "answer")
        queued.audio.extend(bytes(BYTES_PER_SECOND))
        room.floor.audio_arrived(queued)
        now = time.monotonic()
        await room.utterance("Tari, stop talking.", started=now, ended=now)
        assert room.voices["tari"].stopped == 1 and room.voices["astra"].stopped == 0
        assert queued.state == "ready"

    asyncio.run(scenario())


def test_an_accumulated_transcript_yields_only_the_new_words():
    room = make_room()
    assert room._new_words("item-1", "Hey Astra, what's the latest website update?") == "Hey Astra, what's the latest website update?"
    # The listener reused the item, so its transcript now repeats the earlier turn.
    assert room._new_words("item-1", "Hey Astra, what's the latest website update?Stop.") == "Stop."
    # The same words said again in a new item are a new request.
    assert room._new_words("item-2", "Hey Astra, what's the latest website update?") == "Hey Astra, what's the latest website update?"


def test_addressing_another_agent_hands_over_the_floor():
    async def scenario():
        room = make_room()
        speaking = ready_reply(room, "astra")  # Astra's acknowledgement
        await room.floor.tick()
        room.voices["astra"].spoke = True
        rest = ready_reply(room, "astra")  # the rest of its chain, ready before Tari has anything
        now = time.monotonic()
        await room.utterance("Hey Tari, what's on my calendar?", started=now, ended=now, typed=True)
        assert speaking.outcome == "cut" and not speaking.cancelled  # its tool chain is not cancelled
        assert rest.outcome == "handed_over" and not rest.cancelled  # dropped, but its work goes on
        assert room.voices["astra"].backgrounded and room.voices["tari"].asked
        assert room.addressed == "tari" and room.floor.foreground == "tari"
        answer = ready_reply(room, "tari")
        await room.floor.tick()
        assert room.floor.current is answer

    asyncio.run(scenario())


def test_an_agent_that_has_not_spoken_answers_after_the_newly_addressed_one():
    async def scenario():
        room = make_room()
        now = time.monotonic()
        await room.utterance("Tari, what's on my calendar?", started=now, ended=now, typed=True)
        older = ready_reply(room, "tari")
        await room.utterance("Astra, what are you working on?", started=now, ended=now, typed=True)
        assert older.state == "ready" and not room.voices["tari"].backgrounded
        await room.floor.tick()
        assert room.floor.current is None  # Tari's older answer waits for Astra's
        newer = ready_reply(room, "astra")
        await room.floor.tick()
        assert room.floor.current is newer
        assert [r.agent for r in room.floor.ready()] == ["tari"]

    asyncio.run(scenario())


def test_older_speech_waits_for_the_new_agent_only_so_long():
    async def scenario():
        room = make_room()
        older = ready_reply(room, "tari")
        now = time.monotonic()
        await room.utterance("Astra, what are you working on?", started=now, ended=now, typed=True)
        room.floor.foreground_at -= FOREGROUND_WAIT
        await room.floor.tick()
        assert room.floor.current is older

    asyncio.run(scenario())


def test_another_agents_notice_waits_for_a_natural_break_and_survives_stop():
    async def scenario():
        room = make_room()
        now = time.monotonic()
        await room.utterance("Tari, what's on my calendar?", started=now, ended=now, typed=True)
        room.voices["tari"]._queued_requests = 0  # Tari has answered
        notice = ready_reply(room, "astra", "notice")
        room.floor.last_activity = time.monotonic() - 2.0  # past the usual gap, not a natural break
        assert not room._notice_ok("astra") and room._notice_ok("tari")
        room.floor.last_activity = time.monotonic() - BACKGROUND_NOTICE_GAP - 0.1
        assert room._notice_ok("astra")
        room.voices["tari"]._queued_requests = 1  # an answer is on its way: it goes first
        assert not room._notice_ok("astra")
        await room.utterance("Stop.", started=now, ended=now)
        assert notice.state == "ready"  # a stop never loses a job announcement

    asyncio.run(scenario())


def test_rolling_window_eviction_and_asr_jitter_never_replay_previous_turns():
    """Reproduces the manual-testing capture where Higgs evicted older words from a 30s rolling window."""
    async def scenario():
        room = make_room()
        events = [
            ("speech_started", ""),
            ("speech_stopped", ""),
            ("completed", "Hey Astro, what projects do I have going on right now?"),
            ("speech_started", ""),
            ("speech_stopped", ""),
            (
                "completed",
                "Hey Astro, what projects do I have going on right now? "
                "Hey Astro, can you describe the current coffee shop website in the background?",
            ),
            ("speech_started", ""),
            ("speech_stopped", ""),
            (
                "completed",
                "Hey Astro, what projects do I have going on right now? "
                "Hey Astro, can you describe the current coffee shop website in the background? "
                'Hey Astro, can you change the heading and title to "Welcome to"?To Astro Studio.',
            ),
            ("speech_started", ""),
            ("speech_stopped", ""),
            (
                "completed",
                "Hey Astro, what projects do I have going on right now? "
                "Hey Astro, can you describe the current coffee shop website in the background? "
                'Hey Astro, can you change the heading and title to "Welcome to"?To Astro Studio. '
                "Okay, Tari, what do I have on my calendar today?",
            ),
            # Brief hesitation mid-request: speech_started resumes before the partial transcript arrives.
            ("speech_started", ""),
            ("speech_stopped", ""),
            ("speech_started", ""),
            (
                "completed",
                'Hey, Astro. Can you change the heading and title to "Welcome to Astro Studio"? '
                "Okay, Tari. What do I have on my calendar today? "
                "Okay, Tari, can you set a calendar event on October thirty first?First, for.",
            ),
            ("speech_stopped", ""),
            (
                "completed",
                'Hey, Ashra. Can you change the heading and title to "Welcome to Ashra Studio"? '
                "Okay, Tari. What do I have on my calendar today? "
                "Okay, Tari, can you set a calendar event on October 31st for Halloween party from 8 to 9 p.m.Hey.",
            ),
            # Rolling window drops earlier Astra turns and keeps Tari's Halloween request before new Astra questions.
            ("speech_started", ""),
            ("speech_stopped", ""),
            (
                "completed",
                "Okay, Tari. What do I have on my calendar today? "
                "Okay, Tari, can you set a calendar event on October 31st for Halloween party from 8 to 9 p.m.? "
                "Hey, Astro, what was the latest train change we made?To the website.",
            ),
            ("speech_started", ""),
            ("speech_stopped", ""),
            (
                "completed",
                "Okay, Tari, can you set a calendar event on October 31st for Halloween party from 8 to 9 p.m."
                "Hey, Astro. What was the latest train change we made to the website? Okay, Astro. Can you?Revert that.",
            ),
            ("speech_started", ""),
            ("speech_stopped", ""),
            (
                "completed",
                "Okay, Tari, can you set a calendar event on October 31st for Halloween party from 8 to 9 p.m.? "
                "Hey, Astro, what was the latest train change we made to the website?"
                "Okay, Astra, can you revert that? Okay, Astra, can you revert that change?Ah.",
            ),
        ]
        for kind, transcript in events:
            if kind == "speech_started":
                await room._on_listener({"type": "input_audio_buffer.speech_started", "item_id": "35133e6f"})
            elif kind == "speech_stopped":
                await room._on_listener({"type": "input_audio_buffer.speech_stopped", "item_id": "35133e6f"})
            else:
                await room._on_listener(
                    {
                        "type": "conversation.item.input_audio_transcription.completed",
                        "item_id": "35133e6f",
                        "transcript": transcript,
                    }
                )

        # Tari received the calendar query once and the full Halloween event request once — never replayed.
        assert room.voices["tari"].asked == [
            "Okay, Tari, what do I have on my calendar today?",
            "Okay, Tari, can you set a calendar event on October 31st for Halloween party from 8 to 9 p.m.",
        ]
        # Astra received each website request cleanly without Tari stealing the floor.
        assert room.voices["astra"].asked == [
            "Hey Astro, what projects do I have going on right now?",
            "Hey Astro, can you describe the current coffee shop website in the background?",
            'Hey Astro, can you change the heading and title to "Welcome to Astro Studio"?',
            "Hey, Astro, what was the latest train change we made to the website?",
            "Okay, Astro. Can you revert that?",
            "Okay, Astra, can you revert that change?",
        ]
        assert room.addressed == "astra"

    asyncio.run(scenario())


def test_cross_item_replay_strips_previous_turn_but_allows_fresh_repeat():
    room = make_room()
    first = "Okay, Tari, can you set a calendar event on October 31st for Halloween party from 8 to 9 p.m.?"
    assert room._new_words("item-1", first) == first
    # A new item that prepends the earlier turn onto a new request strips the replayed prefix.
    combined = f"{first} Hey Astra, what is the latest website update?"
    assert room._new_words("item-2", combined) == "Hey Astra, what is the latest website update?"
    # A single request intentionally repeated in a new item still goes through.
    repeat = "Hey Astra, what is the latest website update?"
    assert room._new_words("item-3", repeat) == repeat
