"""An agent handed over mid-chain finishes its tool calls without speaking again."""

import asyncio
import base64
from types import SimpleNamespace

from clawvoice.team.floor import SpeakingFloor
from clawvoice.team.voice import AgentVoice


def response(rid, item, call=None):
    events = [
        {"type": "response.created", "response": {"id": rid}},
        {"type": "response.output_audio.delta", "response_id": rid, "item_id": item,
         "delta": base64.b64encode(bytes(48000)).decode()},
    ]
    if call:
        events.append({"type": "response.function_call_arguments.done", "response_id": rid,
                       "call_id": "call-1", "name": call, "arguments": "{}"})
    events.append({"type": "response.done", "response_id": rid, "response": {"status": "completed"}})
    return events


class FakeSession:
    """Answers each response.create with the next scripted response."""

    closed = False

    def __init__(self, voice, script):
        self.voice, self.script, self.sent = voice, script, []

    async def send(self, message):
        self.sent.append(message)
        if message.get("type") == "response.create":
            events = self.script.pop(0)

            async def feed():
                for event in events:
                    await self.voice._on_event(event)

            asyncio.get_running_loop().create_task(feed())
        return True


class FakeTools:
    def __init__(self):
        self.release = asyncio.Event()
        self.ran: list[str] = []

    async def run(self, name, _arguments, _call_id):
        await self.release.wait()
        self.ran.append(name)
        return {"status": "accepted", "job_id": "J1"}


def test_a_handed_over_chain_runs_its_tools_but_says_nothing_more():
    async def scenario():
        async def noop(*_):
            return None

        floor = SpeakingFloor(emit=noop, flush=noop, on_event=lambda *_: None)
        room = SimpleNamespace(floor=floor, reply_created=lambda _r: None, changed=lambda: None)
        voice = object.__new__(AgentVoice)  # the chain only: no runtime, no Higgs
        voice.room, voice.id, voice.tools = room, "astra", FakeTools()
        voice._response = voice._chain = None
        voice.backgrounded = False
        voice.session = FakeSession(voice, [response("r1", "ack", call="delegate_task"), response("r2", "rest")])

        ack = floor.new_reply("astra", "answer")
        chain = asyncio.create_task(voice._generate(ack))
        while ack.state != "ready":
            await asyncio.sleep(0)
        await floor.tick()
        assert floor.current is ack
        await floor.cut()  # the owner starts talking ("Hey Tari, ...")
        voice.yield_floor()  # ...and the room hands the floor to Tari
        voice.tools.release.set()  # the delegated job is accepted after the cut
        await asyncio.wait_for(chain, 2)

        assert voice.tools.ran == ["delegate_task"]  # the work went on
        sent = [m["type"] for m in voice.session.sent]
        assert sent.count("response.create") == 2  # the chain finished...
        assert not floor.replies and floor.current is None  # ...but nothing more reached the floor
        assert any(m.get("item", {}).get("type") == "function_call_output" for m in voice.session.sent)
        assert "conversation.item.truncate" in sent  # its memory matches what was heard

    asyncio.run(scenario())
