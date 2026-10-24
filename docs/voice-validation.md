# ClawVoice Priority 1 — validation results (2026-10-24, America/Toronto)

Environment: macOS arm64 (Mac demo installation), Python 3.11.6, pipecat-ai
1.12.0, pipecat-boson 0.1.4 (same locked graph as the experiment), OpenClaw
2026.9.9 demo container (`infra/openclaw`), Tari on `openai/gpt-6.1-sol` via
ChatGPT OAuth, Node 22.20 + Playwright Chromium, model
`higgs-realtime-1.5-preview`.

**Live** means real Boson Higgs, the real demo OpenClaw Tari and its real Google
account/planner. **Offline** means deterministic tests with a protocol-level fake
Gateway and fixture helpers. Speech input in every live test was synthetic
(macOS `say` WAVs, through aiortc or a Chromium virtual microphone), not a
physical microphone.

## Summary

| Requirement | Result | Evidence |
| --- | --- | --- |
| A. Real Higgs voice | **PASS (live)** | exact preview model acknowledged on both sessions; greeting, typed and spoken turns with non-silent audio; barge-in with `conversation.item.truncate`; disconnect and reconnect |
| B. Cached planner answer | **PASS (live)** | disposable real planner item; `get_context(planner.today)` fresh cache hit (0.11 ms read); spoken answer names it; no OpenClaw run |
| C. Real Calendar action | **PASS (live)** | voice → `delegate_task` → OpenClaw Tari → Google Calendar; independent read-back 15:00–16:00 −04:00 (19:00Z), primary demo calendar, no attendees; announced; deleted by voice and verified absent |
| D. Nonblocking interaction | **PASS (live)** | delayed real OpenClaw job (`sleep 35`, run 42.7 s); cached question answered while it ran; barge-in left it running; completion injected 41 ms after it finished with a verifiably free floor; delivered once |
| E. Refresh and invalidation | **PASS (live)** + offline | agent planner change verified and immediately visible; out-of-band rename picked up by background refresh in 66.2 s; stale/expired/unavailable/rollover offline |
| F. Portability | **PASS (live voice + offline)** | fixture agent Astra (other identity, topics, targets, receipt type, verifier) through the same runtime over the WebSocket transport; no agent-specific code (source scan test). One earlier repeat failed (defect 15); passed on the final code |
| G. Regression and reliability | **PASS** | all suites below; failure modes offline |
| Clarify, then Calendar update (probe `g`) | **PASS (live)** | "Schedule a meeting tomorrow." → clarifying question, no job; create; "Move that event to 4 to 5 PM instead." → the same event read back at 16:00–17:00 −04:00; deleted by voice |
| Checklist change (probe `h`) | **PASS (live)** | task added in a new section and verified; cached `checklist.focus` fresh and names it; removed by voice and verified |
| Browser path | **PASS (live)** | Playwright drives `apps/web`: connect, verified badge, typed and spoken turns, task panel, notice, disconnect |

## Automated suites

| Suite | Result |
| --- | --- |
| `services/voice` (`make check`) | **111 passed**, Ruff clean, compileall OK. No network calls. |
| `infra/openclaw` unittest | **78 OK**; `py_compile` OK; `manage.py doctor`: config valid, Gateway health, unauthenticated rejection and authenticated route passed |
| `experiments/pipecat-higgs-1.5` (`make check`) | **18 passed**, Ruff and compile OK (2 upstream deprecation warnings) |
| `experiments/higgs-realtime-1.5` | `npm test` **24 passed**; `npm run build` OK; `npm run test:browser` **7 passed** (mocked) |

Offline coverage in `services/voice/tests/` includes: preview-model and
endpoint enforcement; secret references (outside-repo and symlink refusal, never
in errors); agent-config validation (loopback-only Gateways, unknown topics);
Ed25519 v3 device signatures verified cryptographically by the fake Gateway;
pairing-required and missing-scope handling; Gateway idempotency (same key, one
run); abort; socket drop reported as `disconnected` and recovered with
`agent.wait`; early tool events buffered; cache fresh/stale/expired/unavailable,
invalidation during an in-flight refresh, single-flight, backoff, active vs idle
cadence, agent isolation and Toronto midnight rollover including the
2026-11-01 DST change; day-planner resolvers and verifiers on realistic helper
JSON (19:00Z verified as 3 PM Toronto; post-DST 20:00Z as 3 PM EST; mismatch,
cancelled, deleted, planner block, checklist status/completion, unsafe
identifiers); receipt parsing; the request envelope; nonblocking acceptance;
duplicate tool calls; overstated outcomes (mismatch, no receipt, agent error,
clarification, "no change"); explicit cancel (WebSocket and HTTP); queueing;
lost acknowledgement re-dispatched with the same key; HTTP 5xx never retried;
HTTP stream lost mid-run → uncertain; unreachable Gateway; restart recovery;
private bounded job store; floor control (no speech overlap, coalescing,
at-most-once, re-queue once, vanished client, quiet gap); the Higgs session
payload and tool options; notice injection reaching Boson as one user item plus
`response.create`; latency attribution; the local API (no secrets, cross-origin
POST refused); two agents sharing code but not state.

## Live scenario details (final consolidated run, committed code)

On the committed code, 2026-10-24 05:20–05:31 UTC: `probes/voice_probe.py --live
--scenario f` (fixture servers), then `--scenario all` (a–e, g, h), then
`probes/ui_probe.mjs --live`. Every scenario passed. An earlier full run, made
before defects 15 and 16 were fixed, passed everything except f. Private reports
are in ignored `services/voice/captures/`.

**A — voice (54.0 s).** Model `higgs-realtime-1.5-preview` acknowledged on
`session.created` and `session.updated` (two sessions). Typed prompt → first
audio 341 ms. The synthetic spoken "What is on my planner today?" was
transcribed exactly and answered from a fresh cache hit; speech end → first
audio 477 ms. Barge-in over a counting response: interruption,
`conversation.item.truncate` at 1969 ms, reply "Interruption confirmed."
Disconnect, new session, "reconnected." The greeting also relayed a result the
previous browser probe had left unheard (see limitations).

**B — cached planner (12.1 s).** Disposable item "ClawVoice Probe …" added for
tonight (21:00–21:30) through the demo's planner helper, cache warmed, then
"What's on my planner today?": `get_context(planner.today)` fresh, cache hit,
read 0.11 ms, handler 0.6 ms, no `delegate_task`, no OpenClaw run. Tool result
→ answer audio 1301 ms. The spoken answer named the item and its time. Item
removed afterwards.

**C — Calendar lifecycle (69.6 s).** "Create a Google Calendar event tomorrow
from 3 to 4 PM called ClawVoice Test …". The acknowledgement did not claim
completion. OpenClaw run 22.0 s (dispatch ack 24 ms), then the ClawVoice
read-back (771 ms) verified the title and 3–4 PM Sunday Oct 25, then the
spoken announcement. The probe's own read-back: `2026-10-25T15:00:00-04:00`–`16:00`
(19:00Z), primary calendar of the isolated demo account, no attendees, no
notifications. Voice delete: run 16.7 s, read-back 542 ms confirmed absence;
re-listed absent.

**D — nonblocking (62.3 s).** Delegated "run `sleep 35`, then reply 'delay test
complete'" (question): dispatch ack 24 ms, run 42.7 s. "Also, what's on my
planner tomorrow?" was answered from the cache while the job ran. A barge-in
during a later reply truncated speech; `/api/state` still showed the job running.
On completion the notice was injected 41 ms later with `user_speaking=false,
bot_speaking=false, response_active=false, tools_in_flight=0`; notice → first
audio 457 ms; delivered once. "Did that first background task finish?" was
answered from tracked state.

**E — invalidation and refresh (173.4 s).** "Add a local planner block tomorrow
from 2 to 3 PM called ClawVoice Block …" → run 16.3 s, read-back 1.45 s,
verified. The next "What's on my planner tomorrow?" (fresh) included it. The
block was then renamed directly with the helper, outside ClawVoice. The
active-session background refresh showed the new title after 66.2 s, and a
repeated question triggered a fresh `get_context` whose spoken answer used the
new title. Voice removal verified (run 15.3 s, read-back 1.02 s); nothing left
behind.

**f — portability (43.1 s).** `make fixture-gateway` + Astra on port 7861:
WebSocket transport (fixture device signed and accepted), `project.active`
from Astra's cache. "Please run the tests." went straight to `delegate_task`,
producing a fixture job with tool evidence, verified by the generic
`helper_lookup` verifier and announced. "How did the latest tests go?" was
answered from the invalidated, fresh `tests.last` topic.

**g — clarification and Calendar update (96.8 s).** "Schedule a meeting
tomorrow." → "What's the meeting about, and what time tomorrow works for you?",
with no `delegate_task`. Then a verified create (3–4 PM), and "Move that event
to 4 to 5 PM instead." → run 15.5 s, read-back 501 ms, verified. The probe found
the same event at 16:00–17:00 −04:00. Deleted by voice and verified absent.

**h — checklist (88.2 s).** "Add a checklist task called ClawVoice Task … with
priority status, in a new checklist section called ClawVoice Probe …" → run
34.1 s, read-back 397 ms, verified. "What are my priority checklist tasks?" →
fresh `checklist.focus`, and the answer named the task. Removing the task and
the emptied section: run 15.3 s, task removal verified (319 ms). The probe
confirmed both were gone.

**Browser (33.6 s).** Agent and cache state were rendered before connecting;
the model badge read verified 1.8 s after page load (Connect included). Typed
turn. A virtual-microphone question was answered via `get_context` and rendered
once. A background question job (ack 35 ms, run 3.8 s) appeared in the task
panel, completed, and produced a notice and a spoken answer. Disconnect.

After the runs, the demo Calendar and planner contained no "ClawVoice" items.

### Measured latencies (final consolidated run; diagnostic, one Mac and network)

| Measure | n | Median | Range |
| --- | ---: | ---: | --- |
| Typed text → first Higgs audio | 24 | 369 ms | 287–2056 ms |
| Speech end (server VAD) → first audio | 3 | 477 ms | 448–501 ms |
| Completion notice → first audio | 12 | 458 ms | 382–867 ms |
| Hot-context read (cache hit) | 8 | 0.10 ms | 0.06–0.15 ms |
| `get_context` tool handler | 8 | 0.5 ms | 0.3–1.0 ms |
| Tool result → first audio of the answer | 20 | 522 ms | 288–1642 ms |
| OpenClaw dispatch acknowledgement (HTTP) | 10 | 24 ms | 15–48 ms |
| OpenClaw Calendar/planner/checklist change runs | 9 | 16.3 s | 12.6–34.1 s |
| ClawVoice independent read-back | 9 | 0.54 s | 0.32–1.45 s |
| Job finished → notice injected, same session | 10 | 77 ms | 10–147 ms (waits for a free floor) |
| Warm all six Tari topics (live helpers, earlier run) | 1 | 2.7 s | 4 helper processes, 3 shared |

Job figures come from the ten Tari probe jobs of this run. Change runs are
OpenClaw model turns, and they varied between runs (median 11.3 s earlier the
same day). These are small samples. Pipecat delivers an asynchronous tool
result after the current speech segment, so a long spoken preamble delays the
acknowledgement; that is upstream behavior of the pinned stack.

## Defects found and fixed during validation

1. Tool events arriving before `chat.send`'s reply were dropped → buffered and replayed.
2. Invalidation compared timestamps that can tie → generation counter; reads
   retry within their budget when a pre-write refresh finishes.
3. Receipt identifiers containing `..` were accepted → stricter allowlist.
4. Latency was attributed to the tool result when Higgs called a tool before speaking → separate turn and answer measurements.
5. A notice counted as delivered to a browser that had silently vanished →
   client-liveness gating (inbound audio or UI keepalive); unheard notices return
   to pending.
6. Higgs answered a repeated planner question from conversation memory (stale
   after an outside edit) → instructions and tool description require a fresh
   read; re-verified live in scenario E.
7. Higgs called a tool in response to a notice and repeated the result → notices
   say to relay once without tools; re-verified in scenario F.
8. Question jobs were described as "not verified" → status wording distinguishes "not needed".
9. A change with a `no_change` receipt sounded like completion → "No change was made."
10. A change run that errored mid-way was labelled failed, and an HTTP stream
    ending without `[DONE]` would have counted as complete → both now *uncertain*.
11. The browser transcript duplicated text (RTVI `bot-output`) and split
    responses at audio pauses → one bubble per response from `bot-llm-text`.
12. A probe bug (unguarded data-channel handler) closed WebRTC mid-test; the
    server-side job still completed and was verified, demonstrating that jobs
    outlive voice sessions. The disposable event from that run was deleted
    through the voice path (verified absent).
13. Higgs put a checklist *section* name into `delegate_task.title`. The task
    itself was created correctly, and the verifier reported a mismatch instead
    of "done", which is the intended behavior. The field description now says
    it is the item's own name, never a section, list or calendar. Re-verified
    live in scenario h.
14. Probe fallback cleanup only handled events whose id it had already seen.
    It now waits for running jobs to finish (they outlive the voice session),
    then removes only items with the probe's unique title, which it checked
    were absent before it started.
15. In a repeat of scenario f, Higgs answered "Please run the tests." from the
    cached last-test result instead of starting a run. It treated an action as
    a status question, after the "always call get_context" guidance had been
    strengthened (6). The instructions and tool descriptions now say that
    cached context answers questions only and that a request to do something
    is always delegated. Re-verified in f, then the whole suite was run again
    on that wording.
16. An agent answer without a final full stop ran on into the notice's
    instruction ("…complete Tell the user…") → notice results are now closed as
    sentences.

## Limitations and untested areas

- **Physical microphone and speakers: pending owner validation.** Steps: `make
  dev`, open the page in Chrome, Connect, allow the microphone, use headphones,
  and repeat the live walkthrough in [voice.md](voice.md#live-demo-walkthrough)
  by voice, including a barge-in. Room noise, echo and perceived audio quality
  were not measured.
- **WebSocket transport against the real Gateway: pending owner approval.** The
  live handshake, Ed25519 v3 signature and pairing request (exactly
  `operator.read` + `operator.write`) were verified; approving the device is an
  owner permission decision that the automation was not permitted to make. All
  live Tari jobs therefore used the HTTP transport. WebSocket runs, abort,
  idempotent re-dispatch and `agent.wait` recovery are verified offline and live
  with the fixture agent only. After approving, check the badge shows
  `WebSocket (paired)` and re-run `--scenario c,d`.
- Recurring and all-day events, and checklist status changes and completion,
  were not exercised live; their verifiers are covered offline. Calendar
  create, time change and delete, planner block add and remove, and checklist
  task add and remove were exercised live.
- Only the changes listed in the agent's receipt are independently read back.
  A side change mentioned only in its summary is not checked by ClawVoice. In
  scenario h, Tari also removed the emptied checklist section; the probe
  checked that, not the runtime.
- A result whose notice was cut off by a disconnect counts as unheard and is
  announced again at the next connection (for up to 6 hours). The browser probe
  disconnects right after its notice starts, so the next session greeted with
  that result ("two plus two is four").
- One OpenClaw session lane per agent: a long job delays later delegated jobs
  (shown as queued). Cached answers are unaffected.
- With HTTP, a ClawVoice restart during a run cannot recover it (the Gateway
  cancels HTTP runs on disconnect); such jobs are marked uncertain.
- Higgs instruction adherence is probabilistic; the behaviors above were
  verified in these runs, not guaranteed. Tool result → answer audio varied
  0.35–1.8 s.
- One browser tab is one voice session; several tabs share jobs and
  announcements (latest session wins) and were not load-tested. Mobile layout is
  responsive but untested on a device.
- `calendar.next` looks three days ahead; the topic says so when empty.
- Astra is a fixture; no second real OpenClaw/Google environment was provisioned.
- Not in scope (Priority 2/3): multiple simultaneous agents and voices, name
  routing, agent collaboration, multi-user rooms.
