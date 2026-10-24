import asyncio
import os
import stat

import pytest

from clawvoice.openclaw.device import DeviceIdentity, auth_payload_v3, load_or_create, normalize_metadata
from clawvoice.openclaw.gateway import DISCONNECTED, GatewayClient, PairingRequired
from conftest import TOKEN


def test_v3_payload_matches_openclaw_format():
    payload = auth_payload_v3(
        device_id="dev", client_id="gateway-client", client_mode="backend", role="operator",
        scopes=["operator.read", "operator.write"], signed_at_ms=1737264000000, token="tok", nonce="n1",
        platform=" Darwin ", device_family=None,
    )
    assert payload == "v3|dev|gateway-client|backend|operator|operator.read,operator.write|1737264000000|tok|n1|darwin|"
    assert normalize_metadata("MacIntel") == "macintel"


def test_device_identity_is_private_stable_and_never_in_repr(tmp_path):
    path = tmp_path / "device.json"
    first = load_or_create(path)
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    again = load_or_create(path)
    assert again.device_id == first.device_id and len(first.device_id) == 64
    assert "PRIVATE" not in repr(first)
    link = tmp_path / "link.json"
    link.symlink_to(path)
    with pytest.raises(RuntimeError, match="symlink"):
        load_or_create(link)


def client_for(gateway, device=None, token=TOKEN):
    return GatewayClient(url=f"ws://127.0.0.1:{gateway.port}", token=token, device=device or DeviceIdentity.generate())


async def test_signed_device_connects_and_reports_scopes(gateway):
    client = client_for(gateway)
    try:
        await client.connect()
        assert client.health.state == "connected"
        assert client.health.scopes == ["operator.read", "operator.write"]
        assert TOKEN not in str(client.health.snapshot())
    finally:
        await client.close()


async def test_pairing_required_is_surfaced_with_request_id(gateway):
    gateway.require_pairing = True
    device = DeviceIdentity.generate()
    client = client_for(gateway, device)
    with pytest.raises(PairingRequired) as error:
        await client.connect()
    assert error.value.request_id
    assert client.health.state == "pairing_required"
    assert client.health.pairing_request_id == error.value.request_id
    gateway.approve(device.device_id)
    await client.connect()
    assert client.health.state == "connected"
    await client.close()


async def test_wrong_token_is_rejected_without_leaking_it(gateway):
    client = client_for(gateway, token="wrong-token-value-123")
    with pytest.raises(Exception):
        await client.connect()
    assert client.health.state == "error"
    assert "wrong-token-value-123" not in (client.health.last_error or "")


async def test_missing_write_scope_is_not_accepted(gateway):
    gateway.granted_scopes = []
    client = client_for(gateway)
    with pytest.raises(PairingRequired):
        await client.connect()
    await client.close()


async def test_same_idempotency_key_never_starts_a_second_run(gateway):
    client = client_for(gateway)
    try:
        first = await client.start_run(session_key="agent:astra:t", message="Request: hello", idempotency_key="job-1", timeout_ms=5000)
        second = await client.start_run(session_key="agent:astra:t", message="Request: hello", idempotency_key="job-1", timeout_ms=5000)
        assert first.run_id == second.run_id
        assert gateway.runs_started == 1
    finally:
        await client.close()


async def test_events_stream_and_abort(gateway):
    gateway.delay_scale = 1.0
    client = client_for(gateway)
    tool_events = []
    try:
        run = await client.start_run(
            session_key="agent:astra:t", message="Request: wait 5 seconds then answer",
            idempotency_key="job-abort", timeout_ms=10000, on_tool_event=tool_events.append,
        )
        await asyncio.sleep(0.1)
        assert await client.abort_run("agent:astra:t", run.run_id) is True
        kinds = [event.kind async for event in client.events(run)]
        assert kinds[-1] == "cancelled"
        assert tool_events and tool_events[0]["data"]["name"] == "read"
    finally:
        await client.close()


async def test_socket_drop_is_disconnected_not_failed_and_run_is_recoverable(gateway):
    gateway.delay_scale = 1.0
    gateway.drop_next_after_send = True
    client = client_for(gateway)
    try:
        run = await client.start_run(
            session_key="agent:astra:t", message="Request: delay 0.3 seconds", idempotency_key="job-drop", timeout_ms=5000
        )
        kinds = [event.kind async for event in client.events(run)]
        assert kinds == [DISCONNECTED]
        result = await client.wait_run(run.run_id, 5000)  # reconnects transparently
        assert result["status"] == "ok"
        assert "Fixture answer" in result["terminalReply"]["text"]
        assert gateway.runs_started == 1
    finally:
        await client.close()
