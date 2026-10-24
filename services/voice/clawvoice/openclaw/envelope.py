"""The message ClawVoice sends to an OpenClaw agent for one background job.

It frames the request (live voice, owner-originated, change vs question),
carries the voice model's resolved details next to the raw speech transcript
so the agent can cross-check them, asks for clarification instead of guesses,
and requests the machine-readable receipt whose object formats come from the
agent's configuration. The agent's own skills and rules remain authoritative.
"""

from __future__ import annotations

from clawvoice.agents import AgentConfig
from clawvoice.jobs.models import Expectations, Job
from clawvoice.openclaw.receipts import MARKER
from clawvoice.timeutil import Clock, local_now, spoken_clock


def build_message(agent: AgentConfig, job: Job, clock: Clock) -> str:
    now = local_now(agent.tz, clock)
    when = f"{now.strftime('%A, %B')} {now.day}, {now.year}, {spoken_clock(now)}"
    if job.kind == "change":
        kind = "CHANGE: the owner explicitly asked for this change. Make exactly this change and nothing else."
    else:
        kind = "QUESTION: answer it. Do not create, change or delete anything."
    lines = [
        f"[ClawVoice voice request, task {job.id}]",
        "Your owner is talking with you live through ClawVoice, a voice interface: they speak, and your reply "
        "is read aloud by the voice assistant. The ClawVoice server relays this request from that conversation.",
        "",
        f"Local time: {when} ({agent.timezone}).",
        f"Request type: {kind}",
        f"Request: {job.request}",
    ]
    if job.user_words:
        lines.append(f'Owner\'s latest words (speech transcript, may contain recognition errors): "{job.user_words}"')
    expect = Expectations(**job.expectations) if job.expectations else Expectations()
    details = []
    if job.target and job.target != "other":
        details.append(f"destination: {job.target}")
    if expect.title:
        details.append(f'title "{expect.title}"')
    if expect.date:
        details.append(f"date {expect.date}")
    if expect.start_time and expect.end_time:
        details.append(f"from {expect.start_time} to {expect.end_time} ({agent.timezone})")
    elif expect.start_time:
        details.append(f"starting {expect.start_time} ({agent.timezone})")
    if details:
        lines.append("Details the voice assistant resolved (check them against the request): " + "; ".join(details) + ".")
    lines += [
        "",
        "Follow your normal skills and AGENTS.md rules. If anything needed is missing, ambiguous or conflicting, "
        "make no change and ask one short question instead.",
        "Reply in one or two short spoken sentences: no markdown, lists, links or IDs.",
        "Then end with exactly one final line in this format:",
        f'{MARKER} {{"status":"done|needs_clarification|failed|no_change","summary":"<one short sentence>",'
        '"question":"<only for needs_clarification>","changes":[...]}',
    ]
    if agent.receipt_types:
        lines.append(
            'In "changes", list every change you actually made, one object each, '
            "with IDs exactly as your tools returned them:"
        )
        lines += [f"  {rtype.format}" for rtype in agent.receipt_types.values()]
    lines.append('Use an empty "changes" list when nothing changed. Never list a change that did not happen.')
    return "\n".join(lines)
