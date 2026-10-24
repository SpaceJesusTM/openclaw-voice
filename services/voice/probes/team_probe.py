"""Live Team-mode smoke probe over WebRTC + RTVI, the same path the browser uses.

    .venv/bin/python probes/team_probe.py --live                 # typed checks only
    .venv/bin/python probes/team_probe.py --live --speech --stop # plus spoken routing and "stop"
    .venv/bin/python probes/team_probe.py --live --notice        # plus a read-only OpenClaw job notice

Requires ``make dev`` with Tari and Astra configured, the Boson key and both
isolated OpenClaw Gateways. Speech input is synthetic (macOS ``say``). Nothing
is created or changed: the --notice job is a read-only Calendar question.

Checks (from server events, not model wording):
  base    Tari greets; two named requests sent 0.3 s apart reach the right agents,
          each runs its own cache tool, both answers play, one after the other
          (no overlap); an unnamed request reaches nobody
  speech  a spoken "Hey Tari, ..." is transcribed, routed to Tari and answered
  stop    a spoken "Stop." over a long answer ends it; nothing plays afterwards
  notice  a Tari background question finishes while Astra answers something
          else, and Tari's completion notice is spoken afterwards (delivered)

Reports go to ignored captures/team-probe-*.json (they contain conversation text).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path

import aiohttp
from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription
from aiortc.mediastreams import MediaStreamError

sys.path.insert(0, str(Path(__file__).parent))
from probe_audio import WavMicrophone  # noqa: E402

CAPTURES = Path(__file__).resolve().parent.parent / "captures"


class ProbeFailure(AssertionError):
    pass


def require(condition, message):
    if not condition:
        raise ProbeFailure(message)


def wav(name: str, text: str) -> Path:
    path = CAPTURES / f"team-probe-{name}.wav"
    if not path.exists():
        CAPTURES.mkdir(exist_ok=True)
        subprocess.run(["say", "-o", str(path), "--file-format=WAVE", "--data-format=LEI16@48000", text], check=True)
    return path


class TeamClient:
    def __init__(self, url: str):
        self.url = url
        self.t0 = time.monotonic()
        self.events: list[dict] = []
        self.loud: list[float] = []
        self.mic = WavMicrophone()
        self.pc: RTCPeerConnection | None = None
        self.channel = None
        self.tasks: set[asyncio.Task] = set()

    def now(self) -> float:
        return round(time.monotonic() - self.t0, 2)

    async def connect(self) -> None:
        self.pc = RTCPeerConnection(RTCConfiguration(iceServers=[]))
        self.pc.addTrack(self.mic)
        self.channel = self.pc.createDataChannel("rtvi")

        @self.channel.on("open")
        def opened():
            self.send("client-ready", {"version": "1.0.0", "about": {"library": "clawvoice-team-probe"}})

        @self.channel.on("message")
        def message(raw):
            msg = json.loads(raw)
            data = msg.get("data") or {}
            if msg.get("type") == "server-message":
                self.events.append({"t": self.now(), **data})

        @self.pc.on("track")
        def track(track):
            async def consume():
                try:
                    while True:
                        frame = await track.recv()
                        if abs(frame.to_ndarray().astype("int32")).max() > 200:
                            self.loud.append(self.now())
                except MediaStreamError:
                    pass

            task = asyncio.create_task(consume())
            self.tasks.add(task)

        async with aiohttp.ClientSession() as http:
            async with http.post(f"{self.url}/start", json={"transport": "webrtc", "body": {"mode": "team"}}) as r:
                r.raise_for_status()
                session_id = (await r.json())["sessionId"]
            await self.pc.setLocalDescription(await self.pc.createOffer())
            async with http.post(f"{self.url}/sessions/{session_id}/api/offer",
                                 json={"sdp": self.pc.localDescription.sdp, "type": "offer"}) as r:
                r.raise_for_status()
                answer = await r.json()
        await self.pc.setRemoteDescription(RTCSessionDescription(answer["sdp"], answer["type"]))

    async def close(self) -> None:
        try:
            self.send("disconnect-bot", {})
        except Exception:  # noqa: BLE001
            pass
        self.mic.stop()
        if self.pc:
            await self.pc.close()
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)

    def send(self, kind: str, data: dict) -> None:
        self.channel.send(json.dumps({"id": f"team-probe-{time.monotonic_ns()}", "label": "rtvi-ai", "type": kind, "data": data}))

    def say(self, text: str) -> float:
        mark = self.now()
        self.send("client-message", {"t": "clawvoice.say", "d": {"text": text}})
        return mark

    def kind(self, kind: str, since: float = 0.0) -> list[dict]:
        return [e for e in self.events if e.get("kind") == kind and e["t"] >= since]

    def replies(self, since: float, agent=None, phase=None, reply_kind=None) -> list[dict]:
        return [e for e in self.kind("clawvoice.team.reply", since) if (agent is None or e["agent"] == agent)
                and (phase is None or e["phase"] == phase) and (reply_kind is None or e.get("reply_kind") == reply_kind)]

    async def wait(self, predicate, what: str, timeout: float = 60) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            errors = self.kind("clawvoice.team.error")
            if any(e.get("fatal") for e in errors):
                raise ProbeFailure(f"team room error: {errors[-1].get('message')}")
            if predicate():
                return
            await asyncio.sleep(0.1)
        raise ProbeFailure(f"timed out waiting for {what}")

    async def quiet(self, seconds: float = 2.5, timeout: float = 120) -> None:
        """Until nothing is speaking, queued or being generated for `seconds`."""
        calm = None
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            floors = self.kind("clawvoice.team.floor")
            last = floors[-1] if floors else {}
            busy = last.get("speaking") or last.get("queue") or any(
                a.get("state") in ("thinking", "speaking", "waiting") for a in (last.get("agents") or {}).values())
            calm = None if busy else calm or time.monotonic()
            if calm and time.monotonic() - calm >= seconds:
                return
            await asyncio.sleep(0.1)
        raise ProbeFailure("the room never went quiet")


def spans(client: TeamClient, since: float) -> list[list]:
    """[start, end, agent] for each reply heard since `since`, in order."""
    found: dict[int, list] = {}
    for e in client.replies(since):
        if e["phase"] == "speaking":
            found.setdefault(e["reply"], [e["t"], None, e["agent"]])
        elif e["phase"] in ("done", "cut") and e["reply"] in found:
            found[e["reply"]][1] = e["t"]
    return sorted(found.values())


async def base(client: TeamClient, out: dict) -> None:
    await client.wait(lambda: client.kind("clawvoice.team.ready"), "team ready", 40)
    await client.quiet()
    greeting = client.replies(0, "tari", "done")
    require(greeting, "Tari did not greet")
    out["greeting"] = greeting[0].get("text")
    mark = client.say("Hey Tari, what's on my planner today?")
    await asyncio.sleep(0.3)
    client.say("Okay Astra, what's the latest website update?")
    await client.wait(lambda: {e["agent"] for e in client.replies(mark, phase="done")} >= {"tari", "astra"}, "both answers", 120)
    routes = [(e.get("agent"), e.get("status")) for e in client.kind("clawvoice.team.user", mark)]
    tools = [(e.get("agent"), e.get("name"), e.get("topic")) for e in client.kind("clawvoice.tool", mark) if e.get("phase") == "result"]
    heard = spans(client, mark)
    out["concurrent"] = {"routes": routes, "tools": tools, "spans": heard}
    require(routes == [("tari", "routed"), ("astra", "routed")], f"routing {routes}")
    require({a for a, *_ in tools} == {"tari", "astra"}, f"each agent should use its own tools: {tools}")
    require(all(b[0] >= a[1] for a, b in zip(heard, heard[1:]) if a[1] is not None), f"overlapping replies {heard}")
    await client.quiet()
    mark = client.say("What's the weather like today?")
    await asyncio.sleep(4)
    unaddressed = client.kind("clawvoice.team.user", mark)
    out["unaddressed"] = [(e.get("agent"), e.get("status")) for e in unaddressed]
    require(unaddressed and unaddressed[0].get("status") == "unaddressed", "unnamed request was routed")
    require(not client.replies(mark), "an agent answered an unnamed request")


async def speech(client: TeamClient, out: dict) -> None:
    await client.quiet()
    mark = client.now()
    client.mic.play(wav("tari-planner", "Hey Tari, what's on my planner today?"))
    await client.wait(lambda: client.replies(mark, "tari", "done"), "spoken request answered", 90)
    out["speech"] = [(e.get("text"), e.get("agent"), e.get("status")) for e in client.kind("clawvoice.team.user", mark)]
    require(out["speech"] and out["speech"][0][1] == "tari", f"spoken request routed {out['speech']}")


async def stop(client: TeamClient, out: dict) -> None:
    await client.quiet()
    mark = client.say("Astra, please describe the coffee shop website in detail, section by section.")
    await client.wait(lambda: client.replies(mark, "astra", "speaking"), "Astra speaking", 60)
    await asyncio.sleep(3)
    client.mic.play(wav("stop", "Stop."))
    await client.wait(lambda: any(e.get("status") == "stop" for e in client.kind("clawvoice.team.user", mark)), "stop", 30)
    stop_at = client.now()
    await asyncio.sleep(7)
    out["stop"] = {
        "astra": [(e["phase"], e.get("outcome")) for e in client.replies(mark, "astra") if e["phase"] in ("cut", "done", "dropped")],
        "spoken_after": [(e["agent"], e["phase"]) for e in client.replies(stop_at + 0.5, phase="speaking")],
        "audio_after_s": round(sum(1 for t in client.loud if t > stop_at + 1.0) * 0.02, 1),
    }
    require(not out["stop"]["spoken_after"] and out["stop"]["audio_after_s"] == 0, f"speech after stop {out['stop']}")


async def notice(client: TeamClient, out: dict) -> None:
    await client.quiet()
    mark = client.say("Tari, please do a live lookup in your calendar: what events do I have on January 5th, 2027?")
    await client.wait(lambda: any(e.get("name") == "delegate_task" and e.get("phase") == "result"
                                  for e in client.kind("clawvoice.tool", mark)), "Tari delegates the lookup", 60)
    await client.wait(lambda: client.replies(mark, "tari", "done"), "Tari's acknowledgement", 60)
    ask = client.say("Okay Astra, what coding projects are you working on?")
    await client.wait(lambda: client.replies(ask, "astra", "done"), "Astra's answer", 90)
    await client.wait(lambda: client.replies(mark, "tari", "done", "notice"), "Tari's completion notice", 300)
    spoken = client.replies(mark, "tari", "done", "notice")[0]
    out["notice"] = {"text": spoken.get("text"), "spans": spans(client, mark)}
    heard = spans(client, mark)
    require(all(b[0] >= a[1] for a, b in zip(heard, heard[1:]) if a[1] is not None), f"overlapping replies {heard}")


async def main(args) -> int:
    client = TeamClient(args.url)
    out: dict = {"started": time.strftime("%Y-%m-%dT%H:%M:%S")}
    status = "PASS"
    try:
        await client.connect()
        await base(client, out)
        if args.speech:
            await speech(client, out)
        if args.stop:
            await stop(client, out)
        if args.notice:
            await notice(client, out)
    except ProbeFailure as failure:
        status, out["failure"] = "FAIL", str(failure)
    finally:
        await client.close()
        out["status"] = status
        out["errors"] = client.kind("clawvoice.team.error")
        CAPTURES.mkdir(exist_ok=True)
        path = CAPTURES / f"team-probe-{int(time.time())}.json"
        path.write_text(json.dumps({**out, "events": client.events}, indent=1, default=str))
        print(json.dumps({k: v for k, v in out.items() if k != "events"}, indent=1, default=str))
        print(f"{status}: report {path}")
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--live", action="store_true", help="required: uses Boson and both OpenClaw agents")
    parser.add_argument("--url", default="http://127.0.0.1:7860")
    parser.add_argument("--speech", action="store_true")
    parser.add_argument("--stop", action="store_true")
    parser.add_argument("--notice", action="store_true")
    arguments = parser.parse_args()
    if not arguments.live:
        parser.error("pass --live to permit live API use")
    sys.exit(asyncio.run(main(arguments)))
