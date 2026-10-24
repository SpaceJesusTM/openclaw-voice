"""Explicit "stop" ends speech, discards the reply to that turn, and dismisses
the interrupted notice. Jobs and ordinary turns are unaffected."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from clawvoice.voice.stop import is_stop_request
from test_floor import Harness, h, settle  # noqa: F401 - shared coordinator fixture
from test_voice_pipeline import make_llm


@pytest.mark.parametrize("text", ["Stop.", "Stop talking.", "You can stop that.", "That's enough.", "Okay, stop.",
                                  "Enough.", "Be quiet.", "Never mind.", "You can stop now, thanks."])
def test_explicit_stop_phrases(text):
    assert is_stop_request(text)


@pytest.mark.parametrize("text", ["Stop the background task.", "Cancel that task.", "Stop. What's on my planner tomorrow?",
                                  "Can you stop by the store tomorrow?", "What is enough time?", ""])
def test_other_turns_are_not_stop_requests(text):
    assert not is_stop_request(text)


async def test_stop_during_a_notice_dismisses_it_and_holds_new_ones(h):  # noqa: F811
    h.c.ready = True
    h.c.enqueue("J1", "Verified: event created.")
    await settle()
    assert len(h.injected) == 1
    h.c.on_bot_speaking(True)  # the notice is being spoken
    h.c.on_interruption()  # the user starts talking over it
    h.c.on_stop_request()  # ... and the words were "stop"
    h.c.on_bot_speaking(False)  # playback stops
    assert h.state("J1") == "dismissed"
    h.c.enqueue("J2", "Verified: another result.")
    h.c.on_user_words("Stop talking.")  # the stop transcript itself releases nothing
    await settle(0.4)
    assert len(h.injected) == 1 and h.c.silenced
    h.c.on_user_words("What's on my planner tomorrow?")  # something new: notices may follow
    await settle(0.4)
    assert len(h.injected) == 2 and "J2" in h.injected[1] and "J1" not in h.injected[1]


async def test_unheard_interrupted_notice_is_not_replayed_after_stop(h):  # noqa: F811
    h.c.ready = True
    h.c.enqueue("J1", "Verified: event created.")
    await settle()
    h.c.on_user_speaking(True)
    h.c.on_interruption()  # interrupted before any audio: normally re-queued once
    assert h.state("J1") == "requeued"
    h.c.on_stop_request()
    h.c.on_user_speaking(False)
    assert h.state("J1") == "dismissed"
    h.c.on_user_words("Thanks, what time is it?")
    await settle(0.4)
    assert len(h.injected) == 1  # never replayed


async def test_ordinary_interruption_still_requeues(h):  # noqa: F811
    h.c.ready = True
    h.c.enqueue("J1", "Verified: event created.")
    await settle()
    h.c.on_user_speaking(True)
    h.c.on_interruption()
    h.c.on_user_speaking(False)
    h.c.on_user_words("Wait, what's on tomorrow?")
    assert h.state("J1") == "requeued" and not h.c.silenced


def transcript(text):
    return SimpleNamespace(item_id=f"item-{abs(hash(text))}", transcript=text)


async def test_stop_transcript_interrupts_and_cancels_the_automatic_reply():
    llm, _, trace = make_llm()
    llm._ws_send = AsyncMock()
    llm.broadcast_interruption = AsyncMock()
    llm._cancel_active_response = AsyncMock()
    llm.push_frame = AsyncMock()
    stops = []
    llm.add_event_handler("on_stop_request", lambda service, text: stops.append(text))
    llm._boson_active_response_ids = {"r-already-started"}  # the server began its reply before the transcript
    await llm.handle_evt_input_audio_transcription_completed(transcript("You can stop that."))
    llm.broadcast_interruption.assert_awaited_once()
    sent = [call.args[0] for call in llm._ws_send.call_args_list]
    assert any(event.get("type") == "response.cancel" for event in sent)
    assert not llm._boson_active_response_ids
    assert stops == ["You can stop that."] and llm._silenced
    # The server's automatic reply to that turn is cancelled; ClawVoice-requested replies are not.
    await llm._dispatch_boson_server_event({"type": "response.created", "response": {"id": "r-auto"}})
    assert llm._cancel_active_response.await_count == 1
    await llm._dispatch_boson_server_event(
        {"type": "response.created", "response": {"id": "r-typed", "metadata": {"client_event_id": "c1"}}})
    assert llm._cancel_active_response.await_count == 1
    # The user's next turn releases the guard and is answered normally.
    await llm._dispatch_boson_server_event({"type": "input_audio_buffer.speech_started", "item_id": "u2"})
    assert not llm._silenced
    await llm._dispatch_boson_server_event({"type": "response.created", "response": {"id": "r-next"}})
    assert llm._cancel_active_response.await_count == 1
    assert any(e["kind"] == "stop.cancelled_reply" for e in trace.events)


async def test_ordinary_transcript_does_not_interrupt():
    llm, _, _ = make_llm()
    llm._ws_send = AsyncMock()
    llm.broadcast_interruption = AsyncMock()
    llm.push_frame = AsyncMock()
    await llm.handle_evt_input_audio_transcription_completed(transcript("Stop by the store tomorrow at noon."))
    llm.broadcast_interruption.assert_not_awaited()
    assert not llm._silenced
    await asyncio.sleep(0)
