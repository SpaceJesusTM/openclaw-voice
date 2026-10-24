"""Test the official runner over WebRTC/RTVI, including actual decoded audio."""

import argparse
import asyncio
import json
import time
from datetime import datetime, timezone

import aiohttp
from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription
from aiortc.mediastreams import AudioStreamTrack, MediaStreamError

from config import MODEL, ROOT, utf8_console
from probe_audio import WavMicrophone


def rtvi(kind, data):
    return json.dumps({"id": f"probe-{time.monotonic_ns()}", "label": "rtvi-ai", "type": kind, "data": data})


class Evidence:
    def __init__(self):
        self.started = time.monotonic()
        self.events = []
        self.model = None
        self.failure = None
        self.media_frames = 0
        self.nonzero_media_frames = 0
        self.allow_cancelled = False

    def receive(self, raw):
        message = json.loads(raw)
        self.events.append({"elapsed_ms": round((time.monotonic() - self.started) * 1000), **message})
        data = message.get("data") or {}
        if message.get("type") == "error":
            self.failure = str(data.get("error", data))
        if message.get("type") != "server-message":
            return
        if data.get("kind") == "preview-model" and data.get("acknowledged"):
            self.model = data["acknowledged"]
        event = data.get("event", {})
        if event.get("type") in ("session.created", "session.updated"):
            self.model = event.get("session", {}).get("model")
            if self.model != MODEL:
                self.failure = f"Unverified model: {self.model!r}"
        if event.get("type") == "error" and event.get("error", {}).get("code") not in (
            "response_not_active",
            "response_id_mismatch",
        ):
            self.failure = f"Boson API error: {event.get('error')}"
        if (
            event.get("type") == "response.done"
            and event.get("response", {}).get("status") != "completed"
            and not (self.allow_cancelled and event.get("response", {}).get("status") == "cancelled")
        ):
            self.failure = f"Unexpected response status: {event.get('response', {}).get('status')}"

    def turn_evidence(self, offset, kind):
        events = self.events[offset:]
        text_events = [
            (i, event["data"].get("text", ""))
            for i, event in enumerate(events)
            if event.get("type") == "bot-llm-text"
        ]
        text = "".join(text for _, text in text_events).strip()
        stopped = [i for i, event in enumerate(events) if event.get("type") == "bot-stopped-speaking"]
        diagnostics = [event.get("data", {}) for event in events if event.get("type") == "server-message"]
        wire = [item["event"] for item in diagnostics if item.get("kind") == "boson.recv"]
        completed = any(
            event.get("type") == "response.done" and event.get("response", {}).get("status") == "completed"
            for event in wire
        )
        results = [item for item in diagnostics if item.get("kind") == "tool.result"]
        valid = bool(text_events and text and stopped and stopped[-1] > text_events[-1][0] and completed)
        if kind == "planner":

            def normalize(text):
                return "".join(c for c in text.lower() if c.isalnum())

            valid = valid and any(not item.get("result", {}).get("error") for item in results)
            fixture = json.loads(
                (ROOT.parent / "higgs-realtime-1.5/fixtures/planner.json").read_text(encoding="utf-8")
            )
            valid = valid and normalize(fixture["entries"][0]["title"]) in normalize(text)
        if kind == "search":
            calls = {
                event.get("item", {}).get("call_id")
                for event in wire
                if event.get("item", {}).get("name") == "web_search"
                and event.get("item", {}).get("executor") == "server"
            }
            result_positions = [
                i
                for i, message in enumerate(events)
                if message.get("type") == "server-message"
                and message.get("data", {}).get("event", {}).get("item", {}).get("type")
                == "function_call_output"
                and message["data"]["event"]["item"].get("call_id") in calls
            ]
            # The preamble can finish playing AFTER the result arrives. It is
            # not the answer. Require a subsequent completed response and speech.
            if result_positions:
                result_position = result_positions[-1]
                answer = [(i, text) for i, text in text_events if i > result_position]
                answer_completed = any(
                    i > result_position
                    and message.get("data", {}).get("event", {}).get("type") == "response.done"
                    and message["data"]["event"]["response"].get("status") == "completed"
                    for i, message in enumerate(events)
                )
                valid = valid and bool(answer and answer_completed and stopped[-1] > answer[-1][0])
            else:
                valid = False
        return valid, text, results


async def wait_for(predicate, evidence, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if evidence.failure:
            raise RuntimeError(evidence.failure)
        if predicate():
            return
        await asyncio.sleep(0.05)
    raise TimeoutError(f"Required evidence not received within {timeout:g} seconds")


async def run(url, timeout=60, search=None, audio_wav=None, barge_wav=None):
    evidence = Evidence()
    pc = RTCPeerConnection(RTCConfiguration(iceServers=[]))
    microphone = WavMicrophone() if audio_wav or barge_wav else AudioStreamTrack()
    pc.addTrack(microphone)
    channel = pc.createDataChannel("rtvi")
    tasks = set()
    closing = False
    checks = []

    @channel.on("open")
    def channel_open():
        print("WebRTC data channel open; sending client-ready", flush=True)
        channel.send(rtvi("client-ready", {"version": "1.0.0"}))

    @channel.on("message")
    def message(raw):
        try:
            evidence.receive(raw)
        except (ValueError, TypeError, KeyError) as error:
            evidence.failure = f"Invalid RTVI message: {type(error).__name__}"

    @pc.on("track")
    def track_received(track):
        async def consume():
            try:
                while True:
                    frame = await track.recv()
                    if track.kind == "audio":
                        evidence.media_frames += 1
                        if abs(frame.to_ndarray().astype("int32")).max() > 2:
                            evidence.nonzero_media_frames += 1
            except MediaStreamError:
                pass

        task = asyncio.create_task(consume())
        tasks.add(task)

    @pc.on("connectionstatechange")
    def state_changed():
        print(f"WebRTC state: {pc.connectionState}", flush=True)
        if pc.connectionState in ("closed", "failed", "disconnected") and not closing:
            evidence.failure = f"WebRTC connection {pc.connectionState}"

    error = None
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as http:
            async with http.post(f"{url}/start", json={"transport": "webrtc"}) as response:
                response.raise_for_status()
                session_id = (await response.json())["sessionId"]
            await pc.setLocalDescription(await pc.createOffer())
            async with http.post(
                f"{url}/sessions/{session_id}/api/offer",
                json={"sdp": pc.localDescription.sdp, "type": "offer"},
            ) as response:
                response.raise_for_status()
                answer = await response.json()
        await pc.setRemoteDescription(RTCSessionDescription(answer["sdp"], answer["type"]))
        await wait_for(
            lambda: (
                evidence.model == MODEL
                and evidence.turn_evidence(0, "greeting")[0]
                and evidence.nonzero_media_frames > 0
            ),
            evidence,
            timeout,
        )
        checks.append({"kind": "greeting", "status": "PASS", "model": evidence.model})
        print(f"PASS connection: acknowledged {evidence.model}; decoded non-silent WebRTC audio")

        for kind, prompt in [
            ("text", "Say: Higgs preview connection verified."),
            ("planner", "What's on my planner today?"),
            *([("search", search)] if search else []),
        ]:
            offset = len(evidence.events)
            media_before = evidence.nonzero_media_frames
            sent_at = time.monotonic()
            channel.send(rtvi("send-text", {"content": prompt, "options": {"audio_response": True}}))
            await wait_for(
                lambda: (
                    evidence.turn_evidence(offset, kind)[0] and evidence.nonzero_media_frames > media_before
                ),
                evidence,
                timeout,
            )
            _, text, results = evidence.turn_evidence(offset, kind)
            events = evidence.events[offset:]
            first_audio = next(
                (
                    event["elapsed_ms"]
                    for event in events
                    if event.get("type") == "server-message"
                    and event.get("data", {}).get("event", {}).get("type") == "response.output_audio.delta"
                ),
                None,
            )
            check = {
                "kind": kind,
                "status": "PASS",
                "prompt": prompt,
                "transcript": text,
                "completion_ms": round((time.monotonic() - sent_at) * 1000),
                "first_boson_audio_ms": round(first_audio - (sent_at - evidence.started) * 1000)
                if first_audio is not None
                else None,
                "nonzero_media_frames": evidence.nonzero_media_frames - media_before,
                "local_results": results,
            }
            checks.append(check)
            print(f"PASS {kind}: {text}")
        if audio_wav:
            offset = len(evidence.events)
            media_before = evidence.nonzero_media_frames
            microphone.play(audio_wav)
            await wait_for(
                lambda: (
                    evidence.turn_evidence(offset, "text")[0] and evidence.nonzero_media_frames > media_before
                ),
                evidence,
                timeout,
            )
            events = evidence.events[offset:]
            wire_types = {event.get("data", {}).get("event", {}).get("type") for event in events}
            if not {"input_audio_buffer.speech_started", "input_audio_buffer.speech_stopped"}.issubset(
                wire_types
            ):
                raise RuntimeError("Audio test missed Boson VAD events")
            transcriptions = [
                event.get("data", {}) for event in events if event.get("type") == "user-transcription"
            ]
            if not any(item.get("final") and item.get("text") for item in transcriptions):
                raise RuntimeError("Audio test missed user transcription")
            checks.append(
                {
                    "kind": "audio-input",
                    "status": "PASS",
                    "transcription": transcriptions,
                    "transcript": evidence.turn_evidence(offset, "text")[1],
                }
            )
            print("PASS synthetic microphone: WebRTC input, server VAD, STT, spoken output")
        if barge_wav:
            evidence.allow_cancelled = True
            offset = len(evidence.events)
            channel.send(
                rtvi(
                    "send-text",
                    {
                        "content": "Count from one to forty slowly, saying every number.",
                        "options": {"audio_response": True},
                    },
                )
            )
            await wait_for(
                lambda: any(
                    event.get("type") == "bot-started-speaking" for event in evidence.events[offset:]
                ),
                evidence,
                timeout,
            )
            await asyncio.sleep(0.3)
            interruption_offset = len(evidence.events)
            microphone.play(barge_wav)
            await wait_for(
                lambda: (
                    evidence.turn_evidence(interruption_offset, "text")[0]
                    and any(
                        event.get("type") == "bot-interrupted"
                        for event in evidence.events[interruption_offset:]
                    )
                ),
                evidence,
                timeout,
            )
            events = evidence.events[interruption_offset:]
            sent = [
                event.get("data", {}).get("event", {})
                for event in events
                if event.get("data", {}).get("kind") == "boson.send"
            ]
            cuts = [event for event in sent if event.get("type") == "conversation.item.truncate"]
            if not cuts:
                raise RuntimeError("Playback interrupted but no server conversation truncation was observed")
            checks.append(
                {
                    "kind": "barge-in",
                    "status": "PASS",
                    "truncations": cuts,
                    "transcript": evidence.turn_evidence(interruption_offset, "text")[1],
                }
            )
            print(
                "PASS synthetic barge-in: user speech, playback interruption, server truncation, new spoken reply"
            )
            evidence.allow_cancelled = False
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        print(f"FAIL: {error}", flush=True)
    finally:
        closing = True
        microphone.stop()
        await pc.close()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        path = ROOT / "captures" / f"probe-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}.json"
        path.parent.mkdir(exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "status": "FAIL" if error else "PASS",
                    "model": evidence.model,
                    "error": error,
                    "checks": checks,
                    "media_frames": evidence.media_frames,
                    "nonzero_media_frames": evidence.nonzero_media_frames,
                    "events": evidence.events,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"Report: {path}")
    return 1 if error else 0


if __name__ == "__main__":
    utf8_console()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live", action="store_true", help="Explicitly allow a live call through the running bot"
    )
    parser.add_argument("--url", default="http://127.0.0.1:7860")
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--search", help="Add a native-search prompt; start bot with BOSON_WEB_SEARCH=true")
    parser.add_argument("--audio-wav", help="Add a spoken planner question from a WAV file")
    parser.add_argument("--barge-wav", help="Speak a WAV interruption over a long response")
    args = parser.parse_args()
    if not args.live:
        parser.error("Use --live to run API tests; --help makes no connection")
    raise SystemExit(
        asyncio.run(run(args.url.rstrip("/"), args.timeout, args.search, args.audio_wav, args.barge_wav))
    )
