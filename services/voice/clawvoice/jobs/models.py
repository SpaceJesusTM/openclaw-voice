"""Background job contract.

Lifecycle::

    accepted -> queued -> running -> verifying -> completed | failed | cancelled | uncertain

``accepted`` means ClawVoice recorded the request, never that work happened.
``completed`` means the agent run finished and, for changes, every reported
change was independently read back (``verification.status``: verified, or
mismatch when the observed state differs from what was asked). ``uncertain``
means an external side effect may or may not have happened (timeout, lost
connection, unverifiable receipt); it is never retried automatically.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

ACTIVE = ("accepted", "queued", "running", "verifying")
TERMINAL = ("completed", "failed", "cancelled", "uncertain")
_HHMM = re.compile(r"^([01]?\d|2[0-4]):([0-5]\d)$")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def normalize_hhmm(value: str | None) -> str | None:
    if not value:
        return None
    text = str(value).strip().lower().replace(".", "")
    match = re.match(r"^(\d{1,2})(?::(\d{2}))?\s*(am|pm)?$", text)
    if not match:
        return None
    hour, minute, meridiem = int(match.group(1)), int(match.group(2) or 0), match.group(3)
    if meridiem:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if meridiem == "pm" else 0)
    elif match.group(2) is None:
        return None
    if hour > 24 or minute > 59 or (hour == 24 and minute):
        return None
    return f"{hour:02d}:{minute:02d}"


@dataclass
class Expectations:
    """Details the voice model resolved; compared with independently read-back state."""

    title: str | None = None
    date: str | None = None  # YYYY-MM-DD, agent-local
    start_time: str | None = None  # HH:MM, agent-local
    end_time: str | None = None

    @classmethod
    def from_args(cls, args: dict[str, Any]) -> Expectations:
        date = str(args.get("date") or "").strip() or None
        if date and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
            date = None
        title = " ".join(str(args.get("title") or "").split())[:200] or None
        return cls(
            title=title,
            date=date,
            start_time=normalize_hhmm(args.get("start_time")),
            end_time=normalize_hhmm(args.get("end_time")),
        )

    def present(self) -> dict[str, str]:
        return {k: v for k, v in asdict(self).items() if v}


@dataclass
class VerificationItem:
    type: str
    action: str
    status: str  # verified | mismatch | unverifiable | error
    facts: dict[str, Any] = field(default_factory=dict)
    mismatches: list[str] = field(default_factory=list)
    message: str = ""


@dataclass
class Verification:
    status: str  # verified | mismatch | unverifiable | not_required | failed
    items: list[VerificationItem] = field(default_factory=list)
    ms: int = 0
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "items": [asdict(i) for i in self.items], "ms": self.ms, "note": self.note}


@dataclass
class Job:
    id: str
    agent_id: str
    owner_scope: str
    session_key: str
    kind: str  # question | change
    target: str
    request: str
    title: str
    expectations: dict[str, str]
    user_words: str
    origin_session: str | None
    tool_call_id: str | None
    fingerprint: str
    idempotency_key: str
    status: str = "accepted"
    created_at: str = field(default_factory=lambda: iso(utcnow()))
    updated_at: str = field(default_factory=lambda: iso(utcnow()))
    transport: str | None = None
    run_id: str | None = None
    timings: dict[str, Any] = field(default_factory=dict)
    answer: str = ""
    receipt: dict[str, Any] | None = None
    receipt_status: str | None = None
    verification: dict[str, Any] | None = None
    outcome_text: str = ""
    error: str | None = None
    side_effects_possible: bool = False
    cancel_requested: bool = False
    announcement: dict[str, Any] = field(default_factory=lambda: {"state": "none", "attempts": 0})
    evidence: dict[str, Any] = field(default_factory=lambda: {"tools": [], "events": 0})
    duplicate_count: int = 0
    attempts: int = 0

    @property
    def terminal(self) -> bool:
        return self.status in TERMINAL

    def touch(self) -> None:
        self.updated_at = iso(utcnow())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Job:
        known = {name for name in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})

    def public(self, now: datetime | None = None) -> dict[str, Any]:
        """Voice/browser-safe view: no session keys, run IDs or raw receipts."""
        now = now or utcnow()
        created = datetime.fromisoformat(self.created_at)
        age_seconds = max(0, int((now - created).total_seconds()))
        verification = self.verification or {}
        return {
            "job_id": self.id,
            "agent_id": self.agent_id,
            "title": self.title,
            "kind": self.kind,
            "target": self.target,
            "status": self.status,
            "verification": verification.get("status"),
            "verified": verification.get("status") == "verified",
            "outcome_text": self.outcome_text,
            "error": self.error,
            "side_effects_possible": self.side_effects_possible,
            "cancel_requested": self.cancel_requested,
            "announcement": self.announcement.get("state"),
            "transport": self.transport,
            "age_seconds": age_seconds,
            "age": _age(age_seconds),
            "created_at": self.created_at,
            "timings": {k: v for k, v in self.timings.items() if isinstance(v, (int, float))},
            "tools_seen": list(self.evidence.get("tools", []))[:10],
        }


def _age(seconds: int) -> str:
    if seconds < 60:
        return "just now" if seconds < 10 else f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60} min ago"
    return f"{seconds // 3600} h ago"
