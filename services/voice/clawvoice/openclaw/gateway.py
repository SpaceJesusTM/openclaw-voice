"""OpenClaw Gateway WebSocket client built on Pipecat's OpenClawGatewayClient.

Reused from Pipecat (pinned 1.12.0): socket lifecycle, the connect-challenge
handshake loop, request/response correlation, run-id adoption and buffering,
and the ``chat`` event stream folded into per-run events.

Added here, all protocol-v4 features of the pinned OpenClaw 2026.9.9 Gateway:

- a signed, paired device identity, required for operator scopes because the
  demo Gateway sees Docker-published connections as remote;
- the ``tool-events`` client capability, so tool lifecycle evidence for a run
  can be recorded alongside its answer;
- caller-chosen idempotency keys and per-run timeouts on ``chat.send``;
- structured Gateway errors (code, details, retryable) instead of message-only;
- a distinct ``disconnected`` run outcome. The Gateway documents that socket
  disconnects do not cancel accepted work, so a dropped connection is never
  reported as a failed or cancelled run; callers recover with ``agent.wait``.
"""

from __future__ import annotations

import asyncio
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable

from loguru import logger
from pipecat.services.openclaw.client import (
    PROTOCOL_VERSION,
    OpenClawError,
    OpenClawEvent,
    OpenClawGatewayClient,
    OpenClawRun,
)

from clawvoice.openclaw.device import DeviceIdentity, auth_payload_v3

DEFAULT_SCOPES = ["operator.read", "operator.write"]
CLIENT_ID = "gateway-client"
CLIENT_MODE = "backend"
DISCONNECTED = "disconnected"
_PAIRING_CODES = {"NOT_PAIRED", "PAIRING_REQUIRED", "DEVICE_PAIRING_REQUIRED"}


class GatewayError(OpenClawError):
    """A Gateway RPC failure with its structured, credential-free details."""

    def __init__(self, message: str, code: Any = None, details: dict | None = None, retryable: bool = False):
        super().__init__(message, code)
        self.details = details or {}
        self.retryable = retryable

    @property
    def detail_code(self) -> str | None:
        value = self.details.get("code")
        return str(value) if value else None


class PairingRequired(GatewayError):
    """The Gateway needs its owner to approve this ClawVoice device."""

    @property
    def request_id(self) -> str | None:
        for key in ("requestId", "pairingRequestId", "deviceRequestId", "id"):
            value = self.details.get(key)
            if isinstance(value, str) and value:
                return value
        return None


@dataclass
class GatewayHealth:
    state: str = "disconnected"  # disconnected | connecting | connected | pairing_required | error
    server_version: str | None = None
    scopes: list[str] = field(default_factory=list)
    device_id: str | None = None
    pairing_request_id: str | None = None
    last_error: str | None = None
    connected_at: float | None = None

    def snapshot(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "server_version": self.server_version,
            "scopes": list(self.scopes),
            "device": (self.device_id[:12] + "…") if self.device_id else None,
            "pairing_request_id": self.pairing_request_id,
            "last_error": self.last_error,
        }


def sanitize(text: Any, *secrets: str | None, limit: int = 500) -> str:
    value = str(text)
    for secret in secrets:
        if secret:
            value = value.replace(secret, "[REDACTED]")
    return value[:limit]


def _error_from(error: dict[str, Any]) -> GatewayError:
    details = error.get("details") if isinstance(error.get("details"), dict) else {}
    code = error.get("code")
    message = str(error.get("message") or code or "Gateway request failed")
    retryable = bool(error.get("retryable") or details.get("retryable"))
    kind = PairingRequired if (str(code) in _PAIRING_CODES or str(details.get("code")) in _PAIRING_CODES) else GatewayError
    return kind(message, code, details, retryable)


class GatewayClient(OpenClawGatewayClient):
    """One authenticated operator connection to one OpenClaw Gateway."""

    def __init__(
        self,
        *,
        url: str,
        token: str,
        device: DeviceIdentity,
        scopes: list[str] | None = None,
        display_name: str = "clawvoice",
        connect_timeout: float = 15.0,
        request_timeout: float = 30.0,
    ):
        super().__init__(
            url=url,
            token=token,
            scopes=list(scopes or DEFAULT_SCOPES),
            connect_timeout=connect_timeout,
            request_timeout=request_timeout,
            reconnect_on_error=False,
        )
        self._device = device
        self._client_display_name = display_name
        self._challenge: dict[str, Any] | None = None
        self.health = GatewayHealth(device_id=device.device_id)
        self._tool_listeners: dict[str, Callable[[dict[str, Any]], None]] = {}
        # Tool events can arrive before chat.send's reply names the run; hold them briefly.
        self._unrouted_agent: deque[dict[str, Any]] = deque(maxlen=200)
        self.recent_events: deque[dict[str, Any]] = deque(maxlen=200)
        self._connect_lock = asyncio.Lock()

    @property
    def url(self) -> str:
        return self._url

    # ------------------------------------------------------------------ connect
    def _client_info(self) -> dict[str, Any]:
        return {
            "id": CLIENT_ID,
            "displayName": self._client_display_name,
            "version": "0.1.0",
            "platform": sys.platform,
            "mode": CLIENT_MODE,
        }

    def _ready(self) -> bool:
        hello = self._hello
        return (
            self._websocket is not None
            and hello is not None
            and hello.done()
            and not hello.cancelled()
            and hello.exception() is None
        )

    async def connect(self):
        """Connect (or verify the live connection) and check the granted scopes."""
        async with self._connect_lock:
            if self._ready() and self.health.state == "connected":
                return
            self.health.state = "connecting"
            try:
                if self._websocket is not None and self._hello is not None and not self._hello.done():
                    await self._wait_ready()
                else:
                    if self._websocket is not None:
                        await self._disconnect()
                    await self._connect()
            except PairingRequired as error:
                self.health.state = "pairing_required"
                self.health.pairing_request_id = error.request_id
                self.health.last_error = "Approve this ClawVoice device on the Gateway (see docs/voice.md)"
                await self._disconnect()
                raise
            except BaseException as error:
                self.health.state = "error"
                self.health.last_error = sanitize(error, self._token)
                if self._websocket is not None:
                    await self._disconnect()
                raise
            hello = self._hello.result() if self._hello else {}
            auth = hello.get("auth") or {}
            self.health.scopes = list(auth.get("scopes") or [])
            self.health.server_version = (hello.get("server") or {}).get("version")
            missing = [scope for scope in self._scopes if scope not in self.health.scopes]
            if missing:
                self.health.state = "pairing_required" if not self.health.scopes else "error"
                self.health.last_error = (
                    f"Gateway granted this device no {', '.join(missing)} scope; "
                    "approve the ClawVoice device on the Gateway"
                )
                await self._disconnect()
                raise PairingRequired(self.health.last_error, "MISSING_SCOPE", {"missing": missing})
            self.health.state = "connected"
            self.health.pairing_request_id = None
            self.health.last_error = None
            self.health.connected_at = time.time()

    @property
    def connected(self) -> bool:
        return self.health.state == "connected" and self._websocket is not None

    async def close(self) -> None:
        await self.disconnect()
        self.health.state = "disconnected"

    def _dispatch(self, frame: dict[str, Any]):
        kind = frame.get("type")
        if kind == "event":
            event = frame.get("event")
            if event == "connect.challenge":
                payload = frame.get("payload")
                self._challenge = payload if isinstance(payload, dict) else None
            elif event == "agent":
                self._route_agent_event(frame.get("payload"))
                return
            if event not in ("tick", "health", "presence", "heartbeat"):
                self.recent_events.append({"t": round(time.time(), 3), "event": event})
        elif kind == "res" and not frame.get("ok"):
            future = self._pending.pop(str(frame.get("id")), None)
            if future and not future.done():
                error = frame.get("error") if isinstance(frame.get("error"), dict) else {}
                future.set_exception(_error_from(error))
            return
        super()._dispatch(frame)

    async def _send_connect(self):
        challenge = self._challenge or {}
        nonce = challenge.get("nonce")
        signed_at = challenge.get("ts")
        hello = self._hello
        if not isinstance(nonce, str) or not isinstance(signed_at, int) or signed_at < 0:
            if hello and not hello.done():
                hello.set_exception(GatewayError("Gateway sent an invalid connect challenge", "BAD_CHALLENGE"))
            return
        client = self._client_info()
        payload = auth_payload_v3(
            device_id=self._device.device_id,
            client_id=client["id"],
            client_mode=client["mode"],
            role=self._role,
            scopes=self._scopes,
            signed_at_ms=signed_at,
            token=self._token,
            nonce=nonce,
            platform=client.get("platform"),
            device_family=client.get("deviceFamily"),
        )
        params = {
            "minProtocol": PROTOCOL_VERSION,
            "maxProtocol": PROTOCOL_VERSION,
            "client": client,
            "caps": ["tool-events"],
            "role": self._role,
            "scopes": self._scopes,
            "auth": {"token": self._token},
            "device": {
                "id": self._device.device_id,
                "publicKey": self._device.public_key,
                "signature": self._device.sign(payload),
                "signedAt": signed_at,
                "nonce": nonce,
            },
        }
        try:
            result = await self._request("connect", params)
        except Exception as error:  # noqa: BLE001 - settle the handshake with the reason
            if hello and not hello.done():
                hello.set_exception(error)
            return
        if hello and not hello.done():
            hello.set_result(result)

    def _fail_live_runs(self, reason: str):
        # A dropped socket is not a run outcome: the Gateway keeps accepted work
        # running. Report it distinctly so the caller recovers with agent.wait.
        for run in set(self._runs.values()):
            self._finish(run, OpenClawEvent(DISCONNECTED, text=reason))  # type: ignore[arg-type]
        if self.health.state == "connected":
            self.health.state = "disconnected"
            self.health.last_error = "Gateway connection closed"

    # ------------------------------------------------------------------ RPC
    async def call(self, method: str, params: dict[str, Any] | None = None, timeout: float | None = None) -> Any:
        """Authenticated Gateway RPC; the Gateway enforces method scopes."""
        await self.connect()
        if timeout is None:
            return await self._request(method, params or {})
        previous = self._request_timeout
        self._request_timeout = timeout
        try:
            return await self._request(method, params or {})
        finally:
            self._request_timeout = previous

    async def start_run(
        self,
        *,
        session_key: str,
        message: str,
        idempotency_key: str,
        timeout_ms: int,
        on_tool_event: Callable[[dict[str, Any]], None] | None = None,
    ) -> OpenClawRun:
        """Send one chat turn and return a handle to its event stream.

        The idempotency key is the caller's stable submission identity, so a
        transport-level retry with the same key cannot start a second run.
        """
        await self.connect()
        run = OpenClawRun(idempotency_key, session_key)
        self._runs[run.run_id] = run
        if on_tool_event:
            self._tool_listeners[run.run_id] = on_tool_event
        try:
            payload = await self._request(
                "chat.send",
                {
                    "sessionKey": session_key,
                    "message": message,
                    "timeoutMs": int(timeout_ms),
                    "idempotencyKey": idempotency_key,
                },
            )
        except BaseException:
            self.release(run)
            raise
        self._adopt_id(run, payload)
        if on_tool_event:
            for run_id in run.ids:
                self._tool_listeners[run_id] = on_tool_event
            early = [p for p in self._unrouted_agent if str(p.get("runId") or "") in run.ids]
            for item in early:
                self._unrouted_agent.remove(item)
                self._route_agent_event(item)
        run.ack = payload if isinstance(payload, dict) else {}  # type: ignore[attr-defined]
        logger.debug(f"{self} started run {run.run_id} in {session_key}")
        return run

    def release(self, run: OpenClawRun) -> None:
        for run_id in run.ids:
            self._tool_listeners.pop(run_id, None)
        self._forget(run)

    async def wait_run(self, run_id: str, timeout_ms: int) -> dict[str, Any]:
        """Gateway-side terminal snapshot of a run (``agent.wait``)."""
        payload = await self.call(
            "agent.wait", {"runId": run_id, "timeoutMs": int(timeout_ms)}, timeout=timeout_ms / 1000 + 10
        )
        return payload if isinstance(payload, dict) else {"raw": payload}

    async def abort_run(self, session_key: str, run_id: str) -> bool:
        payload = await self.call("chat.abort", {"sessionKey": session_key, "runId": run_id})
        return isinstance(payload, dict) and payload.get("aborted") is True

    async def history(self, session_key: str, limit: int = 20) -> dict[str, Any]:
        payload = await self.call("chat.history", {"sessionKey": session_key, "limit": limit})
        return payload if isinstance(payload, dict) else {"raw": payload}

    # ------------------------------------------------------------------ events
    def _route_agent_event(self, payload: Any) -> None:
        if not isinstance(payload, dict):
            return
        listener = self._tool_listeners.get(str(payload.get("runId") or ""))
        if listener is None:
            self._unrouted_agent.append(payload)
            return
        try:
            listener(payload)
        except Exception as error:  # noqa: BLE001 - evidence capture must not break routing
            logger.debug(f"Tool event listener failed: {error}")
