"""Transport-neutral outcome of one OpenClaw agent run."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# completed     the agent produced a final answer
# failed        the Gateway or agent reported an error
# cancelled     the run was stopped (explicitly, by us)
# timeout       the run exceeded its time budget (outcome of side effects unknown)
# disconnected  the connection broke before an outcome was known
# lost          the Gateway no longer knows the run (e.g. restarted)
RUN_STATUSES = ("completed", "failed", "cancelled", "timeout", "disconnected", "lost")


@dataclass
class RunOutcome:
    status: str
    text: str = ""
    error: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def known(self) -> bool:
        """True when the Gateway reported a definite end state."""
        return self.status in ("completed", "failed", "cancelled")
