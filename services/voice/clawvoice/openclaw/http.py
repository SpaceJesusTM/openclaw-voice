"""OpenClaw Gateway HTTP transport: streaming ``/v1/chat/completions``.

Used when the WebSocket device is not (yet) paired. It authenticates with the
server-side Gateway bearer token, selects the agent with ``openclaw/<agentId>``
and routes the turn to an explicit session with ``x-openclaw-session-key`` (the
same key the WebSocket transport uses, so both share conversation history).

Documented Gateway semantics this transport respects:

- headers arriving means the Gateway accepted the request; the run is underway;
- disconnecting the HTTP client cancels the agent run, so ``cancel()`` closes
  the stream, and a stream that breaks mid-run is reported as ``disconnected``
  (an external write may already have happened: the caller marks it uncertain);
- there is no idempotency key or resumable stream, so a submission is never
  retried once the request may have reached the Gateway.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any

import aiohttp
from loguru import logger

from clawvoice.openclaw.outcomes import RunOutcome


class HttpChatTransport:
    name = "http"

    def __init__(self, *, base_url: str, token: str, agent_selector: str, connect_timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self._token = token
        self.agent_selector = agent_selector
        self.connect_timeout = connect_timeout
        self._session: aiohttp.ClientSession | None = None
        self.last_error: str | None = None
        self.last_ok_at: float | None = None

    async def _http(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=None, sock_connect=self.connect_timeout, sock_read=None)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def health_check(self) -> bool:
        try:
            http = await self._http()
            async with http.get(self.base_url + "/healthz", timeout=aiohttp.ClientTimeout(total=5)) as response:
                ok = response.status == 200
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
            ok = False
        self.last_error = None if ok else "Gateway HTTP health check failed"
        return ok

    async def start(self, *, session_key: str, message: str, timeout_s: float) -> HttpRun:
        """Send the turn; returns once the Gateway accepted it (HTTP 200 headers)."""
        http = await self._http()
        body = {"model": self.agent_selector, "stream": True, "messages": [{"role": "user", "content": message}]}
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            "x-openclaw-session-key": session_key,
        }
        started = time.monotonic()
        try:
            response = await http.post(self.base_url + "/v1/chat/completions", json=body, headers=headers)
        except (aiohttp.ClientConnectorError, ConnectionRefusedError) as error:
            # Nothing reached the Gateway: safe to retry.
            raise HttpDispatchError("Gateway unreachable", retryable=True) from error
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as error:
            raise HttpDispatchError(f"Gateway request failed ({type(error).__name__})", retryable=False) from error
        if response.status != 200:
            detail = await _error_detail(response)
            response.release()
            # 4xx: rejected before any agent work. 5xx: ambiguous, never retried.
            raise HttpDispatchError(
                f"Gateway returned HTTP {response.status}{': ' + detail if detail else ''}",
                retryable=False,
                status=response.status,
            )
        self.last_ok_at = time.time()
        run = HttpRun(response=response, timeout_s=timeout_s)
        run.ack_ms = round((time.monotonic() - started) * 1000)
        run.start_reader()
        return run


class HttpDispatchError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool, status: int | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.status = status


async def _error_detail(response: aiohttp.ClientResponse) -> str:
    try:
        payload = await response.json(content_type=None)
        error = payload.get("error") if isinstance(payload, dict) else None
        message = error.get("message") if isinstance(error, dict) else None
        return str(message or "")[:200]
    except (ValueError, aiohttp.ClientError):
        return ""


class HttpRun:
    transport = "http"

    def __init__(self, *, response: aiohttp.ClientResponse, timeout_s: float):
        self.run_id = "http-" + uuid.uuid4().hex[:16]
        self._response = response
        self._timeout_s = timeout_s
        self._parts: list[str] = []
        self._outcome: asyncio.Future[RunOutcome] = asyncio.get_running_loop().create_future()
        self._reader: asyncio.Task | None = None
        self._cancelled = False
        self.ack_ms: int | None = None
        self.events = 0
        self.tool_events: list[dict[str, Any]] = []

    def start_reader(self) -> None:
        self._reader = asyncio.create_task(self._read(), name=f"http-run:{self.run_id}")

    async def _read(self) -> None:
        outcome: RunOutcome
        finished = False
        try:
            async with asyncio.timeout(self._timeout_s):
                async for raw in self._response.content:
                    line = raw.decode("utf-8", errors="replace").strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    self.events += 1
                    if data == "[DONE]":
                        finished = True
                        break
                    try:
                        chunk = json.loads(data)
                    except ValueError:
                        continue
                    if isinstance(chunk.get("error"), (dict, str)):
                        error = chunk["error"]
                        message = error.get("message") if isinstance(error, dict) else str(error)
                        self._settle(RunOutcome("failed", text="".join(self._parts), error=str(message)[:300]))
                        return
                    for choice in chunk.get("choices") or []:
                        delta = choice.get("delta") or {}
                        if isinstance(delta.get("content"), str):
                            self._parts.append(delta["content"])
            if self._cancelled:
                outcome = RunOutcome("cancelled", text="".join(self._parts))
            elif finished:
                outcome = RunOutcome("completed", text="".join(self._parts))
            else:
                # The stream ended without [DONE]: the outcome is unknown, not "completed".
                outcome = RunOutcome("disconnected", text="".join(self._parts), error="stream ended early")
        except asyncio.TimeoutError:
            outcome = RunOutcome("timeout", text="".join(self._parts), error="Agent run exceeded its time limit")
        except asyncio.CancelledError:
            outcome = RunOutcome("cancelled", text="".join(self._parts))
            self._settle(outcome)
            raise
        except (aiohttp.ClientError, ConnectionError, OSError) as error:
            status = "cancelled" if self._cancelled else "disconnected"
            outcome = RunOutcome(status, text="".join(self._parts), error=f"stream closed ({type(error).__name__})")
        finally:
            self._response.release()
        self._settle(outcome)

    def _settle(self, outcome: RunOutcome) -> None:
        if not self._outcome.done():
            self._outcome.set_result(outcome)

    async def wait(self) -> RunOutcome:
        return await asyncio.shield(self._outcome)

    async def cancel(self) -> bool:
        """Close the stream; the Gateway cancels an HTTP run whose client disconnects."""
        if self._outcome.done():
            return False
        self._cancelled = True
        self._response.close()
        if self._reader:
            try:
                await asyncio.wait_for(asyncio.shield(self._reader), timeout=5)
            except (asyncio.TimeoutError, asyncio.CancelledError, Exception):  # noqa: BLE001
                logger.debug("HTTP run reader did not settle promptly after cancel")
        self._settle(RunOutcome("cancelled", text="".join(self._parts)))
        return True
