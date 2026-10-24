# Higgs Team overseer (PFinal 2 experiment)

`feat/pfinal2-higgs-overseer` starts from PFinal 1 (`6cb8b17`), preserving the
redesigned UI, real OpenClaw runtimes and shared speaking floor. Single-agent
Tari/Astra sessions are unchanged. Reference: Jeff's `agent-selector-demo`
`docs/silent-overseer.md`, selector sources and Higgs probe findings.

## Run and roll back

From the repository root, start the existing isolated demo backends if needed:

```sh
python3 infra/openclaw/manage.py start
python3 infra/openclaw/manage.py --agent astra start
cd services/voice
CLAWVOICE_TEAM_ROUTER_MODE=overseer make dev
```

Open <http://127.0.0.1:7860/?agent=team>, then Connect. Existing Boson credentials
are reused; never copy credentials from production Tari. A backend already
running does not need another start. See [GETTING_STARTED.md](GETTING_STARTED.md)
for first-time setup. Stop the foreground voice server with Ctrl+C.

The flag is a **process environment variable**, read when the server/room starts.
Restart the voice server to compare the original router:

```sh
CLAWVOICE_TEAM_ROUTER_MODE=names make dev
```

Names mode runs the original `AddressBook` path, including bare-name follow-ups,
short fragment continuations and unnamed requests going to nobody. It has no
overseer tools or generated routing responses. An invalid flag fails explicitly.

## Decision and event flow

The microphone goes through Pipecat WebRTC to exactly one Higgs session, pinned
and runtime-checked to `higgs-realtime-1.5-preview`. This session has server VAD,
input transcription, text-only output and a short roster prompt derived from
configured agents' public role paragraphs and aliases. The roster never contains
credentials, private tool results or agent replies.

The sole function tool is:

```json
{
  "type": "function",
  "name": "call_agent",
  "parameters": {
    "type": "object",
    "properties": {
      "agent": {"type": "string", "enum": ["<configured agent IDs>"]},
      "reason": {"type": "string"}
    },
    "required": ["agent", "reason"],
    "additionalProperties": false
  }
}
```

1. VAD `speech_started` creates a monotonically increasing internal turn ID and
   invokes the existing immediate barge-in. Reused Higgs item IDs are not turn IDs.
2. VAD commits the actual audio. Transcription completion supplies the text that
   will eventually go to the selected agent. The existing rolling-window alignment,
   number/name normalization and replay filtering remain in place.
3. With `create_response=false` on VAD, a single routing worker manually requests
   the decision after transcription is ready. The overseer judges the **original
   microphone audio in its conversation**, not a re-injected transcript. This
   avoids racing auto-created decisions against typed requests and ASR.
4. `response.created` binds a response ID. Function argument events and
   `response.done` must match that ID. Calls also found in the final response
   output use the same deduplication path. Every call is acknowledged with its
   exact call ID, including invalid/stale calls; duplicate events are acknowledged
   once. An accepted tool result is provisional until response completion.
5. A completed, valid decision selects the destination. No call means keep the
   active agent, initially the configured default. Only a validated tool decision
   produces the UI's “Routed to … · reason” label. All ordinary overseer text is
   discarded and never becomes an agent reply or audio.
6. The relevant user text goes once to that agent's existing `AgentVoice.ask`.
   No raw microphone audio is sent to any agent voice session. Explicitly addressed
   separate sentences may be sent to multiple agents, as before; each gets its
   own sentence, and the first destination takes conversational priority.

Explicit names and stop commands remain deterministic safeguards. An unambiguous
explicit name takes priority over an incorrect model decision, including on
failure. A bare name selects the conversational agent without starting a task.
An unnamed turn with a failed/invalid decision is visibly unresolved and goes to
no agent. Ordinary no-call completion is successful continuity, not a failure.
Selecting a specialist does not start a job: the existing agent tools still act
only on the actual request.

Typed input enters the same worker, prompt, tool validation and dispatch path;
only typed input is inserted as an overseer user text item. Sending text while
VAD says you are speaking is visibly refused; finish speaking and send again.
New speech or a newer typed message supersedes an older unresolved decision.
Already dispatched agent requests/jobs keep their existing lifecycle.

## Bounds, recovery and isolation

Routing holds the existing floor's awaiting gate for at most eight seconds after
speech stops (including ASR), refreshing that gate without modifying floor pacing.
Each response waits at most 3.5 seconds. A completed `upstream_error` retries once
within the overall budget. Other failures do not loop. Missing ASR and failed
routing show a repeat/name-address suggestion. A timed-out response replaces the
listener socket rather than relying on early `response.cancel`, which can omit
`response.done` on Higgs. Reconnection retains the active-agent ID, not private
agent state. Unsolicited tool-output continuations are drained and their calls
rejected; they cannot independently select an agent or become a user turn.

Late response IDs and superseded turns cannot dispatch or change active state.
Repeated completions within a VAD turn cannot dispatch twice. The trace records
`team.overseer.call`, `team.overseer.retry`, `team.overseer.failed` and `team.route`
with turn/response/call identifiers for inspection.

Handover uses the existing `SpeakingFloor` and `AgentVoice.yield_floor`: audible
speech is cut, unheard acknowledgements dropped, heard history truncated, and
accepted background jobs continue. Completion notices still wait for a natural
gap. No floor, cache, permission, receipt verification, cancellation or job
recovery implementation was replaced. Tari's tools/results remain with Tari;
Astra's tools/results remain with Astra. Only the overseer sees all user input.
Private traces stay in ignored `services/voice/captures/`.

## Verification

Offline, from the repository root:

```sh
cd services/voice
make check
cd ../..
node --test apps/web/tests/*.test.cjs
node --check apps/web/app.js
```

The routing tests drive real TeamRoom/floor code with fake provider events:
selection, no-call continuity, explicit priority, typed input, invalid arguments,
matched acknowledgements, stale responses, cumulative ASR, retry/timeout recovery,
handoff and original names behavior. They do not prove model quality.

One bounded live read-only smoke test, against a dedicated server if another
voice server is already running:

```sh
cd services/voice
CLAWVOICE_TEAM_ROUTER_MODE=overseer CLAWVOICE_STATE_DIR="$PWD/.local/pfinal2-smoke" make dev PORT=7864
# In another terminal, from services/voice:
.bootstrap/bin/uv run --locked python probes/overseer_probe.py --live --url http://127.0.0.1:7864
```

This sends synthetic microphone audio through WebRTC: explicit Astra project
status → unnamed Calendar today → unnamed latest website change → “Was that
verified?” It checks the actual `call_agent` trace, matching function outputs,
outbound per-agent text, real `get_context` results and non-overlapping playback.
It prints routing metadata only; the private report remains in ignored captures.
It performs no Calendar writes or coding changes and ends within 180 seconds.
The older `team_probe.py` base scenario expects **names mode**.

Live evidence (2026-10-24): one WebRTC run delivered all four spoken requests
exactly once to Astra → Tari → Astra → Astra. The first three had actual accepted
`call_agent` calls, matching function outputs and fresh context reads; the
follow-up used Astra's history without a switch. All four received spoken
answers, with no overlapping replies, provider errors or routing retries. The
probe initially also required a fresh context lookup on the conversational
follow-up; that assertion was narrowed to the three status questions, and the
same captured run was independently checked without repeating live calls.
No Calendar event or coding change was requested.

Manual check: repeat those four requests with headphones and your microphone.
Try a clarification, “Thanks,” a topic switch while an agent speaks, “Stop
talking,” and typed input between spoken turns. The active-conversation chip and
current speaker can differ while background results wait.

## Limits

- Intent selection is probabilistic. Incorrect *successful* unnamed decisions
  cannot be detected by protocol validation; use a name or the rollback flag.
- Waiting for ASR and routing adds latency. This experiment deliberately avoids
  speculative agent replies or raw-audio fan-out.
- The overseer sees user turns, roles and active agent only. It cannot interpret
  references that depend on an agent's private answer or tool result.
- New input supersedes an unresolved turn; it does not queue every overlapping
  fragment as a separate request. A provider reusing an item ID without a fresh
  VAD boundary cannot reliably identify a new turn. Rephrase after a visible miss.
- Listener replacement loses the overseer's audio history; the selected agent's
  own history/jobs remain. Long sessions, real acoustic echo, three or more agents
  and consequential job execution require owner acceptance beyond this smoke test.
