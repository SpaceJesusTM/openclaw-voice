"""Ed25519 device identity for the OpenClaw Gateway WebSocket protocol.

The Gateway grants operator scopes to a non-loopback WebSocket client only for
an approved, paired device identity. (Docker port publishing means the demo
Gateway never sees ClawVoice as a loopback client.) This module creates one
private key per ClawVoice installation, stores it in the ignored .local/ tree
with 0600 permissions, and signs the Gateway's connect challenge using the
v3 payload format of the pinned OpenClaw 2026.9.9 gateway client:

    v3|deviceId|clientId|clientMode|role|scopes|signedAtMs|token|nonce|platform|deviceFamily

The private key and any token are never logged or sent to the browser.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from clawvoice.paths import private_dir


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def normalize_metadata(value: str | None) -> str:
    """Mirror OpenClaw's normalizeDeviceMetadataForAuth: trim, ASCII lower-case."""
    if not isinstance(value, str):
        return ""
    return "".join(chr(ord(c) + 32) if "A" <= c <= "Z" else c for c in value.strip())


def auth_payload_v3(
    *,
    device_id: str,
    client_id: str,
    client_mode: str,
    role: str,
    scopes: list[str],
    signed_at_ms: int,
    token: str | None,
    nonce: str,
    platform: str | None,
    device_family: str | None,
) -> str:
    return "|".join(
        [
            "v3",
            device_id,
            client_id,
            client_mode,
            role,
            ",".join(scopes),
            str(signed_at_ms),
            token or "",
            nonce,
            normalize_metadata(platform),
            normalize_metadata(device_family),
        ]
    )


@dataclass(frozen=True)
class DeviceIdentity:
    device_id: str
    public_key: str
    _private_key: Ed25519PrivateKey = field(repr=False, compare=False)

    def sign(self, payload: str) -> str:
        return b64url(self._private_key.sign(payload.encode("utf-8")))

    @classmethod
    def from_private_key(cls, key: Ed25519PrivateKey) -> DeviceIdentity:
        raw = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        return cls(device_id=hashlib.sha256(raw).hexdigest(), public_key=b64url(raw), _private_key=key)

    @classmethod
    def generate(cls) -> DeviceIdentity:
        return cls.from_private_key(Ed25519PrivateKey.generate())


def load_or_create(path: Path) -> DeviceIdentity:
    """Load the installation's identity, creating it exclusively on first use."""
    if path.is_symlink():
        raise RuntimeError("Refusing a symlinked device identity file")
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        key = serialization.load_pem_private_key(data["private_key_pem"].encode("ascii"), password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise RuntimeError("Device identity is not an Ed25519 key")
        identity = DeviceIdentity.from_private_key(key)
        if identity.device_id != data.get("device_id"):
            raise RuntimeError("Device identity file is inconsistent; inspect it before reuse")
        return identity
    private_dir(path.parent)
    identity = DeviceIdentity.generate()
    pem = identity._private_key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode("ascii")
    record = {
        "version": 1,
        "purpose": "ClawVoice OpenClaw Gateway operator device (private; never commit)",
        "device_id": identity.device_id,
        "public_key": identity.public_key,
        "private_key_pem": pem,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(record, handle, indent=1)
    return identity
