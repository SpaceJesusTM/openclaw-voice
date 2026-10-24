"""Generic adapter for one configured OpenClaw agent.

Everything agent-specific (Gateway URLs, token reference, OpenClaw agent ID,
session key template, timeouts) comes from ``AgentConfig``. The adapter offers
one contract to the job manager regardless of transport:

    submit()  -> SubmittedRun     (returns once the Gateway accepted the turn)
    outcome() -> RunOutcome       (waits; recovers WebSocket runs after drops)
    cancel()  -> bool             (explicit cancellation, never speech barge-in)
    recover() -> RunOutcome       (after a ClawVoice restart, WebSocket only)

Transport ``auto`` prefers the paired WebSocket device (run IDs, Gateway-side
idempotency, abort, recovery) and falls back to the HTTP bearer route.
"""

from __future__ import annotations

import asyncio
import time
from contextlib import aclosing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from loguru import logger
from pipecat.services.openclaw.client import OpenClawRun

from clawvoice import secrets
from clawvoice.agents import AgentConfig
from clawvoice.openclaw.device import load_or_create
from clawvoice.openclaw.gateway import DISCONNECTED, GatewayClient, GatewayError, PairingRequired, sanitize
from clawvoice.openclaw.http import HttpChatTransport, HttpDispatchError, HttpRun
from clawvoice.openclaw.outcomes import RunOutcome

WS_REPROBE_SECONDS = 600


class DispatchError(RuntimeError):
    """The turn could not be handed to the Gateway.

    ``may_have_started`` is False only when the Gateway provably never received
    the request; only then may the job manager retry automatically.
    """

    def __init__(self, message: str, *, may_have_started: bool, transport: str | None = None):
        super().__init__(message)
        self.may_have_started = may_have_started
        self.transport = transport


@dataclass
class SubmittedRun:
    transport: str
    run_id: str
    session_key: str
    ack_ms: int
    ws_run: OpenClawRun | None = None
    http_run: HttpRun | None = None
    tool_events: list[dict[str, Any]] = field(default_factory=list)


def _history_text(history: dict[str, Any]) -> str:
    messages = history.get("messages") if isinstance(history, dict) else None
    for message in reversed(messages or []):
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content
        if isinstance(content, list):
            text = "".join(
                part.get("text", "") for part in content if isinstance(part, dict) and part.get("type") == "text"
            )
            if text.strip():
                return text
    return ""


class OpenClawAgent:
    def __init__(self, agent: AgentConfig, *, state_directory: Path):
        self.agent = agent
        self.mode: str | None = None
        self.config_error: str | None = None
        self._next_ws_probe = 0.0
        self.ws: GatewayClient | None = None
        self.http: HttpChatTransport | None = None
        try:
            token = secrets.resolve(agent.gateway.token_ref)
        except secrets.SecretError as error:
            self.config_error = str(error)
            return
        from clawvoice.voice.telemetry import register_secret

        register_secret(token)
        transport = agent.gateway.transport
        if transport in ("auto", "ws"):
            device = load_or_create(state_directory / agent.gateway.device_file)
            self.ws = GatewayClient(
                url=agent.gateway.ws_url, token=token, device=device, display_name=f"clawvoice-{agent.id}"
            )
        if transport in ("auto", "http"):
            self.http = HttpChatTransport(
                base_url=agent.gateway.http_url, token=token, agent_selector=f"openclaw/{agent.openclaw_agent_id}"
            )

    # ------------------------------------------------------------ transport
    async def select_transport(self, *, force: bool = False) -> str | None:
        """Prefer a working WebSocket device; otherwise fall back to HTTP."""
        if self.config_error:
            self.mode = None
            return None
        if self.ws is not None and (force or self.mode != "ws"):
            try:
                await self.ws.connect()
                self.mode = "ws"
                return self.mode
            except PairingRequired:
                logger.warning(
                    f"OpenClaw device for {self.agent.id} is not paired yet"
                    + (f" (request {self.ws.health.pairing_request_id})" if self.ws.health.pairing_request_id else "")
                )
            except Exception as error:  # noqa: BLE001 - surfaced through health
                logger.warning(f"OpenClaw WebSocket unavailable for {self.agent.id}: {type(error).__name__}")
            self._next_ws_probe = time.monotonic() + WS_REPROBE_SECONDS
        if self.mode == "ws":
            return self.mode
        if self.http is not None and await self.http.health_check():
            self.mode = "http"
        else:
            self.mode = None
        return self.mode

    async def _ensure_mode(self, require: str | None = None) -> str:
        if require == "ws":
            # A retry after an ambiguous WebSocket dispatch must reuse the same
            # idempotency key on the same transport; never fall back to HTTP.
            if self.ws is None:
                raise DispatchError("WebSocket transport unavailable", may_have_started=False, transport="ws")
            try:
                await self.ws.connect()
            except Exception:  # noqa: BLE001
                raise DispatchError("Gateway WebSocket unavailable", may_have_started=False, transport="ws") from None
            self.mode = "ws"
            return "ws"
        if self.mode == "http" and self.ws is not None and time.monotonic() >= self._next_ws_probe:
            await self.select_transport(force=True)
        if self.mode is None:
            await self.select_transport()
        if self.mode is None:
            raise DispatchError(self.config_error or "The OpenClaw Gateway is unavailable", may_have_started=False)
        return self.mode

    # ------------------------------------------------------------ runs
    async def submit(
        self,
        *,
        message: str,
        session_key: str,
        idempotency_key: str,
        timeout_s: float,
        on_tool_event: Callable[[dict[str, Any]], None] | None = None,
        require_transport: str | None = None,
    ) -> SubmittedRun:
        mode = await self._ensure_mode(require_transport)
        started = time.monotonic()
        if mode == "ws":
            assert self.ws is not None
            try:
                run = await self.ws.start_run(
                    session_key=session_key,
                    message=message,
                    idempotency_key=idempotency_key,
                    timeout_ms=int(timeout_s * 1000),
                    on_tool_event=on_tool_event,
                )
            except PairingRequired as error:
                self.mode = None
                raise DispatchError(str(error), may_have_started=False, transport="ws") from None
            except GatewayError as error:
                # A Gateway rejection is a definite answer: nothing started.
                raise DispatchError(sanitize(error), may_have_started=False, transport="ws") from None
            except (OSError, asyncio.TimeoutError, Exception) as error:  # noqa: BLE001
                # The request may have been written before the socket failed. The
                # idempotency key makes a resubmission over WebSocket safe; it is
                # still reported as possibly started.
                self.mode = None
                raise DispatchError(
                    f"Gateway connection failed during dispatch ({type(error).__name__})",
                    may_have_started=True,
                    transport="ws",
                ) from None
            return SubmittedRun(
                transport="ws",
                run_id=run.run_id,
                session_key=session_key,
                ack_ms=round((time.monotonic() - started) * 1000),
                ws_run=run,
            )
        assert self.http is not None
        try:
            http_run = await self.http.start(session_key=session_key, message=message, timeout_s=timeout_s)
        except HttpDispatchError as error:
            if error.retryable:
                self.mode = None
            raise DispatchError(
                str(error),
                may_have_started=not error.retryable and (error.status or 500) >= 500,
                transport="http",
            ) from None
        return SubmittedRun(
            transport="http",
            run_id=http_run.run_id,
            session_key=session_key,
            ack_ms=http_run.ack_ms or round((time.monotonic() - started) * 1000),
            http_run=http_run,
        )

    async def outcome(self, run: SubmittedRun, timeout_s: float, *, via_wait: bool = False) -> RunOutcome:
        """Wait for the run's end state within ``timeout_s`` (plus recovery).

        ``via_wait`` skips the event stream and asks the Gateway directly. It is
        used after a retried (idempotent) dispatch, when the original run may
        already have finished and will emit no further events.
        """
        deadline = time.monotonic() + timeout_s
        if via_wait and run.ws_run is not None and self.ws is not None:
            self.ws.release(run.ws_run)
            return await self.recover(run_id=run.run_id, session_key=run.session_key, timeout_s=timeout_s)
        if run.http_run is not None:
            try:
                return await asyncio.wait_for(run.http_run.wait(), timeout=timeout_s + 15)
            except asyncio.TimeoutError:
                await run.http_run.cancel()
                return RunOutcome("timeout", error="No outcome before the time limit")
        assert self.ws is not None and run.ws_run is not None
        parts: list[str] = []
        try:
            async with asyncio.timeout(timeout_s), aclosing(self.ws.events(run.ws_run)) as events:
                async for event in events:
                    if event.kind == "text_delta":
                        parts.append(event.text)
                    elif event.kind == "completed":
                        return RunOutcome("completed", text=event.text or "".join(parts))
                    elif event.kind == "cancelled":
                        return RunOutcome("cancelled", text=event.text or "".join(parts))
                    elif event.kind == "failed":
                        return RunOutcome("failed", text="".join(parts), error=sanitize(event.text, limit=300))
                    elif event.kind == DISCONNECTED:
                        break
        except asyncio.TimeoutError:
            return RunOutcome("timeout", text="".join(parts), error="No outcome before the time limit")
        finally:
            self.ws.release(run.ws_run)
        remaining = max(5.0, deadline - time.monotonic())
        return await self.recover(run_id=run.run_id, session_key=run.session_key, timeout_s=remaining)

    async def recover(self, *, run_id: str, session_key: str, timeout_s: float) -> RunOutcome:
        """Ask the Gateway how a known WebSocket run ended (``agent.wait``)."""
        if self.ws is None:
            return RunOutcome("lost", error="Run is not recoverable on this transport")
        deadline = time.monotonic() + timeout_s
        delay = 1.0
        while time.monotonic() < deadline:
            try:
                await self.ws.connect()
                self.mode = "ws"
                wait_ms = int(min(25.0, max(1.0, deadline - time.monotonic())) * 1000)
                result = await self.ws.wait_run(run_id, wait_ms)
            except GatewayError as error:
                if "not found" in str(error).lower():
                    return RunOutcome("lost", error="The Gateway no longer knows this run")
                await asyncio.sleep(delay)
                delay = min(delay * 2, 15)
                continue
            except Exception:  # noqa: BLE001 - reconnect and retry until the deadline
                await asyncio.sleep(delay)
                delay = min(delay * 2, 15)
                continue
            status = str(result.get("status") or "")
            if status in ("timeout", "pending"):
                continue
            reply = result.get("terminalReply") if isinstance(result.get("terminalReply"), dict) else {}
            text = reply.get("text") if reply.get("disposition") == "visible" else ""
            if not text:
                try:
                    text = _history_text(await self.ws.history(session_key, limit=10))
                except Exception:  # noqa: BLE001
                    text = ""
            if status == "ok":
                return RunOutcome("completed", text=text or "", details={"recovered": True})
            if status == "error":
                return RunOutcome("failed", text=text or "", error=sanitize(result.get("error") or "run failed"))
            return RunOutcome("cancelled", text=text or "", details={"gateway_status": status})
        return RunOutcome("timeout", error="Run outcome unknown after reconnecting")

    async def cancel(self, run: SubmittedRun) -> bool:
        if run.http_run is not None:
            return await run.http_run.cancel()
        if self.ws is not None:
            try:
                return await self.ws.abort_run(run.session_key, run.run_id)
            except Exception as error:  # noqa: BLE001
                logger.warning(f"Abort failed for {run.run_id}: {type(error).__name__}")
        return False

    # ------------------------------------------------------------ status
    def health(self) -> dict[str, Any]:
        ws = self.ws.health.snapshot() if self.ws else None
        return {
            "agent_id": self.agent.id,
            "openclaw_agent": self.agent.openclaw_agent_id,
            "configured_transport": self.agent.gateway.transport,
            "active_transport": self.mode,
            "config_error": self.config_error,
            "websocket": ws,
            "http": {"available": self.mode == "http" or (self.http is not None and self.http.last_error is None)}
            if self.http
            else None,
            "pairing_hint": self.agent.gateway.pairing_hint.format(
                request_id=self.ws.health.pairing_request_id or "<request id from 'devices list'>"
            )
            if self.ws and self.ws.health.state == "pairing_required"
            else None,
        }

    async def close(self) -> None:
        if self.ws is not None:
            await self.ws.close()
        if self.http is not None:
            await self.http.close()
