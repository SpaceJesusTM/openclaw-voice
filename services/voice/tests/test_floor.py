import asyncio

import pytest

from clawvoice.voice import floor as floor_module
from clawvoice.voice.floor import NOTICE_PREFIX, AnnouncementCoordinator


class Harness:
    def __init__(self):
        self.injected: list[str] = []
        self.marks: list[tuple[str, str]] = []
        self.c = AnnouncementCoordinator(
            session_id="s1", agent_name="Tari", inject=self.inject, mark=self.mark, quiet_seconds=0.05
        )

    async def inject(self, text):
        self.injected.append(text)

    def mark(self, job_id, state):
        self.marks.append((job_id, state))

    def state(self, job_id):
        return [s for j, s in self.marks if j == job_id][-1]


@pytest.fixture
async def h():
    harness = Harness()
    harness.c.start()
    stop = asyncio.Event()

    async def client_audio():  # a connected browser streams input audio continuously
        while not stop.is_set():
            harness.c.on_client_audio()
            await asyncio.sleep(0.02)

    feeder = asyncio.create_task(client_audio())
    harness.stop_audio = stop.set
    yield harness
    stop.set()
    await feeder
    await harness.c.stop()


async def settle(seconds=0.35):
    await asyncio.sleep(seconds)


async def test_waits_for_greeting_then_delivers_once_floor_is_quiet(h):
    h.c.enqueue("J1", "Verified: event created.")
    await settle()
    assert h.injected == []  # greeting not finished yet
    h.c.on_response(True)
    h.c.on_bot_speaking(True)
    h.c.on_bot_speaking(False)
    h.c.on_response(False)
    await settle()
    assert len(h.injected) == 1 and h.injected[0].startswith(NOTICE_PREFIX)
    assert "Result: Verified: event created." in h.injected[0] and "not from the user" in h.injected[0]


async def test_notice_results_end_as_sentences(h):
    def notice(text):
        return h.c.compose([{"job_id": "J1", "text": text, "earlier": False}])

    assert "Result: delay test complete. Tell the user" in notice("delay test complete")
    assert "Result: Verified: event created. Tell the user" in notice("Verified: event created.")
    assert "Result: Tari said: 'all set.' Tell the user" in notice("Tari said: 'all set.'")
    assert "(1) first. (2) second? Tell" in h.c.compose(
        [{"job_id": "J1", "text": "first", "earlier": False}, {"job_id": "J2", "text": "second?", "earlier": False}]
    )


@pytest.mark.parametrize("busy", ["user", "bot", "response", "tool"])
async def test_never_speaks_over_anyone(h, busy):
    h.c.ready = True
    setter = {
        "user": lambda v: h.c.on_user_speaking(v),
        "bot": lambda v: h.c.on_bot_speaking(v),
        "response": lambda v: h.c.on_response(v),
        "tool": lambda v: h.c.on_tool("call-1", v),
    }[busy]
    setter(True)
    h.c.enqueue("J1", "done")
    await settle(0.5)
    assert h.injected == []
    setter(False)
    await settle()
    assert len(h.injected) == 1


async def test_coalesces_and_ignores_duplicates(h):
    h.c.ready = True
    h.c.on_user_speaking(True)
    for job in ("J1", "J2", "J2", "J3", "J4"):
        h.c.enqueue(job, f"result {job}")
    h.c.on_user_speaking(False)
    await settle()
    assert len(h.injected) == 1 and "3 background tasks" in h.injected[0]
    assert "(1) result J1" in h.injected[0] and "result J4" not in h.injected[0]


async def test_heard_notice_is_delivered_at_most_once(h):
    h.c.ready = True
    h.c.enqueue("J1", "done")
    await settle()
    h.c.on_bot_speaking(True)
    h.c.on_interruption()  # user barges in after hearing part of it
    h.c.on_bot_speaking(False)
    await settle()
    assert len(h.injected) == 1 and h.state("J1") == "delivered"
    h.c.enqueue("J1", "done")  # a late duplicate is ignored
    await settle()
    assert len(h.injected) == 1


async def test_unheard_interruption_is_requeued_once(h):
    h.c.ready = True
    h.c.enqueue("J1", "done")
    await settle()
    h.c.on_interruption()  # before any audio of the notice played
    assert h.state("J1") == "requeued"
    await settle()
    assert len(h.injected) == 2
    h.c.on_interruption()
    assert h.state("J1") == "interrupted"
    await settle()
    assert len(h.injected) == 2


async def test_unheard_timeout_releases_the_floor(h, monkeypatch):
    monkeypatch.setattr(floor_module, "UNHEARD_TIMEOUT_SECONDS", 0.1)
    h.c.ready = True
    h.c.enqueue("J1", "done")
    await settle()
    assert h.state("J1") == "unheard"


async def test_no_notice_is_spoken_into_a_vanished_client(h, monkeypatch):
    monkeypatch.setattr(floor_module, "CLIENT_SILENCE_SECONDS", 0.2)
    h.c.ready = True
    h.stop_audio()
    await settle(0.3)
    h.c.enqueue("J1", "done")
    await settle()
    assert h.injected == [] and h.state("J1") == "queued"


async def test_client_vanishing_mid_notice_leaves_it_unheard(h, monkeypatch):
    monkeypatch.setattr(floor_module, "CLIENT_SILENCE_SECONDS", 0.2)
    h.c.ready = True
    h.c.enqueue("J1", "done")
    await settle()
    assert len(h.injected) == 1
    h.c.on_bot_speaking(True)
    h.stop_audio()
    await settle(0.3)
    h.c.on_bot_speaking(False)
    assert h.state("J1") == "unheard"  # re-delivered on the next connection


async def test_session_end_keeps_undelivered_notices_pending():
    harness = Harness()
    harness.c.enqueue("J9", "done")
    await harness.c.stop()
    assert harness.state("J9") == "pending"


async def test_user_words_skip_notices(h):
    h.c.on_user_words("  Tari, create   an event ")
    h.c.on_user_words(f"{NOTICE_PREFIX} something")
    assert list(h.c.user_words) == ["Tari, create an event"]
