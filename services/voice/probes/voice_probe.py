"""Live ClawVoice acceptance probe over WebRTC + RTVI, the same path a browser uses.

    .venv/bin/python probes/voice_probe.py --live --scenario all        # a-e, g, h
    .venv/bin/python probes/voice_probe.py --live --scenario c,g        # any subset

Requires ``make dev`` running, the Boson key, and the isolated demo OpenClaw.
Speech input uses synthetic WAV files generated with macOS ``say`` (not a
physical microphone). Checks rely on observable evidence: model
acknowledgements, tool calls and results, cache status, job states, Boson
truncation events and independent helper read-backs, not on model wording.

  a  Higgs voice: preview model, greeting, typed turn, spoken question,
     barge-in with server truncation, disconnect and reconnect
  b  cached planner answer from real planner data; no OpenClaw run
  c  real Calendar event lifecycle via voice -> OpenClaw -> verified -> cleanup
  d  nonblocking: delayed OpenClaw job; a cached question is answered before
     it ends; barge-in does not cancel it; completion announced without overlap
  e  read-your-writes after an agent change; out-of-band change picked up by
     background refresh; cleanup
  f  portability: the fixture agent through the same runtime (needs
     `make fixture-gateway` and a fixture server, see scenario_f)
  g  an ambiguous request gets a clarifying question, not a job; then a
     Calendar create, a verified time change of the same event, and delete
  h  checklist add verified and visible in the cached checklist; removal
  i  cache vs live: today from the cache (no job); January 2027, an explicit
     live check and a broad question go to OpenClaw or a clarifying question,
     verified in OpenClaw's own transcript; disposable January event removed

The disposable Calendar event and planner items use unique "ClawVoice ..."
titles, tomorrow/today only, no attendees, and are removed at the end.
Reports go to ignored captures/probe-*.json (they contain conversation text).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import re
import sqlite3
import subprocess
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import aiohttp
from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription
from aiortc.mediastreams import MediaStreamError

sys.path.insert(0, str(Path(__file__).resolve().parent))
from probe_audio import WavMicrophone  # noqa: E402

MODEL = "higgs-realtime-1.5-preview"
SERVICE = Path(__file__).resolve().parents[1]
REPO = SERVICE.parent.parent
CAPTURES = SERVICE / "captures"
TZ = ZoneInfo("America/Toronto")
BIRDS = ["Heron", "Kestrel", "Plover", "Osprey", "Finch", "Wren", "Swift", "Tern"]


class ProbeFailure(AssertionError):
    pass


def require(condition, message):
    if not condition:
        raise ProbeFailure(message)


def norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(text).lower()).strip()


def unique(prefix: str) -> str:
    return f"{prefix} {random.choice(BIRDS)} {random.randint(10, 99)}"


def manage(*args: str, timeout: float = 120) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(REPO / "infra/openclaw/manage.py"), *args],
        capture_output=True, text=True, timeout=timeout, cwd=REPO,
    )


def helper_json(*args: str):
    result = manage(*args)
    require(result.returncode == 0, f"helper {' '.join(args[:3])} failed with exit {result.returncode}")
    text = result.stdout.strip()
    return json.loads(text[text.find("{") if text.find("{") != -1 else text.find("["):])


def calendar_events_on(day) -> list[dict]:
    start = datetime.combine(day, datetime.min.time(), TZ)
    data = helper_json("google", "calendar", "list", "--start", start.isoformat(),
                       "--end", (start + timedelta(days=1)).isoformat(), "--json")
    require(data.get("complete") is True, "Calendar list incomplete")
    return data["events"]


def wav(name: str, text: str) -> Path:
    path = CAPTURES / f"probe-{name}.wav"
    if not path.exists():
        CAPTURES.mkdir(exist_ok=True)
        subprocess.run(["say", "-o", str(path), "--file-format=WAVE", "--data-format=LEI16@48000", text], check=True)
    return path


class Probe:
    def __init__(self, url: str, timeout: float):
        self.url = url
        self.timeout = timeout
        self.started = time.monotonic()
        self.events: list[dict] = []
        self.model: str | None = None
        self.failure: str | None = None
        self.frames = 0
        self.nonzero = 0
        self.mic = WavMicrophone()
        self.pc: RTCPeerConnection | None = None
        self.channel = None
        self.tasks: set[asyncio.Task] = set()
        self.closing = False
        self.states: list[tuple] = []
        self.handler_errors: list[str] = []

    def now_ms(self) -> int:
        return round((time.monotonic() - self.started) * 1000)

    # ---------------------------------------------------------- connection
    async def connect(self, agent: str = "tari") -> None:
        self.pc = RTCPeerConnection(RTCConfiguration(iceServers=[]))
        self.pc.addTrack(self.mic)
        self.channel = self.pc.createDataChannel("rtvi")

        @self.channel.on("open")
        def opened():
            self.send("client-ready", {"version": "1.0.0", "about": {"library": "clawvoice-probe"}})

        @self.channel.on("message")
        def message(raw):
            try:
                self.receive(raw)
            except Exception as error:  # noqa: BLE001 - never let a probe bug close WebRTC
                self.handler_errors.append(f"{type(error).__name__}: {error}")

        @self.pc.on("track")
        def track(track):
            async def consume():
                try:
                    while True:
                        frame = await track.recv()
                        if track.kind == "audio":
                            self.frames += 1
                            if abs(frame.to_ndarray().astype("int32")).max() > 2:
                                self.nonzero += 1
                except MediaStreamError:
                    pass

            task = asyncio.create_task(consume())
            self.tasks.add(task)

        @self.pc.on("iceconnectionstatechange")
        def ice_state():
            self.states.append((self.now_ms(), "ice", self.pc.iceConnectionState))

        @self.pc.on("connectionstatechange")
        def state():
            self.states.append((self.now_ms(), "pc", self.pc.connectionState))
            if self.pc.connectionState in ("failed", "closed", "disconnected") and not self.closing:
                self.failure = f"WebRTC {self.pc.connectionState}"

        async with aiohttp.ClientSession() as http:
            async with http.post(f"{self.url}/start", json={"transport": "webrtc", "body": {"agent": agent}}) as r:
                r.raise_for_status()
                session_id = (await r.json())["sessionId"]
            await self.pc.setLocalDescription(await self.pc.createOffer())
            async with http.post(f"{self.url}/sessions/{session_id}/api/offer",
                                 json={"sdp": self.pc.localDescription.sdp, "type": "offer"}) as r:
                r.raise_for_status()
                answer = await r.json()
        await self.pc.setRemoteDescription(RTCSessionDescription(answer["sdp"], answer["type"]))
        await self.wait(lambda: self.model == MODEL, "exact preview model acknowledgement")

    async def close(self) -> None:
        self.closing = True
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
        self.channel.send(json.dumps({"id": f"probe-{time.monotonic_ns()}", "label": "rtvi-ai", "type": kind, "data": data}))

    # ---------------------------------------------------------- evidence
    def receive(self, raw: str) -> None:
        message = json.loads(raw)
        message["t"] = self.now_ms()
        self.events.append(message)
        data = message.get("data") or {}
        if message.get("type") == "error":
            self.failure = f"server error: {data}"
        if message.get("type") != "server-message":
            return
        if data.get("kind") == "preview-model" and data.get("acknowledged"):
            self.model = data["acknowledged"]
        event = data.get("event") if isinstance(data.get("event"), dict) else {}
        if event.get("type") in ("session.created", "session.updated"):
            self.model = (event.get("session") or {}).get("model")
            if self.model != MODEL:
                self.failure = f"unexpected model {self.model!r}"
        if event.get("type") == "error":
            code = (event.get("error") or {}).get("code")
            if code not in ("response_not_active", "response_id_mismatch"):
                self.failure = f"Boson error {event.get('error')}"

    def mark(self) -> int:
        return len(self.events)

    def since(self, offset: int, kind: str | None = None) -> list[dict]:
        return [e for e in self.events[offset:] if kind is None or e.get("type") == kind]

    def server(self, offset: int, kind: str) -> list[dict]:
        return [e["data"] | {"t": e["t"]} for e in self.since(offset, "server-message") if e["data"].get("kind") == kind]

    def boson(self, offset: int, direction: str, event_type: str) -> list[dict]:
        return [d["event"] | {"t": d["t"]} for d in self.server(offset, f"boson.{direction}") if d["event"].get("type") == event_type]

    def bot_text(self, offset: int) -> str:
        return "".join(e["data"].get("text", "") for e in self.since(offset, "bot-llm-text")).strip()

    def tools(self, offset: int, name: str | None = None) -> list[dict]:
        return [t for t in self.server(offset, "clawvoice.tool") if t.get("phase") == "result" and (name is None or t["name"] == name)]

    def jobs(self, offset: int, job_id: str | None = None, event: str | None = None) -> list[dict]:
        return [
            j for j in self.server(offset, "clawvoice.job")
            if (job_id is None or j["job"]["job_id"] == job_id) and (event is None or j["event"] == event)
        ]

    def latencies(self, offset: int, origin: str | None = None) -> list[int]:
        return [x["first_audio_ms"] for x in self.server(offset, "clawvoice.latency") if origin is None or x["origin"] == origin]

    async def wait(self, predicate, what: str, timeout: float | None = None) -> None:
        deadline = time.monotonic() + (timeout or self.timeout)
        while time.monotonic() < deadline:
            if self.failure:
                raise ProbeFailure(self.failure)
            if predicate():
                return
            await asyncio.sleep(0.05)
        raise ProbeFailure(f"timed out waiting for {what}")

    def turn_complete(self, offset: int) -> bool:
        texts = [i for i, e in enumerate(self.events[offset:]) if e.get("type") == "bot-llm-text"]
        stops = [i for i, e in enumerate(self.events[offset:]) if e.get("type") in ("bot-stopped-speaking", "bot-interrupted")]
        return bool(texts and stops and stops[-1] > texts[-1])

    async def wait_turn(self, offset: int, what: str, timeout: float | None = None, quiet: float = 1.5) -> str:
        await self.wait(lambda: self.turn_complete(offset), what, timeout)
        while True:  # let multi-part replies (tool call -> answer) finish
            count = len(self.since(offset, "bot-llm-text"))
            await asyncio.sleep(quiet)
            if len(self.since(offset, "bot-llm-text")) == count and self.turn_complete(offset):
                return self.bot_text(offset)

    async def say(self, text: str) -> int:
        offset = self.mark()
        self.send("send-text", {"content": text, "options": {"audio_response": True}})
        return offset

    def report(self) -> dict:
        return {"model": self.model, "media_frames": self.frames, "nonzero_media_frames": self.nonzero,
                "states": self.states, "handler_errors": self.handler_errors, "events": self.events}


async def api(url: str, path: str, method: str = "GET"):
    async with aiohttp.ClientSession() as http:
        async with http.request(method, f"{url}{path}") as response:
            return await response.json()


def job_state(state: dict, job_id: str) -> dict:
    return next((j for j in state["jobs"] if j["job_id"] == job_id), {})


async def greet(probe: Probe, url: str, agent: str = "tari") -> str:
    """Wait for the greeting, then for any results left unheard by earlier sessions to be spoken."""
    greeting = await probe.wait_turn(0, "greeting")
    for _ in range(3):
        state = await api(url, f"/api/state?agent={agent}")
        waiting = [j for j in state["jobs"] if j.get("announcement") in ("pending", "interrupted_unheard", "delivering")
                   and j.get("age_seconds", 0) < 6 * 3600]
        if not waiting:
            break
        await probe.wait_turn(probe.mark(), "earlier results announced", timeout=60)
    return greeting


async def wait_job(probe: Probe, offset: int, kind_text: str, timeout: float = 300) -> dict:
    await probe.wait(lambda: any(t["name"] == "delegate_task" for t in probe.tools(offset)), f"delegate_task for {kind_text}", 60)
    result = next(t for t in probe.tools(offset) if t["name"] == "delegate_task")
    require(result.get("status") == "accepted", f"delegate_task status {result.get('status')}")
    job_id = result["job_id"]
    await probe.wait(lambda: probe.jobs(offset, job_id, "job.finished"), f"job {job_id} to finish", timeout)
    return probe.jobs(offset, job_id, "job.finished")[-1]["job"] | {"accepted_at_t": result["t"]}


async def settle_jobs(url: str, agent: str = "tari", limit: float = 420) -> None:
    """Jobs outlive voice sessions: let running ones finish before a fallback cleanup."""
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        state = await api(url, f"/api/state?agent={agent}")
        if not any(j["status"] in ("accepted", "queued", "running", "verifying") for j in state["jobs"]):
            return
        await asyncio.sleep(3)


async def remove_disposable_events(url: str, day, title: str) -> str:
    """Fallback cleanup after a failure: delete only events this probe created.

    The title is unique and was checked absent before the probe started, so a
    primary-calendar event with exactly this title on that day is the probe's own.
    """
    await settle_jobs(url)
    mine = [e for e in calendar_events_on(day) if e.get("summary") == title]
    for event in mine:
        if event.get("calendar_primary"):
            manage("google", "calendar", "manage", "delete", "primary", event["id"], "--send-updates", "none")
    left = [e for e in calendar_events_on(day) if e.get("summary") == title]
    return f"fallback CLI cleanup of {len(mine)} disposable event(s): {'absent' if not left else 'STILL PRESENT'}"


# ============================================================== scenarios
async def scenario_a(url: str, timeout: float, out: dict) -> dict:
    probe = Probe(url, timeout)
    try:
        await probe.connect()
        out["greeting"] = await probe.wait_turn(0, "greeting")
        require(probe.nonzero > 0, "no decoded non-silent audio")
        out["model"] = probe.model
        offset = await probe.say("Say exactly: Higgs preview connection verified.")
        out["typed_reply"] = await probe.wait_turn(offset, "typed reply")
        require("verified" in out["typed_reply"].lower(), "typed reply missing the requested phrase")
        out["typed_first_audio_ms"] = probe.latencies(offset, "user_text")[:1]
        frames_before = probe.nonzero
        offset = probe.mark()
        probe.mic.play(wav("planner-today", "What is on my planner today?"))
        await probe.wait(lambda: any(e["data"].get("final") for e in probe.since(offset, "user-transcription")), "speech transcription")
        out["spoken_reply"] = await probe.wait_turn(offset, "spoken reply")
        require(probe.boson(offset, "recv", "input_audio_buffer.speech_started"), "no server VAD speech start")
        out["user_transcription"] = [e["data"]["text"] for e in probe.since(offset, "user-transcription") if e["data"].get("final")]
        out["speech_tools"] = [(t["name"], t.get("topic"), t.get("status"), t.get("cache")) for t in probe.tools(offset)]
        require(any(t["name"] == "get_context" for t in probe.tools(offset)), "spoken planner question did not use get_context")
        out["speech_end_to_first_audio_ms"] = probe.latencies(offset, "speech_end")
        require(probe.nonzero > frames_before, "no audio for spoken turn")
        offset = await probe.say("Count from one to forty slowly, saying every number.")
        await probe.wait(lambda: probe.since(offset, "bot-started-speaking"), "counting speech")
        await asyncio.sleep(1.5)
        barge = probe.mark()
        probe.mic.play(wav("interrupt", "Stop counting. Say interruption confirmed."))
        await probe.wait(lambda: probe.since(barge, "bot-interrupted"), "bot interruption")
        out["after_barge_in"] = await probe.wait_turn(probe.mark(), "reply after interruption")
        cuts = probe.boson(barge, "send", "conversation.item.truncate")
        require(cuts, "no conversation.item.truncate sent to Boson")
        out["truncation_audio_end_ms"] = cuts[0].get("audio_end_ms")
        require("confirm" in out["after_barge_in"].lower(), "reply after barge-in missing 'confirmed'")
    finally:
        await probe.close()
        out["session_1"] = probe.report()
    await asyncio.sleep(1.0)
    again = Probe(url, timeout)
    try:
        await again.connect()
        await again.wait_turn(0, "greeting after reconnect")
        offset = await again.say("Say exactly: reconnected.")
        out["reconnect_reply"] = await again.wait_turn(offset, "reply after reconnect")
        require("reconnect" in out["reconnect_reply"].lower(), "no reply after reconnect")
        out["reconnect_model"] = again.model
    finally:
        await again.close()
        out["session_2"] = again.report()
    return out


async def scenario_b(url: str, timeout: float, out: dict) -> dict:
    title = unique("ClawVoice Probe")
    day = datetime.now(TZ).date()
    added = None
    for slot in ("21:00-21:30", "21:30-22:00", "22:00-22:30"):
        result = manage("planner", "add", day.isoformat(), slot, title)
        if result.returncode == 0:
            added = slot
            break
    require(added, "could not add the disposable planner item")
    out.update(title=title, slot=added)
    probe = Probe(url, timeout)
    try:
        refreshed = await api(url, "/api/agents/tari/cache/planner.today/refresh", "POST")
        require(refreshed["refreshed"] and norm(title) in norm(refreshed["topic"]["summary"]), "cache warm-up missed the item")
        before = await api(url, "/api/state?agent=tari")
        await probe.connect()
        await probe.wait_turn(0, "greeting")
        offset = await probe.say("What's on my planner today?")
        out["reply"] = await probe.wait_turn(offset, "planner answer")
        tools = probe.tools(offset)
        out["tools"] = tools
        context = [t for t in tools if t["name"] == "get_context" and t.get("topic") == "planner.today"]
        require(context, "get_context(planner.today) was not called")
        require(context[0]["status"] == "fresh" and context[0]["cache"] == "hit", f"not a fresh cache hit: {context[0]}")
        require(not [t for t in tools if t["name"] == "delegate_task"], "a cache question started an OpenClaw job")
        after = await api(url, "/api/state?agent=tari")
        require(len(after["jobs"]) == len(before["jobs"]), "a job was created for a cached answer")
        words = norm(title).split()
        require(all(w in norm(out["reply"]) for w in words[1:]), "spoken answer missing the planner item")
        out["cache_read_ms"] = context[0].get("read_ms")
        out["tool_ms"] = context[0].get("ms")
        out["tool_result_to_first_audio_ms"] = probe.latencies(offset, "tool_result")
        out["typed_to_first_audio_ms"] = probe.latencies(offset, "user_text")
        out["openclaw_runs_during_answer"] = 0
    finally:
        await probe.close()
        out["session"] = probe.report()
        manage("planner", "remove", day.isoformat(), title)
        out["cleanup_planner_item"] = title not in manage("planner", "show", day.isoformat()).stdout
    return out


async def scenario_c(url: str, timeout: float, out: dict) -> dict:
    title = unique("ClawVoice Test")
    tomorrow = datetime.now(TZ).date() + timedelta(days=1)
    out.update(title=title, date=tomorrow.isoformat())
    require(not [e for e in calendar_events_on(tomorrow) if e.get("summary") == title], "title already exists")
    probe = Probe(url, timeout)
    try:
        await probe.connect()
        await probe.wait_turn(0, "greeting")
        offset = await probe.say(f"Create a Google Calendar event tomorrow from 3 to 4 PM called {title}.")
        out["ack"] = await probe.wait_turn(offset, "acknowledgement", timeout=60)
        out["ack_claims_done"] = bool(re.search(r"\b(done|created|added|booked|scheduled)\b", out["ack"].lower()))
        job = await wait_job(probe, offset, "calendar create", 360)
        out["job"] = job
        require(job["status"] == "completed" and job["verification"] == "verified", f"create not verified: {job}")
        announce = probe.mark()
        out["announcement"] = await probe.wait_turn(announce, "completion announcement", timeout=90)
        events = [e for e in calendar_events_on(tomorrow) if e.get("summary") == title]
        require(len(events) == 1, f"expected exactly one created event, found {len(events)}")
        event = events[0]
        start = datetime.fromisoformat(event["start"]["dateTime"].replace("Z", "+00:00")).astimezone(TZ)
        end = datetime.fromisoformat(event["end"]["dateTime"].replace("Z", "+00:00")).astimezone(TZ)
        out["readback"] = {"start_local": start.isoformat(), "end_local": end.isoformat(),
                           "start_utc": start.astimezone(timezone.utc).isoformat(), "attendees": bool(event.get("attendees")),
                           "calendar": event.get("calendar_id"), "calendar_primary": event.get("calendar_primary")}
        require((start.hour, start.minute, end.hour, end.minute) == (15, 0, 16, 0), "event is not 3-4 PM Toronto")
        require(not event.get("attendees"), "disposable event unexpectedly has attendees")
        offset = await probe.say(f"Please delete the Calendar event called {title} tomorrow.")
        delete_job = await wait_job(probe, offset, "calendar delete", 360)
        out["delete_job"] = delete_job
        require(delete_job["status"] == "completed" and delete_job["verification"] == "verified", f"delete not verified: {delete_job}")
        out["delete_announcement"] = await probe.wait_turn(probe.mark(), "delete announcement", timeout=90)
        require(not [e for e in calendar_events_on(tomorrow) if e.get("summary") == title], "event still present after delete")
        out["cleanup"] = "deleted through voice -> OpenClaw and re-checked absent"
    finally:
        await probe.close()
        out["session"] = probe.report()
        if "cleanup" not in out:  # the voice delete did not finish
            out["cleanup"] = await remove_disposable_events(url, tomorrow, title)
    return out


async def scenario_d(url: str, timeout: float, out: dict) -> dict:
    probe = Probe(url, timeout)
    try:
        await probe.connect()
        await probe.wait_turn(0, "greeting")
        offset = await probe.say(
            "Start a background question for the agent: ask it to run the shell command 'sleep 35' "
            "and then reply exactly 'delay test complete'. It's a question, not a change."
        )
        await probe.wait(lambda: any(t["name"] == "delegate_task" for t in probe.tools(offset)), "delegation", 60)
        accepted = next(t for t in probe.tools(offset) if t["name"] == "delegate_task")
        require(accepted.get("status") == "accepted", f"delegate status {accepted.get('status')}")
        job_id = accepted["job_id"]
        out["job_id"] = job_id
        out["ack"] = await probe.wait_turn(offset, "acknowledgement", timeout=60)
        question = await probe.say("Also, what's on my planner tomorrow?")
        out["cached_reply"] = await probe.wait_turn(question, "cached planner answer", timeout=60)
        answered_at = probe.since(question, "bot-stopped-speaking")[-1]["t"]
        tools = probe.tools(question)
        require(any(t["name"] == "get_context" and t.get("topic") == "planner.tomorrow" for t in tools), "no cached planner read")
        require(not probe.jobs(0, job_id, "job.finished"), "job finished before the cached answer; delay too short")
        out["cached_answer_done_at_ms"] = answered_at
        out["job_running_when_answered"] = True
        barge = await probe.say("Count slowly from one to thirty.")
        await probe.wait(lambda: probe.since(barge, "bot-started-speaking"), "counting")
        await asyncio.sleep(1.2)
        interrupt = probe.mark()
        probe.mic.play(wav("interrupt", "Stop counting. Say interruption confirmed."))
        await probe.wait(lambda: probe.since(interrupt, "bot-interrupted"), "interruption")
        require(probe.boson(interrupt, "send", "conversation.item.truncate"), "no truncation on barge-in")
        out["after_barge_in"] = await probe.wait_turn(probe.mark(), "reply after barge-in")
        state = await api(url, "/api/state?agent=tari")
        require(job_state(state, job_id).get("status") in ("running", "accepted", "queued"), "barge-in changed the job")
        out["job_status_after_barge_in"] = job_state(state, job_id).get("status")
        await probe.wait(lambda: probe.jobs(0, job_id, "job.finished"), "delayed job completion", 300)
        finished = probe.jobs(0, job_id, "job.finished")[-1]
        out["job"] = finished["job"]
        require(finished["job"]["status"] == "completed", f"job ended {finished['job']['status']}")
        require(finished["t"] > answered_at, "job did not outlast the cached answer")
        notice_at = probe.mark()
        await probe.wait(lambda: any(job_id in (f.get("delivering") or []) for f in probe.server(0, "clawvoice.floor")), "notice delivery", 120)
        out["announcement"] = await probe.wait_turn(notice_at, "announcement", timeout=90)
        delivering = next(f for f in probe.server(0, "clawvoice.floor") if job_id in (f.get("delivering") or []))
        speaking_events = [e for e in probe.events if e.get("type") in ("bot-started-speaking", "bot-stopped-speaking") and e["t"] <= delivering["t"]]
        out["floor_at_injection"] = {k: delivering.get(k) for k in ("user_speaking", "bot_speaking", "response_active", "tools_in_flight")}
        out["bot_speaking_at_injection"] = bool(speaking_events and speaking_events[-1]["type"] == "bot-started-speaking")
        require(
            not out["bot_speaking_at_injection"] and not any(out["floor_at_injection"].values()),
            f"notice overlapped speech or a response: {out['floor_at_injection']}",
        )
        out["notice_to_first_audio_ms"] = probe.latencies(notice_at, "notice")
        state = await api(url, "/api/state?agent=tari")
        job = job_state(state, job_id)
        out["announcement_state"] = job.get("announcement")
        out["timings"] = job.get("timings")
        require(job.get("announcement") == "delivered", f"announcement state {job.get('announcement')}")
        status = await probe.say("Did that first background task finish?")
        out["status_reply"] = await probe.wait_turn(status, "status answer", timeout=60)
        require(any(t["name"] in ("check_tasks", "get_context") for t in probe.tools(status)), "status not read from tracked jobs")
    finally:
        await probe.close()
        out["session"] = probe.report()
    return out


async def scenario_e(url: str, timeout: float, out: dict) -> dict:
    title = unique("ClawVoice Block")
    renamed = title + " Moved"
    tomorrow = datetime.now(TZ).date() + timedelta(days=1)
    out.update(title=title, renamed=renamed, date=tomorrow.isoformat())
    probe = Probe(url, timeout)
    try:
        await probe.connect()
        await probe.wait_turn(0, "greeting")
        offset = await probe.say(
            f"Add a local planner block tomorrow from 2 to 3 PM called {title}. It's a planner block, not a Calendar event."
        )
        job = await wait_job(probe, offset, "planner add", 300)
        out["job"] = job
        require(job["status"] == "completed" and job["verification"] == "verified", f"planner add not verified: {job}")
        await probe.wait_turn(probe.mark(), "announcement", timeout=90)
        ask = await probe.say("What's on my planner tomorrow?")
        out["after_write_reply"] = await probe.wait_turn(ask, "planner tomorrow after write")
        read = [t for t in probe.tools(ask) if t["name"] == "get_context" and t.get("topic") == "planner.tomorrow"]
        require(read and read[0]["status"] == "fresh", "post-write read not fresh")
        out["after_write_cache"] = read[0].get("cache")
        state = await api(url, "/api/state?agent=tari")
        row = next(r for r in state["cache"]["topics"] if r["topic"] == "planner.tomorrow")
        require(norm(title) in norm(row["summary"]), "cache does not reflect the verified write")
        changed = manage("planner", "rename", tomorrow.isoformat(), title, renamed)
        require(changed.returncode == 0, "out-of-band rename failed")
        started = time.monotonic()
        while time.monotonic() - started < 200:
            state = await api(url, "/api/state?agent=tari")
            row = next(r for r in state["cache"]["topics"] if r["topic"] == "planner.tomorrow")
            if norm(renamed) in norm(row["summary"] or ""):
                break
            await asyncio.sleep(2)
        out["background_refresh_seconds"] = round(time.monotonic() - started, 1)
        require(norm(renamed) in norm(row["summary"]), "background refresh did not pick up the external change")
        ask = await probe.say("What's on my planner tomorrow now?")
        out["after_external_change_reply"] = await probe.wait_turn(ask, "planner after external change")
        reread = [t for t in probe.tools(ask) if t["name"] == "get_context" and t.get("topic") == "planner.tomorrow"]
        out["after_external_change_tool"] = reread[:1]
        require(reread, "repeated question was answered from memory instead of a fresh get_context")
        require("moved" in norm(out["after_external_change_reply"]), "spoken answer missed the external change")
        remove = await probe.say(f"Remove the planner block called {renamed} from tomorrow.")
        removal = await wait_job(probe, remove, "planner remove", 300)
        out["remove_job"] = removal
        require(removal["status"] == "completed" and removal["verification"] == "verified", f"remove not verified: {removal}")
        await probe.wait_turn(probe.mark(), "removal announcement", timeout=90)
    finally:
        await probe.close()
        out["session"] = probe.report()
        await settle_jobs(url)
        show = manage("planner", "show", tomorrow.isoformat()).stdout
        if title in show or renamed in show:
            manage("planner", "remove", tomorrow.isoformat(), renamed if renamed in show else title)
            show = manage("planner", "show", tomorrow.isoformat()).stdout
        out["cleanup"] = "absent" if title not in show and renamed not in show else "STILL PRESENT"
    return out


async def scenario_f(url: str, timeout: float, out: dict) -> dict:
    """Portability: the fixture agent Astra through the same runtime (fake Gateway).

    Start first: ``make fixture-gateway`` and ``PORT=7861 make fixture-dev``;
    run with ``--fixture-url http://127.0.0.1:7861 --scenario f``.
    """
    agents = await api(url, "/api/agents")
    require(agents["default"] == "astra", "fixture server is not serving Astra")
    state = await api(url, "/api/state?agent=astra")
    out["transport"] = state["gateway"]["active_transport"]
    probe = Probe(url, timeout)
    try:
        await probe.connect(agent="astra")
        out["greeting"] = await probe.wait_turn(0, "greeting")
        ask = await probe.say("What's the status of my project?")
        out["status_reply"] = await probe.wait_turn(ask, "project status")
        require(any(t["name"] == "get_context" and t.get("topic") == "project.active" for t in probe.tools(ask)),
                "project.active was not read from the hot context")
        run = await probe.say("Please run the tests.")
        job = await wait_job(probe, run, "fixture test run", 120)
        out["job"] = job
        require(job["status"] == "completed" and job["verification"] == "verified", f"test run not verified: {job}")
        out["announcement"] = await probe.wait_turn(probe.mark(), "announcement", timeout=60)
        last = await probe.say("How did the latest tests go?")
        out["tests_reply"] = await probe.wait_turn(last, "latest tests")
        read = [t for t in probe.tools(last) if t["name"] == "get_context" and t.get("topic") == "tests.last"]
        require(read and read[0]["status"] == "fresh", "tests.last not read fresh after the verified run")
        require("42" in out["tests_reply"], "answer does not reflect the verified test run")
    finally:
        await probe.close()
        out["session"] = probe.report()
    return out


async def scenario_g(url: str, timeout: float, out: dict) -> dict:
    """Clarification for an ambiguous request, then a follow-up Calendar update."""
    title = unique("ClawVoice Test")
    tomorrow = datetime.now(TZ).date() + timedelta(days=1)
    out.update(title=title, date=tomorrow.isoformat())
    require(not [e for e in calendar_events_on(tomorrow) if e.get("summary") == title], "title already exists")
    probe = Probe(url, timeout)
    try:
        await probe.connect()
        await greet(probe, url)
        vague = await probe.say("Schedule a meeting tomorrow.")
        out["clarification"] = await probe.wait_turn(vague, "clarifying question", timeout=60)
        out["clarification_delegated"] = [t["name"] for t in probe.tools(vague) if t["name"] == "delegate_task"]
        require(not out["clarification_delegated"], "an ambiguous request was delegated instead of clarified")
        require("?" in out["clarification"], "no clarifying question was asked")
        create = await probe.say(f"Never mind that. Create a Google Calendar event tomorrow from 3 to 4 PM called {title}.")
        job = await wait_job(probe, create, "calendar create", 360)
        require(job["status"] == "completed" and job["verification"] == "verified", f"create not verified: {job}")
        await probe.wait_turn(probe.mark(), "create announcement", timeout=90)
        created = [e for e in calendar_events_on(tomorrow) if e.get("summary") == title]
        require(len(created) == 1, "created event not found")
        created_id = created[0]["id"]
        move = await probe.say("Move that event to 4 to 5 PM instead.")
        update = await wait_job(probe, move, "calendar update", 360)
        out["update_job"] = update
        require(update["status"] == "completed" and update["verification"] == "verified", f"update not verified: {update}")
        out["update_announcement"] = await probe.wait_turn(probe.mark(), "update announcement", timeout=90)
        event = next(e for e in calendar_events_on(tomorrow) if e.get("id") == created_id)
        start = datetime.fromisoformat(event["start"]["dateTime"].replace("Z", "+00:00")).astimezone(TZ)
        end = datetime.fromisoformat(event["end"]["dateTime"].replace("Z", "+00:00")).astimezone(TZ)
        out["readback_after_update"] = {"start_local": start.isoformat(), "end_local": end.isoformat(),
                                        "same_event": True, "title": event.get("summary") == title}
        require((start.hour, end.hour) == (16, 17), "event was not moved to 4-5 PM Toronto")
        delete = await probe.say(f"Now delete the {title} event.")
        removal = await wait_job(probe, delete, "calendar delete", 360)
        require(removal["status"] == "completed" and removal["verification"] == "verified", f"delete not verified: {removal}")
        await probe.wait_turn(probe.mark(), "delete announcement", timeout=90)
        require(not [e for e in calendar_events_on(tomorrow) if e.get("id") == created_id], "event still present")
        out["cleanup"] = "deleted through voice and re-checked absent"
    finally:
        await probe.close()
        out["session"] = probe.report()
        if "cleanup" not in out:  # the voice delete did not finish
            out["cleanup"] = await remove_disposable_events(url, tomorrow, title)
    return out


async def scenario_h(url: str, timeout: float, out: dict) -> dict:
    """Checklist mutation by voice, reflected by the cached checklist topic, then removed."""
    task, section = unique("ClawVoice Task"), unique("ClawVoice Probe")
    out.update(task=task, section=section)
    before = manage("planner", "checklist", "--json", "--all").stdout
    require(task not in before and section not in before, "task or section already exists")
    probe = Probe(url, timeout)
    try:
        await probe.connect()
        await greet(probe, url)
        add = await probe.say(
            f"Add a checklist task called {task} with priority status, in a new checklist section called {section}."
        )
        job = await wait_job(probe, add, "checklist add", 300)
        out["job"] = job
        require(job["status"] == "completed" and job["verification"] == "verified", f"checklist add not verified: {job}")
        await probe.wait_turn(probe.mark(), "announcement", timeout=90)
        ask = await probe.say("What are my priority checklist tasks?")
        out["reply"] = await probe.wait_turn(ask, "checklist answer")
        read = [t for t in probe.tools(ask) if t["name"] == "get_context" and t.get("topic") == "checklist.focus"]
        require(read and read[0]["status"] == "fresh", "checklist.focus not read fresh")
        require(all(w in norm(out["reply"]) for w in norm(task).split()[2:]), "answer missed the new task")
        remove = await probe.say(f"Remove the checklist task {task}, and then remove the empty {section} section.")
        removal = await wait_job(probe, remove, "checklist remove", 300)
        out["remove_job"] = removal
        require(removal["status"] == "completed", f"removal: {removal}")
        await probe.wait_turn(probe.mark(), "removal announcement", timeout=90)
    finally:
        await probe.close()
        out["session"] = probe.report()
        await settle_jobs(url)
        if task in manage("planner", "checklist", "--json", "--all").stdout:
            manage("planner", "task-remove", task)
        if section in manage("planner", "checklist", "--json", "--all").stdout:
            manage("planner", "section-remove", section)  # refuses a section that still has tasks
        final = manage("planner", "checklist", "--json", "--all").stdout
        out["cleanup"] = "absent" if task not in final and section not in final else "STILL PRESENT"
    return out


REFUSAL = re.compile(r"can.?t access|cannot access|don.?t have access|no access|only (?:see|have)|my cache|cache (?:only|ends)")
OPENCLAW_DB = REPO / "infra/openclaw/.local/state/agents/tari/agent/openclaw-agent.sqlite"
VOICE_SESSION_KEY = "agent:tari:clawvoice-local-owner"


def openclaw_commands(since_ms: int) -> list[str]:
    """Commands Tari's OpenClaw agent ran since a time, read-only from its own transcript."""
    db = sqlite3.connect(f"file:{OPENCLAW_DB}?mode=ro", uri=True)
    try:
        row = db.execute("select current_session_id from session_nodes where session_key=?", (VOICE_SESSION_KEY,)).fetchone()
        require(row, "ClawVoice OpenClaw session not found")
        events = db.execute("select event_json from transcript_events where session_id=? and created_at>=? "
                            "and event_json is not null order by seq", (row[0], since_ms)).fetchall()
    finally:
        db.close()
    commands = []
    for (raw,) in events:
        message = json.loads(raw).get("message") or {}
        for part in message.get("content") or [] if isinstance(message, dict) else []:
            if isinstance(part, dict) and part.get("type") == "toolCall":
                arguments = part.get("arguments") or {}
                commands.append(str(arguments.get("code") or arguments.get("command") or arguments))
    return commands


async def scenario_i(url: str, timeout: float, out: dict) -> dict:
    """The cache is an accelerator, not a boundary: covered questions are cache hits;
    anything outside coverage becomes a read-only OpenClaw lookup, verified in
    OpenClaw's own transcript. A disposable event in January 2027 proves real results."""
    title = unique("ClawVoice Jan Probe")
    day = date(2027, 1, 14)
    tomorrow = datetime.now(TZ).date() + timedelta(days=1)
    out.update(title=title, date=day.isoformat())
    require(not [e for e in calendar_events_on(day) if e.get("summary") == title], "title already exists")
    manage("google", "calendar", "manage", "create", "primary", "--summary", title,
           "--from", f"{day.isoformat()}T10:00:00-05:00", "--to", f"{day.isoformat()}T11:00:00-05:00", "--send-updates", "none")
    require(len([e for e in calendar_events_on(day) if e.get("summary") == title]) == 1, "January probe event not created")
    probe = Probe(url, timeout)
    try:
        await probe.connect()
        await greet(probe, url)
        # 1. Covered by the cache: instant answer, no OpenClaw run.
        jobs_before = len((await api(url, "/api/state?agent=tari"))["jobs"])
        ask = await probe.say("What's on my Calendar today?")
        out["today_reply"] = await probe.wait_turn(ask, "today from cache")
        reads = [t for t in probe.tools(ask, "get_context") if t.get("topic") in ("calendar.today", "planner.today")]
        out["today_tools"] = [(t["name"], t.get("topic"), t.get("status"), t.get("cache")) for t in probe.tools(ask)]
        require(reads and reads[0]["status"] == "fresh", "today's Calendar was not answered from a fresh cache read")
        require(not probe.tools(ask, "delegate_task"), "a covered question started an OpenClaw job")
        require(len((await api(url, "/api/state?agent=tari"))["jobs"]) == jobs_before, "a job was created for a cached answer")
        # 2. Outside coverage: January 2027 must be looked up live, not refused.
        since = int(time.time() * 1000) - 1000
        ask = await probe.say("What events do I have in January 2027?")
        job = await wait_job(probe, ask, "January lookup", 300)
        out["january_job"] = {k: job.get(k) for k in ("kind", "target", "status", "outcome_text", "transport")}
        out["january_ack"] = probe.bot_text(ask)
        require(job["kind"] == "question", "the lookup was not delegated as a read-only question")
        require(job["status"] == "completed", f"January lookup did not complete: {job.get('outcome_text')}")
        require(not REFUSAL.search(norm(out["january_ack"])), "Tari claimed it could not access January")
        bird = title.split()[-2:]
        require(all(w.lower() in norm(job["outcome_text"]) for w in bird), "January answer missed the real event")
        commands = openclaw_commands(since)
        out["january_commands"] = [c[:200] for c in commands]
        require(any("2027-01" in c and ("tari-google" in c or "day-planner" in c) for c in commands),
                "OpenClaw did not run a Calendar query for January 2027")
        out["january_announcement"] = await probe.wait_turn(probe.mark(), "January announcement", timeout=90)
        require(all(w.lower() in norm(out["january_announcement"]) for w in bird), "spoken answer missed the event")
        # 3. Explicit live check of a covered day still goes to OpenClaw.
        since = int(time.time() * 1000) - 1000
        ask = await probe.say("Can you actually check Google Calendar live, not the cache, for tomorrow?")
        live = await wait_job(probe, ask, "explicit live check", 300)
        out["live_job"] = {k: live.get(k) for k in ("kind", "status", "outcome_text")}
        require(live["kind"] == "question" and live["status"] == "completed", "explicit live check did not run")
        commands = openclaw_commands(since)
        out["live_commands"] = [c[:200] for c in commands]
        require(any(("tari-google" in c or "day-planner" in c) and (tomorrow.isoformat() in c or "tomorrow" in c)
                    for c in commands), "OpenClaw did not query tomorrow's Calendar")
        await probe.wait_turn(probe.mark(), "live check announcement", timeout=90)
        # 5. Broad question: clarify or a clearly bounded live search, never "nothing else" from the cache.
        ask = await probe.say("Are there any other events in my Calendar?")
        out["broad_reply"] = await probe.wait_turn(ask, "broad question", timeout=60)
        delegated = probe.tools(ask, "delegate_task")
        out["broad_delegated"] = bool(delegated)
        require(delegated or "?" in out["broad_reply"], "broad question was neither clarified nor looked up")
        require(not REFUSAL.search(norm(out["broad_reply"])), "broad answer treated the cache as the limit")
        if delegated:
            await settle_jobs(url)
    finally:
        await probe.close()
        out["session"] = probe.report()
        await settle_jobs(url)
        out["cleanup"] = await remove_disposable_events(url, day, title)
    return out


SCENARIOS = {
    "a": scenario_a, "b": scenario_b, "c": scenario_c, "d": scenario_d, "e": scenario_e,
    "f": scenario_f, "g": scenario_g, "h": scenario_h, "i": scenario_i,
}


async def main(url: str, names: list[str], timeout: float, fixture_url: str) -> int:
    failures = 0
    for name in names:
        started = time.monotonic()
        report: dict = {"scenario": name, "started": datetime.now(timezone.utc).isoformat()}
        out: dict = {}
        report["result"] = out
        try:
            await SCENARIOS[name](fixture_url if name == "f" else url, timeout, out)
            report["status"] = "PASS"
        except Exception as error:  # noqa: BLE001 - reported per scenario
            report["status"] = "FAIL"
            report["error"] = f"{type(error).__name__}: {error}"
            failures += 1
        report["seconds"] = round(time.monotonic() - started, 1)
        path = CAPTURES / f"probe-{name}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
        path.write_text(json.dumps(report, indent=1, default=str))
        summary = {k: v for k, v in (report.get("result") or {}).items() if not k.startswith("session")}
        print(json.dumps({"scenario": name, "status": report["status"], "seconds": report["seconds"],
                          "error": report.get("error"), "summary": summary}, indent=1, default=str)[:6000], flush=True)
        print(f"report: {path}", flush=True)
        await asyncio.sleep(1.5)
    return 1 if failures else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--live", action="store_true", help="Explicitly allow live Boson/OpenClaw/Google calls")
    parser.add_argument("--url", default="http://127.0.0.1:7860")
    parser.add_argument("--fixture-url", default="http://127.0.0.1:7861", help="Server running the fixture agent (f)")
    parser.add_argument("--scenario", default="all", help="a-i, comma separated, or all (= a-e, g, h, i; f needs the fixture servers)")
    parser.add_argument("--timeout", type=float, default=90)
    args = parser.parse_args()
    if not args.live:
        parser.error("Pass --live to run live tests; --help makes no connection")
    chosen = list("abcdeghi") if args.scenario == "all" else [s.strip() for s in args.scenario.split(",")]
    raise SystemExit(asyncio.run(main(args.url.rstrip("/"), chosen, args.timeout, args.fixture_url.rstrip("/"))))
