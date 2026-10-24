"""Independent verification of changes an agent reports.

The agent's words are never the proof. Each change in the agent's receipt is
read back from the authoritative source through a configured, deterministic
helper, and compared with what the user asked for. Verifiers are looked up by
name from agent configuration; this module holds the shared contract and the
generic ``helper_lookup`` verifier. Domain verifiers (for example the day
planner and Calendar) live in plugins.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from clawvoice.agents import AgentConfig, ReceiptType
from clawvoice.context.builtin import lookup
from clawvoice.helpers import HelperError, HelperRunner
from clawvoice.jobs.models import Expectations, VerificationItem
from clawvoice.timeutil import Clock

# Calendar IDs (e.g. name@group.calendar.google.com), event IDs, block/task IDs.
_SAFE_ARG = re.compile(r"^[A-Za-z0-9_.:@+=-]{1,256}$")


@dataclass
class VerifyContext:
    agent: AgentConfig
    helpers: HelperRunner
    clock: Clock


class ReceiptError(ValueError):
    """A receipt item is malformed or unsafe to pass to a helper."""


def safe_arg(value: Any, name: str) -> str:
    text = str(value or "")
    if not _SAFE_ARG.fullmatch(text) or text.startswith("-") or ".." in text:
        raise ReceiptError(f"receipt field {name!r} is missing or not a plain identifier")
    return text


def norm_title(value: Any) -> str:
    return " ".join(str(value or "").split()).casefold()


def compare(expect: Expectations, facts: dict[str, Any]) -> list[str]:
    """Human-readable differences between requested and observed values."""
    problems = []
    if expect.title and facts.get("title") is not None and norm_title(expect.title) != norm_title(facts["title"]):
        problems.append(f"title is '{facts['title']}', not '{expect.title}'")
    if expect.date and facts.get("date") and expect.date != facts["date"]:
        problems.append(f"date is {facts['date']}, not {expect.date}")
    if expect.start_time and facts.get("start") and expect.start_time != facts["start"]:
        problems.append(f"start is {facts['start']}, not {expect.start_time}")
    if expect.end_time and facts.get("end") and expect.end_time != facts["end"]:
        problems.append(f"end is {facts['end']}, not {expect.end_time}")
    return problems


async def helper_lookup(
    vctx: VerifyContext, item: dict[str, Any], rtype: ReceiptType, expect: Expectations
) -> VerificationItem:
    """Generic: run ``args.helper`` with receipt fields, expect ``found``-style JSON.

    Agent YAML example::

        receipt_types:
          test_run:
            verifier: helper_lookup
            args:
              helper: project
              argv: [tests, --json, --run-id, "{run_id}"]
              found_field: found
              facts: {status: status, summary: summary}
              message_field: summary
    """
    action = str(item.get("action") or "")
    args = rtype.args
    argv = []
    for part in args.get("argv") or []:
        def substitute(match: re.Match) -> str:
            return safe_arg(item.get(match.group(1)), match.group(1))

        argv.append(re.sub(r"\{([a-z_][a-z0-9_]*)\}", substitute, str(part)))
    result = await vctx.helpers.run_json(str(args.get("helper")), argv, fresh=True)
    data = result.data
    found = bool(lookup(data, args.get("found_field", "found")))
    facts = {name: lookup(data, path) for name, path in (args.get("facts") or {}).items()}
    expect_absent = action in tuple(args.get("absent_actions") or ("deleted", "removed"))
    if expect_absent:
        status = "verified" if not found else "mismatch"
        mismatches = [] if not found else ["it still exists"]
    elif not found:
        status, mismatches = "mismatch", ["it was not found"]
    else:
        required = args.get("require") or {}
        mismatches = [
            f"{name} is {facts.get(name)!r}, expected {wanted!r}"
            for name, wanted in required.items()
            if facts.get(name) != wanted
        ]
        mismatches += compare(expect, facts)
        status = "mismatch" if mismatches else "verified"
    message = str(lookup(data, args.get("message_field")) or facts.get("title") or rtype.name)
    return VerificationItem(
        type=rtype.name, action=action, status=status, facts=facts, mismatches=mismatches, message=message
    )


async def run_verifier(
    registry, vctx: VerifyContext, item: dict[str, Any], expect: Expectations
) -> VerificationItem:
    """Dispatch one receipt item to its configured verifier; never raises."""
    item_type = str(item.get("type") or "")
    action = str(item.get("action") or "")
    rtype = vctx.agent.receipt_types.get(item_type)
    if rtype is None:
        return VerificationItem(
            type=item_type or "unknown",
            action=action,
            status="unverifiable",
            message=f"No verifier is configured for {item_type or 'this change'}",
        )
    verifier = registry.verifiers.get(rtype.verifier)
    if verifier is None:
        return VerificationItem(type=item_type, action=action, status="unverifiable", message="Verifier missing")
    try:
        return await verifier(vctx, item, rtype, expect)
    except ReceiptError as error:
        return VerificationItem(type=item_type, action=action, status="unverifiable", message=str(error))
    except HelperError as error:
        return VerificationItem(type=item_type, action=action, status="error", message=str(error))
    except Exception as error:  # noqa: BLE001 - verification failures are reported, not raised
        return VerificationItem(
            type=item_type, action=action, status="error", message=f"Verification failed ({type(error).__name__})"
        )


def register(registry) -> None:
    registry.verifiers["helper_lookup"] = helper_lookup
