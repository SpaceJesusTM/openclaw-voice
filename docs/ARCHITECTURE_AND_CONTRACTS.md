# Chorus — Architecture and reusable contracts

> **Status:** Priority 1 is implemented (baseline `v0.1.0`; see [architecture.md](architecture.md) and [voice.md](voice.md) for the actual code). Priority 2a and later remain design intent.
>
> **Design intent, not an already-implemented API specification.** Claude should inspect actual repo source, Pipecat/Boson compatibility, and OpenClaw Gateway behavior, then choose the simplest coherent implementation. The names, YAML and type shapes below are examples of **semantics**, not requirements to create new microservices.

## Architectural split

```text
Human microphone / typed input
             |
      Pipecat WebRTC transport
             |
   Boson Higgs Realtime 1.5 Preview   <----- spoken responses / tool continuations
             |
  ClawVoice speech/tool coordinator
       |                      |
       | fast retrieval       | delegation (short acceptance)
       v                      v
 Per-agent hot context     Nonblocking OpenClaw jobs
       ^                      |        |
       |                      |        +----> progress + verified result events
 Periodic / event refresh    |
       |                      v
       +------------------- OpenClaw Gateway -- agent-specific session
                                           |
                             agent skills / state / verified tools
                                           |
                                 planner, Calendar, Gmail, ...

Future: named agent registry -> agent-select routing -> one shared floor manager
Future: independent agent owners / permissioned multi-human rooms
```

### Module responsibilities

**Pipecat/Boson voice loop** — microphone/audio transport, native streaming speech-to-speech, typed debug input, voice output, VAD/interruption, exact model/session verification, function-call dispatch, captions/observability. Do not rebuild generic audio transport. Preserve the two compatibility adapters in the working Pipecat experiment.

**Agent registry/adapter** — resolve a configured OpenClaw identity, backend endpoint, secret reference and session identity; submit user requests; report accepted/running/verified/failure/cancel state; return bounded results. No Tari-specific branch in the runtime. Support a second mock example even before two real agents exist.

**Hot-context engine** — configure approved per-agent topics; refresh via safe OpenClaw-backed resolvers; track freshness, completeness, version and source; serve fast read-only snapshots to Higgs; invalidate when known writes succeed; record bounded recent verified job results. It is a materialized view, **not** a second source of truth.

**Job runner/event coordinator** — dispatch OpenClaw work off the realtime turn, preserve independent request identity, avoid duplicate side effects, surface progress separately from final Higgs tool results as required by the SDK, and queue completion announcements while human speech or another response is active. Audio barge-in and explicit job cancellation are separate controls.

**Floor/routing controller** — in P1, arbitrate user's utterance, voice response and queued job completion for **one agent**. In P2, explicit name routing and distinct voices; later automatic selection, multi-agent turn turns and direct inter-agent messages. Avoid speculative comprehensive team federation in P1.

**Web UI** — localhost connection controls, microphone, captions, selected agent, voice state, job statuses; later colorful animated orbs for each agent and clear floor/working indicators. Never transmit raw provider/Gateway keys to frontend.

## Existing foundation to preserve

The Mac demo OpenClaw is isolated in `infra/openclaw/`, with a loopback Gateway port `18939` as its documented default. The current Gateway supports trusted backend chat requests with selector `openclaw/tari`; the actual LLM is configured via OpenClaw (previously verified on subscription OAuth with `openai/gpt-6.1-sol`). Revalidate the selected route in the checked-out revision. `tari-read` grants Gmail and Calendar read-only; `tari-calendar-write` grants Calendar management. Do **not** broaden Gmail scopes or bypass agent skills.

Ported local day-planner state is expected to include dated Markdown blocks/commutes and a categorized checklist, independent from Google Calendar. Its integration was in progress when these docs were created—verify current source, tests and live behavior rather than assuming it was merged.

The validated reference experiments are `experiments/higgs-realtime-1.5/` (direct realtime) and `experiments/pipecat-higgs-1.5/` (Pipecat). Reuse the validated Pipecat stack (Python 3.11, `pipecat-ai==1.12.0`, `pipecat-boson==0.1.4` pending controlled upgrade) and the exact model **`higgs-realtime-1.5-preview`**. The experiment already proved live media, barge-in, tool call, and mock worker bus; only the real Gateway/job/cache integration remains for P1.

## Suggested agent configuration model

A declarative agent definition should cover stable identity, display name, optional Markdown role/personality, Boson voice selection, UI color, backend/agent selector and auth **reference**, timezone, permitted capabilities, hot-context topics and source/resolver names, update/freshness policies, retention, and ownership scope. Example (illustrative only):

```yaml
agents:
  tari:
    display_name: Tari
    avatar_color: green
    voice_id: default
    role_file: agents/tari.md
    openclaw:
      endpoint_ref: local_demo_gateway
      agent_selector: openclaw/tari
      credential_ref: gateway_server_env
    timezone: America/Toronto
    hot_context:
      - key: planner.today
        resolver: planner_day
        args: {day: today}
        refresh_seconds: 120
        max_staleness_seconds: 300
      - key: calendar.next
        resolver: next_calendar_event
        refresh_seconds: 120
        max_staleness_seconds: 300
  astra:
    display_name: Astra
    avatar_color: blue
    role_file: agents/astra.md
    openclaw: {endpoint_ref: future_coding_gateway, agent_selector: openclaw/astra,
               credential_ref: astra_server_env}
    hot_context:
      - key: project.active
        resolver: active_project_status
        refresh_seconds: 180
```

Do not require the agent name, green orb, or planner resolver to be built into the generic cache. Use an agent-ID + authorized owner/session namespace. An agent can be independently configured without inheriting Tari's OAuth or Google mailbox.

## Cache / hot-context contract

A useful logical snapshot:

```text
agent_id, owner_scope, topic_key
value (typed, bounded, voice-safe representation)
source / resolver, source_version if available
fetched_at, fresh_until, maximum_usable_age
complete: true|false
status: fresh|stale|unavailable|refreshing|error
error reason (sanitized), optional provenance
```

**Tari P1 cache minimum:** planner.today, planner.tomorrow, checklist priorities/in-progress, calendar.today, calendar.next; plus bounded recent verified job results. Other configured agents could cache build status, current project, game state, or reminders without code changes to the cache core.

**Freshness:** warm proactively; refresh on controlled schedules; invalidate after *verified* planner, checklist and Calendar changes; use singleflight to avoid duplicate source reads; handle TTL, DST-aware date rollover, failed/incomplete sources, backoff and explicit live bypass for freshness-critical requests. A cache that failed refreshing may yield a **clearly marked** last-known result, never silently invent fresh availability. Refresh through actual agent tools/resolvers or verified OpenClaw-side helpers; avoid costly full LLM agent calls for each heartbeat when structured read-only retrieval exists. OpenClaw proactive heartbeats are an optional mechanism, not a requirement to enable autonomous jobs on the demo agent.

**Consistency:** after verified planner/Calendar mutation, relevant snapshots must invalidate or update before subsequently answering as current. Avoid asserting a failed or merely accepted write in cached results. Treat external changes made outside Chorus as eventually detected by bounded refresh. Never leak Gmail security/auth messages or raw private emails into generic voice/cache state.

**Separate recent-results index:** job_id, agent_id, request_id, created_at, completed_at, terminal status, verified/simulated flags, safe summary and limited retention. Don't confuse prior generated speech with a verified tool action.

## Background job contract and event model

Proposed states:

```text
queued -> running -> succeeded (verified outcome)
                  -> failed
                  -> cancelled (known no longer running; side-effect caveats)
                  -> uncertain (timeout or unknown external side effect)
```

Each submission has a caller/session scope, correlation ID, idempotency key when supported, selected agent, task description, accepted timestamp and final verification result. `accepted` means **work dispatched**, never the action succeeded.

**Nonblocking semantics:** Higgs receives a prompt acknowledgment + job identifier quickly, can handle unrelated voice/cache questions, and later receives a final result event that can become spoken confirmation. A long-lived speech-model function should not await the full underlying OpenClaw operation. Preserve completion status even if the user speaks over an acknowledgment or disconnects. Never automatically rerun an ambiguous write without checking whether it happened.

**Interruption ≠ cancellation:** barge-in stops/truncates speech via Pipecat/Boson. Running OpenClaw work is not aborted just because audio stopped. Explicit cancellation requests invoke a separately modeled cancellation flow; no assurance of rollback after an external write may have executed.

**Completion arbitration:** wait until human is no longer speaking; don't overlap existing bot speech; coalesce duplicate notices; make brief announcements; allow "what's the status?" without forcing redispatch; don't lose final results during session changes. For P2, extend the event model to include speaking agent and floor priority.

## External action and permission boundary

Voice layer never implements privileged business logic already in OpenClaw. The end-to-end path is:

1. The user speaks a clear request.
2. Higgs invokes an appropriate structured Chorus tool.
3. Job runner sends it to the correctly configured OpenClaw agent/session.
4. OpenClaw uses its existing authoritative tools/skills (e.g. Calendar management).
5. On success, backend reads back the event and verifies actual title, time, timezone, target and state.
6. Chorus surfaces a verified result; Higgs speaks it at an appropriate turn.

For ambiguous time, recurrence, attendee, recipient, calendar ownership, or destructive operations, solicit user clarity/authorization and do not guess. Existing Calendar system allows direct explicitly specified event creation, but an inferred event from Gmail or a document is **not** an authorization. Planner blocks never silently create Google Calendar events. Imported content and cached facts do not authorize actions.

**Timezone:** use IANA `America/Toronto` for the initial demo; never equate UTC Calendar UI display with a stored-event bug. `2026-10-26T15:00:00-04:00` and `2026-10-26T19:00:00Z` are identical instants. Validate timezone and DST conversions with live read-back and tests.

## Later multi-agent and multi-user expansion hooks

- **P2a:** assign independently configured OpenClaw backends/sessions and different Higgs voices; explicit name-addressing; configurable lead agent; coordinated sole-speaker floor; no silent private-context mixing.
- **Voice implementation uncertainty:** prefer distinct Higgs realtime sessions per concurrently available agent if feasible; a shared listening session plus agent-specific spoken voices is a fallback to evaluate. **Do not prescribe that implementation before real SDK tests**. Agent-to-agent communication is text/structured data, not re-synthesized audio relayed as inputs.
- **P2b:** relevance router, team/task IDs, structured inter-agent messages with sender/recipient/provenance, ownership of shared task, event deduplication, staged multi-agent speaking turns and handoffs.
- **P3:** room identity, authenticated human/agent ownership, per-user grants, scoped sharing/availability, explicit confirmation for cross-account writes, rate limits, auditability and private-cache partitioning. Cannot assume one user's OpenClaw credentials apply to other users.

## Reliability, security and observability

- Isolate demo from real MSI production Tari; never migrate tokens, workspace, agent memory or Google credentials from production.
- Loopback-only by default. API keys, OAuth details and Gateway token stay server-side; sanitization in logs, voice and cache.
- Reuse Pipecat transport and worker abstractions; avoid duplicate message-bus infrastructure without a reason. One modular local runtime is sufficient for P1.
- Track `speech_end -> first_audio`, cache hit/miss/refresh and age, job accepted/running/done, Gateway duration, confirmed vs inferred results, and completion-announcement delay; avoid misleading synthetic/live comparisons.
- Test no double execution on duplicated tool calls, conflicting user turns, disconnects, agent outages, cache failures and observed-to-spoken provenance.
