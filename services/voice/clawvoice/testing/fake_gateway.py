"""A small, protocol-level fake OpenClaw Gateway for offline tests and fixtures.

Implements the subset ClawVoice uses, on one aiohttp port like the real
Gateway (WS + HTTP multiplexed):

- WS protocol v4: connect.challenge -> connect (shared token + Ed25519 device
  signature, verified cryptographically), optional pairing requirement,
  chat.send (idempotency keys), chat events (delta/final/error/aborted),
  ``agent`` tool events, agent.wait, chat.abort, chat.history;
- HTTP: GET /healthz and streaming POST /v1/chat/completions (a dropped HTTP
  client cancels its run, as documented for the real Gateway).

The scripted "agent" reacts to keywords in the request so tests can produce
answers, receipts, delays, failures and test runs deterministically. It is a
test double: nothing here talks to a model, Google or OpenClaw.

    python -m clawvoice.testing.fake_gateway --port 18999   # fixture agent demo
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import re
import secrets
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from aiohttp import WSMsgType, web
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from clawvoice.openclaw.device import auth_payload_v3
from clawvoice.testing import fixture_helper

METHODS = ["chat.send", "chat.abort", "chat.history", "agent.wait", "sessions.list"]


def _b64url_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


@dataclass
class FakeRun:
    run_id: str
    session_key: str
    message: str
    status: str = "running"  # running | ok | error | aborted
    text: str = ""
    error: str | None = None
    done: asyncio.Event = field(default_factory=asyncio.Event)
    subscribers: set = field(default_factory=set)
    task: asyncio.Task | None = None
    seq: int = 0


class FakeGateway:
    def __init__(
        self,
        *,
        token: str,
        require_pairing: bool = False,
        granted_scopes: tuple[str, ...] = ("operator.read", "operator.write"),
        delay_scale: float = 1.0,
    ):
        self.token = token
        self.require_pairing = require_pairing
        self.granted_scopes = list(granted_scopes)
        self.delay_scale = delay_scale
        self.approved_devices: set[str] = set()
        self.pairing_requests: dict[str, str] = {}
        self.runs: dict[str, FakeRun] = {}
        self.idempotency: dict[str, str] = {}
        self.history: dict[str, list[dict[str, Any]]] = {}
        self.runs_started = 0
        self.drop_next_after_send = False
        self.drop_next_before_reply = False
        self.http_fail_status: int | None = None
        self._http_requests: dict[int, web.Request] = {}
        self.sockets: set[web.WebSocketResponse] = set()
        self.app = web.Application()
        self.app.router.add_get("/healthz", self._health)
        self.app.router.add_get("/", self._websocket)
        self.app.router.add_post("/v1/chat/completions", self._completions)
        self._runner: web.AppRunner | None = None
        self.port: int | None = None

    # ------------------------------------------------------------ lifecycle
    async def start(self, port: int = 0) -> int:
        self._runner = web.AppRunner(self.app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", port)
        await site.start()
        self.port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
        return self.port

    async def stop(self) -> None:
        for run in self.runs.values():
            if run.task and not run.task.done():
                run.task.cancel()
        for ws in list(self.sockets):
            await ws.close()
        if self._runner:
            await self._runner.cleanup()

    def approve(self, device_id: str) -> None:
        self.approved_devices.add(device_id)

    async def drop_all_sockets(self) -> None:
        for ws in list(self.sockets):
            await ws.close()

    def break_http_streams(self) -> None:
        """Cut streaming HTTP responses mid-run without an error chunk (network loss)."""
        for request in list(self._http_requests.values()):
            if request.transport is not None:
                request.transport.close()

    # ------------------------------------------------------------ the scripted agent
    async def _behave(self, run: FakeRun) -> None:
        message = run.message
        request = re.search(r"^Request: (.*)$", message, flags=re.M)
        text_in = (request.group(1) if request else message).lower()
        delay = re.search(r"(?:delay|wait|sleep)\D{0,12}(\d+(?:\.\d+)?)\s*s", text_in)
        try:
            await self._emit_tool(run, "read", "start")
            if delay:
                await asyncio.sleep(float(delay.group(1)) * self.delay_scale)
            if "fail" in text_in:
                run.status, run.error = "error", "Scripted failure"
                await self._chat(run, {"state": "error", "errorMessage": run.error, "errorKind": "unknown"})
                return
            if re.search(r"\brun\b.{0,40}\btest", text_in):
                test_id = "R" + secrets.token_hex(3).upper()
                await self._emit_tool(run, "exec", "start", command="pytest -q tests")
                await asyncio.to_thread(fixture_helper.project, ["record-test-run", test_id, "passed", "42", "42"])
                reply = "All 42 tests passed."
                receipt = {"status": "done", "summary": reply,
                           "changes": [{"type": "test_run", "action": "completed", "run_id": test_id}]}
            elif "scripted calendar event" in text_in:
                event_id = re.search(r"scripted calendar event (\w+)", text_in).group(1)
                reply = "Added it to your Calendar."
                receipt = {"status": "done", "summary": reply, "changes": [
                    {"type": "calendar_event", "action": "created", "calendar_id": "primary", "event_id": event_id}]}
            elif "no receipt" in text_in:
                reply, receipt = "I did it, trust me.", None
            elif "claim change" in text_in:
                reply = "I created it."
                receipt = {"status": "done", "summary": reply,
                           "changes": [{"type": "test_run", "action": "completed", "run_id": "RNOPE00"}]}
            elif "clarify" in text_in:
                reply = "Which test suite do you mean?"
                receipt = {"status": "needs_clarification", "summary": reply, "question": reply, "changes": []}
            else:
                reply = f"Fixture answer about: {text_in[:80]}"
                receipt = {"status": "no_change", "summary": reply, "changes": []}
            full = reply + (f"\nCLAWVOICE_RECEIPT {json.dumps(receipt)}" if receipt else "")
            for index in range(0, len(full), 24):
                await self._chat(run, {"state": "delta", "deltaText": full[index:index + 24]})
                await asyncio.sleep(0)
            run.status, run.text = "ok", full
            self.history.setdefault(run.session_key, []).append(
                {"role": "assistant", "content": [{"type": "text", "text": full}]}
            )
            await self._chat(run, {"state": "final", "message": {"role": "assistant", "content": [{"type": "text", "text": full}]}})
        except asyncio.CancelledError:
            run.status = "aborted"
            await self._chat(run, {"state": "aborted"})
        finally:
            run.done.set()

    async def _emit_tool(self, run: FakeRun, name: str, phase: str, command: str | None = None) -> None:
        data: dict[str, Any] = {"name": name, "phase": phase}
        if command:
            data["args"] = {"command": command}
        await self._broadcast(run, {"type": "event", "event": "agent",
                                    "payload": {"runId": run.run_id, "stream": "tool", "data": data}})

    async def _chat(self, run: FakeRun, payload: dict[str, Any]) -> None:
        run.seq += 1
        await self._broadcast(run, {"type": "event", "event": "chat",
                                    "payload": {"runId": run.run_id, "sessionKey": run.session_key, "seq": run.seq, **payload}})

    async def _broadcast(self, run: FakeRun, frame: dict[str, Any]) -> None:
        for ws in list(run.subscribers):
            if ws.closed:
                run.subscribers.discard(ws)
                continue
            try:
                await ws.send_json(frame)
            except (ConnectionError, RuntimeError):
                run.subscribers.discard(ws)
        for queue in list(getattr(run, "_queues", [])):
            queue.put_nowait(frame)

    def _start_run(self, session_key: str, message: str, idempotency_key: str | None) -> tuple[FakeRun, bool]:
        if idempotency_key and idempotency_key in self.idempotency:
            return self.runs[self.idempotency[idempotency_key]], True
        run = FakeRun(run_id="run-" + uuid.uuid4().hex[:12], session_key=session_key, message=message)
        self.runs[run.run_id] = run
        if idempotency_key:
            self.idempotency[idempotency_key] = run.run_id
        self.history.setdefault(session_key, []).append({"role": "user", "content": message})
        self.runs_started += 1
        run.task = asyncio.create_task(self._behave(run))
        return run, False

    # ------------------------------------------------------------ HTTP
    async def _health(self, request: web.Request) -> web.Response:
        return web.json_response({"ok": True})

    def _authorized(self, request: web.Request) -> bool:
        return request.headers.get("Authorization") == f"Bearer {self.token}"

    async def _completions(self, request: web.Request) -> web.StreamResponse:
        if not self._authorized(request):
            return web.json_response({"error": {"message": "unauthorized"}}, status=401)
        if self.http_fail_status:
            return web.json_response({"error": {"message": "scripted upstream failure"}}, status=self.http_fail_status)
        body = await request.json()
        session_key = request.headers.get("x-openclaw-session-key") or f"agent:fixture:http-{uuid.uuid4().hex[:6]}"
        message = body["messages"][-1]["content"]
        run, _ = self._start_run(session_key, message, None)
        queue: asyncio.Queue = asyncio.Queue()
        run._queues = [queue]  # type: ignore[attr-defined]
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream; charset=utf-8"})
        await response.prepare(request)
        self._http_requests[id(request)] = request
        chunk_id = "chatcmpl-" + uuid.uuid4().hex[:10]

        async def send(obj: Any) -> None:
            await response.write(f"data: {obj if isinstance(obj, str) else json.dumps(obj)}\n\n".encode())

        try:
            await send({"id": chunk_id, "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {"role": "assistant"}}]})
            while True:
                frame = await queue.get()
                if frame.get("event") != "chat":
                    continue
                payload = frame["payload"]
                if payload["state"] == "delta":
                    await send({"id": chunk_id, "object": "chat.completion.chunk",
                                "choices": [{"index": 0, "delta": {"content": payload["deltaText"]}}]})
                elif payload["state"] == "final":
                    await send({"id": chunk_id, "object": "chat.completion.chunk",
                                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
                    await send("[DONE]")
                    break
                elif payload["state"] in ("error", "aborted"):
                    await send({"error": {"message": payload.get("errorMessage") or payload["state"]}})
                    await send("[DONE]")
                    break
        except (ConnectionResetError, asyncio.CancelledError):
            # Documented Gateway behavior: a disconnecting HTTP client cancels the run.
            if run.task and not run.task.done():
                run.task.cancel()
            raise
        finally:
            self._http_requests.pop(id(request), None)
        return response

    # ------------------------------------------------------------ WebSocket
    async def _websocket(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(max_msg_size=25 * 1024 * 1024)
        await ws.prepare(request)
        self.sockets.add(ws)
        nonce, ts = secrets.token_hex(16), int(time.time() * 1000)
        await ws.send_json({"type": "event", "event": "connect.challenge", "payload": {"nonce": nonce, "ts": ts}})
        authed = False
        try:
            async for msg in ws:
                if msg.type != WSMsgType.TEXT:
                    continue
                frame = json.loads(msg.data)
                if frame.get("type") != "req":
                    continue
                request_id, method, params = frame.get("id"), frame.get("method"), frame.get("params") or {}

                async def reply(payload: Any = None, error: dict | None = None) -> None:
                    message = {"type": "res", "id": request_id, "ok": error is None}
                    message["payload" if error is None else "error"] = payload if error is None else error
                    await ws.send_json(message)

                if method == "connect":
                    error = self._check_connect(params, nonce, ts)
                    if error:
                        await reply(error=error)
                        continue
                    authed = True
                    await reply({"type": "hello-ok", "protocol": 4, "server": {"version": "fake-2026.9.9", "connId": "c1"},
                                 "features": {"methods": METHODS, "events": ["chat", "agent", "tick"]}, "snapshot": {},
                                 "auth": {"role": "operator", "scopes": self.granted_scopes}, "policy": {"tickIntervalMs": 30000}})
                    continue
                if not authed:
                    await reply(error={"code": "UNAUTHORIZED", "message": "connect first"})
                    continue
                if method == "chat.send":
                    run, duplicate = self._start_run(params["sessionKey"], params["message"], params.get("idempotencyKey"))
                    run.subscribers.add(ws)
                    if self.drop_next_before_reply:
                        # The run started, but the client never hears the acknowledgement.
                        self.drop_next_before_reply = False
                        await ws.close()
                        break
                    await reply({"runId": run.run_id, "status": "started", **({"deduplicated": True} if duplicate else {})})
                    if self.drop_next_after_send:
                        self.drop_next_after_send = False
                        await ws.close()
                        break
                elif method == "agent.wait":
                    run = self.runs.get(params.get("runId", ""))
                    if run is None:
                        await reply(error={"code": "INVALID_REQUEST", "message": "agent run was not found"})
                        continue
                    try:
                        await asyncio.wait_for(run.done.wait(), timeout=int(params.get("timeoutMs", 1000)) / 1000)
                    except asyncio.TimeoutError:
                        await reply({"status": "timeout"})
                        continue
                    status = {"ok": "ok", "error": "error", "aborted": "cancelled"}.get(run.status, "error")
                    await reply({"status": status, "error": run.error,
                                 "terminalReply": {"disposition": "visible", "text": run.text} if run.text else {"disposition": "empty"}})
                elif method == "chat.abort":
                    run = self.runs.get(params.get("runId", ""))
                    if run and run.status == "running" and run.task:
                        run.task.cancel()
                        await reply({"ok": True, "aborted": True, "runIds": [run.run_id]})
                    else:
                        await reply({"ok": True, "aborted": False, "runIds": []})
                elif method == "chat.history":
                    await reply({"messages": self.history.get(params.get("sessionKey", ""), [])[-int(params.get("limit", 20)):]})
                else:
                    await reply(error={"code": "UNKNOWN_METHOD", "message": f"unknown method {method}"})
        finally:
            self.sockets.discard(ws)
        return ws

    def _check_connect(self, params: dict[str, Any], nonce: str, ts: int) -> dict | None:
        if (params.get("auth") or {}).get("token") != self.token:
            return {"code": "AUTH_TOKEN_MISMATCH", "message": "unauthorized"}
        device = params.get("device")
        if not isinstance(device, dict):
            return {"code": "NOT_PAIRED", "message": "device identity required",
                    "details": {"code": "PAIRING_REQUIRED"}}
        client = params.get("client") or {}
        payload = auth_payload_v3(
            device_id=device.get("id", ""), client_id=client.get("id", ""), client_mode=client.get("mode", ""),
            role=params.get("role", ""), scopes=list(params.get("scopes") or []), signed_at_ms=device.get("signedAt", -1),
            token=self.token, nonce=device.get("nonce", ""), platform=client.get("platform"),
            device_family=client.get("deviceFamily"),
        )
        try:
            key = Ed25519PublicKey.from_public_bytes(_b64url_decode(device.get("publicKey", "")))
            key.verify(_b64url_decode(device.get("signature", "")), payload.encode())
        except (InvalidSignature, ValueError):
            return {"code": "INVALID_REQUEST", "message": "device signature invalid",
                    "details": {"code": "DEVICE_AUTH_SIGNATURE_INVALID"}}
        if device.get("nonce") != nonce or device.get("signedAt") != ts:
            return {"code": "INVALID_REQUEST", "message": "device nonce mismatch",
                    "details": {"code": "DEVICE_AUTH_NONCE_MISMATCH"}}
        if self.require_pairing and device["id"] not in self.approved_devices:
            request_id = self.pairing_requests.setdefault(device["id"], str(uuid.uuid4()))
            return {"code": "NOT_PAIRED", "message": "pairing required: device is not approved yet",
                    "details": {"code": "PAIRING_REQUIRED", "requestId": request_id, "deviceId": device["id"]}}
        return None


async def _serve(port: int, token: str) -> None:
    gateway = FakeGateway(token=token)
    await gateway.start(port)
    print(f"Fake OpenClaw Gateway (fixtures only) on 127.0.0.1:{gateway.port}", flush=True)
    await asyncio.Event().wait()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=18999)
    parser.add_argument("--token-env", default="CLAWVOICE_FIXTURE_GATEWAY_TOKEN")
    args = parser.parse_args()
    token = os.environ.get(args.token_env)
    if not token:
        raise SystemExit(f"Set {args.token_env} (any random string) for the fixture Gateway")
    asyncio.run(_serve(args.port, token))


if __name__ == "__main__":
    main()
