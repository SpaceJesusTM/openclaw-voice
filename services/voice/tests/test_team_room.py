"""Team room routing: named requests reach only the named agent; nothing else is inferred."""

import asyncio
import time

from clawvoice.team.addressing import AddressBook
from clawvoice.team.floor import BYTES_PER_SECOND, SpeakingFloor
from clawvoice.team.room import TeamRoom
from clawvoice.voice.telemetry import Trace


class FakeVoice:
    def __init__(self, agent_id):
        self.id = agent_id
        self.asked: list[str] = []
        self.silenced = False
        self.stopped = 0

    def ask(self, text):
        self.asked.append(text)

    def on_stop(self):
        self.stopped += 1
        self.silenced = True


def make_room():
    room = object.__new__(TeamRoom)  # routing only: no Higgs sessions, no pipeline
    room.trace = Trace(label="test", persist=False)
    room.book = AddressBook({"tari": ["tari"], "astra": ["astra"]})
    room.voices = {"tari": FakeVoice("tari"), "astra": FakeVoice("astra")}
    room.messages = []
    room.ui = lambda kind, data: room.messages.append((kind, data))
    room.changed = lambda: None
    room.addressed = None
    room._follow_up = None
    room._last_named = None
    room._item_text = {}

    async def emit(_chunk):
        return None

    async def flush():
        return None

    room.floor = SpeakingFloor(emit=emit, flush=flush, on_event=lambda *_: None)
    return room


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
