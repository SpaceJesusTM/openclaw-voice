# ClawVoice — Phased roadmap, evidence, and demo storyboard

This is the **whole-project roadmap**, not a command to implement every phase during a single Claude Code run. Later explicit assignments govern what to build now. Preserve the final product intent even when deliberately limiting the implementation scope.

## Phased delivery map

| Stage | Priority | Required capabilities | Evidence of completion |
| --- | --- | --- | --- |
| Foundation | P0, already mostly built | Isolated OpenClaw Tari, OAuth, Gmail readonly, Calendar write and read, persistent day planner/checklist | Auth status + real tool read/write + verified planner state; reconfirm after Codex completes its planner task |
| One working voice + cache + jobs | **P1 — next Claude implementation** | Boson Higgs 1.5 + Pipecat realtime speech, connected to real OpenClaw Tari, hot-context cache with background refresh, nonblocking actions, completion announcements, generic agent registry | Physical/local spoken planner reads and verified Calendar write; can ask a cache question while an OpenClaw job is running; other agent fixture plugs in without runtime rewrites |
| Multiple independently voiced agents | **P2a** | Two, target three real OpenClaw agent configurations, unique voices/personalities/roles/orbs, explicit name addressing, configurable lead agent, one spoken floor | "Tari..." / "Astra..." / "Jester..." routed only to named backend, visibly distinct voices and work states, no unintended overlapping speech |
| Relevance + real collaboration | **P2b** | Automatic agent selection, genuine structured agent-to-agent work, team task handling, controlled sequential speaking | A collaborative project-availability request is handled by independently executing Astra and Tari work with correct handoff, user approval and verified action |
| Shared multi-person agent teams | **P3 — stretch** | Multi-human rooms, independently owned OpenClaw environments, scoped private context exchange, approvals and cross-user negotiation | Two owners coordinate through different agents without private credential leakage or unauthorized writes |

**Scope distinction:** The original hackathon brief lists two independently voiced agents as part of the hackathon *minimum viable demo* and three plus collaboration as the target. The first upcoming Claude task, however, intentionally stops at **P1** to create a solid reusable harness. P2a/P2b are still explicit project requirements for the envisioned finished experience and should remain architecturally possible, not be quietly dropped.

## Priority 1: concrete build order

1. **Inspect and preserve the foundation.** Latest branch and commit, the completed planner port, its tests, existing OAuth and state, current Gateway API, Pipecat experiment, documentation and known limitations. Create a new branch only after parallel Codex work has finished. No production MSI access.
2. **Integrate Pipecat/Higgs with real OpenClaw.** Backend-only Gateway token, `openclaw/<configured_agent>` selector, stable session keys, typed tool calls, verified results and error handling. Reuse working Pipecat/boson versions and compatibility fixes, not a mock-only demonstration.
3. **Build functional realtime local UI.** Microphone input, streaming speech and transcript, typed testing, interrupt, reconnect, agent/job status. Keep the browser token-free.
4. **Add nonblocking job handling.** Fast acceptance; worker-backed OpenClaw execution; job progress/terminal state; follow-up/status queries; natural later completion announcements; speech interruption not job cancellation.
5. **Build generic hot context.** Declarative per-agent topics/resolvers, warm-up, periodic/as-needed refresh, freshness/partial-failure signaling, invalidation after verified writes, scoped recent outcomes and minimal observability. Implement actual Tari planner/calendar/checklist resolvers through authorized agent/tool integration, not direct mock fixtures.
6. **Prove portability.** Register one synthetic non-Tari agent with a different cache topic/role, connect it to a mock Gateway, and show the same adapter/cache interfaces work without Tari-specific core changes.
7. **Acceptance and documentation.** Offline regression suite plus actual Boson/Gateway/Google end-to-end tests, startup instructions, known limitations and a clear owner demonstration.

### P1 required voice scenarios

**Scenario P1-A — low-latency fact answer**

- The owner opens localhost voice UI, connects the microphone and confirms resolved model `higgs-realtime-1.5-preview`.
- "What's in my day planner today?" triggers hot-context lookup for this agent and owner.
- The answer reflects the **real persisted local planner**, correctly distinguishing Calendar events, local blocks, commutes and checklist items. A fresh hit does not synchronously wait on a new OpenClaw agent run. A stale/unavailable snapshot is disclosed and refreshed/bypassed appropriately.
- Today's/tomorrow's planner, checklist priorities, today's Calendar and next meeting are supported by configured topics; the new day rolls over in `America/Toronto`.

**Scenario P1-B — actual spoken Calendar write**

- "Create a Calendar event for Monday from 3 to 4 PM called Study Group." Agent correctly resolves/clarifies date and timezone, chooses its existing permitted Calendar skill, creates the event, reads it back and checks the time and title.
- Higgs first acknowledges *work underway*, not *work completed*. It later announces the verified result; can report errors truthfully.
- Disposables are created only with owner authorization and cleaned up. Test follow-up update/delete only with specific authorization.
- Explicitly verify `America/Toronto` vs UTC display. A requested `2026-10-26T15:00:00-04:00` is `19:00Z`; a UI showing 7 PM in a UTC-configured Calendar does not itself imply incorrect API storage.

**Scenario P1-C — conversation continues during work (critical differentiator)**

- Start a controlled delayed backend job, accept it quickly and show a running indicator.
- Interrupt the assistant while it is speaking, then ask "What do I have tomorrow?".
- A fresh cached answer completes **before** the earlier job ends.
- The original OpenClaw job continues and terminates once, not cancelled by barge-in; a completion is queued until user speech has finished, then announced at most once.
- "Did the first job finish?" returns the real tracked state.

**Scenario P1-D — read after write + refresh behavior**

- Warm planner and Calendar snapshots. Make an explicitly authorized change through the agent. On verified success, invalidate/refresh affected topics.
- Follow-up spoken read reflects new source state, not the old snapshot.
- Out-of-band changes eventually appear through bounded background polling. Simulated stale and failed refreshes are clearly labeled and never interpreted as empty availability.

**Scenario P1-E — reusable rather than Tari-specific**

- Provide a second agent config (such as Astra with `project.active` cache) served by a fake Gateway/source resolver.
- The same adapter, hot-context mechanism, job schema and voice tool path work without `if name == tari` branches. This is a **portability test**, not the full P2a independent multi-voice room.

### P1 test matrix

| Area | Required automated / live evidence |
| --- | --- |
| Higgs/Pipecat | Exact preview acknowledgement, typed and microphone input, non-silent spoken output, native function result, barge-in/truncation, shutdown/reconnect; physical-device manual test marked separately |
| OpenClaw | Actual authorized Gateway request and final response; stable session; no frontend bearer leak; full result vs acceptance semantics; agent skill invoked rather than plausible response text |
| Cache | Fresh hit path, missing key, stale warning, incomplete data, expiration, midnight rollover, periodic refresh, singleflight, error/backoff, verified-write invalidation, scoped owner isolation, bounded recent results |
| Jobs | Queued/running/succeeded/failed/uncertain, duplicate IDs, concurrent unrelated requests, worker timeouts, interrupt during job, disconnect, explicit cancellation semantics, no duplicated external write |
| Calendar safety | Disposable real event create/read-back/update/delete as permitted, precise time/offset and recurrence handling, read-only Gmail unchanged |
| UX and isolation | Local startup, mic permission errors, captions, job progress, clear connection health, no tokens in client, no credentials/PII in tracked files |
| Regression | Existing direct Higgs and Pipecat experiments still pass; planner/Google tests remain green; no production Tari access |

Collect timings including speech-end to first audio, cache-hit latency, refresh latency, job acceptance vs full duration, and completion-to-announcement delay. Report test environment/sample count and distinguish *measured live* from simulated; never invent timings or claim one anecdotal run proves a general latency advantage.

## Priority 2a: multiple independent agents

**Target behaviors**

- Two functioning OpenClaw agents first, then three example personas (Tari, Astra, Jester), independently provisioned and credentialed as appropriate.
- Each agent's name, Markdown role/personality, backend, memory, tools, voice and orb/theme configurable.
- A configurable lead/primary agent handles unnamed exchanges where appropriate; other agents are directly addressable and do not depend on the lead for every action.
- Explicit name routing sends work only to the addressed agent, preserving independent conversation contexts and outstanding jobs.
- One shared human conversation room with floor control: human speech first, only appropriate agent speaks, others may wait/work without interrupting.
- Animated orbs show idle/listening/speaking/working/waiting. Only the speaking agent animates as speaking.

**Critical validation:** verify whether distinct simultaneous Higgs realtime sessions, one shared listener plus per-agent voice output, or another supported arrangement works best. The original brief prefers separate sessions when possible but allows a shared listener fallback. Test concurrent session accounting, interruptions, voice selection and ability to switch rapidly. Do not pretend multiple independent agents are one LLM roleplaying voices.

**P2a demo:** "Hey Tari, what's tomorrow's schedule?" → Tari speaks; "Astra, how are my tests?" → Astra uses its own tools and responds; "Jester, tell us a joke" → Jester speaks in another voice. No unaddressed agent pipes up.

## Priority 2b: relevance routing, collaboration and floor control

- Unnamed requests route to most relevant enabled agent, with uncertainty/role conflicts handled safely.
- Authenticated, structured inter-agent message contract: sender, recipient, task ID, requested capabilities, provenance, scope, status, idempotency, outcome.
- One agent can ask another for information or propose work without inheriting its credentials or permission to make external changes.
- Coordinator prevents overlapping speech, supports multi-agent contributions in sequence, and queues completion announcements sensibly.
- **True collaboration test:** "Hey guys, can we finish the project tomorrow?" Astra produces an independently tool-grounded estimate; Tari independently checks the real planner/calendar; owner approves a specific block; Tari writes it and verifies; each agent speaks only when chosen.
- Other possible use cases: shared research, project planning, status checks and agent handoffs.

## Priority 3: multi-human, agent-owned rooms (stretch)

- Multiple human speakers and independent owner identities in the same session.
- Cross-owner agent negotiation uses scoped information, not direct private database/token access.
- Consent needed for third-party availability, communication, and consequential calendar writes.
- Design includes authenticated user identities, session boundaries, owner-scoped cache and result stores, audit/provenance, interrupt/floor policy across human/agent speakers, and deny-by-default access rules.
- Example: two people propose a meeting; their independently owned agents find compatible availability using limited shared details; both humans approve before real events are written.
- Not required in the single-agent hackathon P1 deliverable.

## Complete hackathon storyboard (target experience)

This story is important product intent even if not all scenes are in the first working build.

| Scene | Spoken interaction | Visible/systems effect |
| --- | --- | --- |
| 1. Morning Tari | "Hey Tari, what's my schedule looking like?" | Green orb; quick fresh cached planner/Calendar response |
| 2. Afternoon Astra | "Astra, how's the code coming along? Run the tests." | Blue orb, own OpenClaw workspace, background job queued without blocking |
| 3. Evening Jester | "Jester, tell us a joke!" | Pink orb, distinct voice and lightweight conversation |
| 4. Completion re-entry | While Jester is talking, Astra's tests finish | Job state completes; coordinator waits for free floor; Astra announces verified outcome without overlap |
| 5. Actual collaboration | "Hey guys, can we get the project finished tomorrow?" | Astra estimates work, Tari checks availability; both speak in sequence; user approves a concrete schedule action |
| 6. Goodnight | "Good work today, guys. Goodnight!" | Agents respond in turn, orbs gently settle/fade |

**Closing line:** "Your assistant. Your developer. Your entertainer. Your entire AI team, always just a conversation away." — **Give your agents a voice.**

## Principles for credible demos

- The team feature is not real if a single model invents roles or task completion.
- A voice-to-agent tool integration is not real if it only calls a local fixture.
- A cached answer is not correct merely because it is fast: show source freshness and invalidation.
- An accepted background job is not a completed Google/engineering action.
- No model or SDK fallback should silently hide that the target preview or agent is unavailable.
- No UI polish should conceal unfinished reliability or safety checks; both matter in the final demo.
- Demo uses local Mac and explicitly authorized disposable data, **not** production Tari on the MSI.

## Known open questions / decisions to validate

1. P1 planner is committed and documented on the foundation (`e77e9c4`; 78 offline tests and live planner-agent acceptance reported). Confirm the local working checkout and its currently running demo before extending it; inspect actual `day-planner show --json` payloads and permissions.
2. Google API smoke event was verified at `19:00Z`, corresponding to requested `15:00 America/Toronto`; Google Calendar display appeared to be UTC. The UI settings were not directly inspected. Verify ongoing timezone correctness for voice-originated and recurring writes.
3. Boson/Pipecat handling of background completion injected into ongoing realtime sessions and natural spoken floor re-entry; validate live rather than relying on fixture-only behavior.
4. Concurrent Higgs sessions and stable distinct voice selection for P2a; evaluate early before committing to one transport architecture.
5. Voice UI polish scope after P1: Pipecat stock UI can be retained for functional acceptance; team-orb polished frontend is an explicit later demo goal.
6. Long-running work persistence and reconnect behavior: build honest observed states first; expand durability when proven necessary.
7. Costs/concurrency quotas from real API accounts: use measured provider behavior; avoid new paid dependencies without owner approval.
