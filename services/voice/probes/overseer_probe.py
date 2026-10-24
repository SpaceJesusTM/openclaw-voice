"""One bounded, read-only WebRTC microphone smoke test for Team overseer routing.

Run against a dedicated local server with traces enabled. Outputs only routing
metadata; private transcripts/tool results stay in ignored captures/. No retries
of the test campaign, Calendar writes, or coding tasks.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from team_probe import CAPTURES, TeamClient, require, spans, wav

SCENARIO = [
    ("Hey Astra, what project is currently active?", "astra", True),
    ("What's on my calendar today?", "tari", True),
    ("What was the last website change?", "astra", True),
    ("Was that verified?", "astra", False),
]


def read_trace(session_id: str) -> tuple[Path, list[dict]]:
    for path in sorted(CAPTURES.glob("session-team-*.jsonl"), reverse=True):
        entries = [json.loads(line) for line in path.read_text().splitlines()]
        if any(e.get("kind") == "session.start" and e.get("session") == session_id for e in entries):
            return path, entries
    raise AssertionError("No local trace for this session; enable CLAWVOICE_PERSIST_TRACES=true")


async def smoke(url: str) -> None:
    client = TeamClient(url)
    try:
        await client.connect()
        await client.wait(lambda: client.kind("clawvoice.team.ready"), "Team startup", 40)
        require(client.kind("clawvoice.team.ready")[-1].get("router_mode") == "overseer", "Server must use overseer mode")
        await client.quiet(timeout=30)
        for index, (text, agent, switch) in enumerate(SCENARIO):
            mark = client.now()
            client.mic.play(wav(f"overseer-{index}", text))
            await client.wait(lambda: client.kind("clawvoice.team.user", mark), "spoken routing result", 20)
            users = client.kind("clawvoice.team.user", mark)
            require(len(users) == 1 and users[0].get("status") == "routed" and users[0].get("agent") == agent,
                    f"Turn {index + 1}: wrong or unresolved recipient")
            turn = users[0]["turn"]
            await client.wait(lambda: client.replies(mark, agent, "done", "answer"), "spoken agent answer", 35)
            await client.quiet(seconds=0.7, timeout=15)
            session_id = client.kind("clawvoice.hello")[0]["session_id"]
            _, entries = read_trace(session_id)
            calls = [e for e in entries if e.get("kind") == "team.overseer.call" and e.get("turn") == turn
                     and e.get("result", {}).get("ok")]
            if switch:
                require(any(e["result"].get("selected") == agent for e in calls), f"Turn {index + 1}: no actual call_agent switch")
            else:
                require(not calls, "Follow-up unnecessarily called call_agent")
            for call in calls:
                require(any(e.get("kind") == "team.send" and e.get("label") == "listener"
                            and e.get("event", {}).get("item", {}).get("call_id") == call["call_id"] for e in entries),
                        "Missing matching function-call output")
            # Independent read-back of actual outbound recipient, beyond the browser route label.
            transcript = users[0]["text"]
            sends = [e for e in entries if e.get("kind") == "team.send" and e.get("label") in ("voice:tari", "voice:astra")
                     and e.get("event", {}).get("item", {}).get("content") == [{"type": "input_text", "text": transcript}]]
            require([e["label"] for e in sends] == [f"voice:{agent}"], f"Turn {index + 1}: user text leaked or wasn't delivered exactly once")
            if switch:  # a natural follow-up can be answered from that agent's own history
                require(any(e.get("agent") == agent and e.get("phase") == "result" and e.get("name") == "get_context"
                            for e in client.kind("clawvoice.tool", mark)), "Recipient did not perform its real context lookup")
            print(json.dumps({"turn": index + 1, "recipient": agent, "call_agent": bool(calls), "answered": True}))
        heard = spans(client, 0)
        require(all(b[0] >= a[1] for a, b in zip(heard, heard[1:]) if a[1] is not None), "Overlapping audible replies")
        print("PASS: four spoken turns, actual tool decisions and recipients, real context reads, one audible speaker")
    finally:
        CAPTURES.mkdir(exist_ok=True)
        (CAPTURES / "overseer-probe-private.json").write_text(json.dumps(client.events, indent=2))
        await client.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", required=True)
    parser.add_argument("--url", default="http://127.0.0.1:7864")
    args = parser.parse_args()
    asyncio.run(asyncio.wait_for(smoke(args.url), timeout=180))


if __name__ == "__main__":
    main()
