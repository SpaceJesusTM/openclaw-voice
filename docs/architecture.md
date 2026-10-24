# Chorus architecture and milestones

Chorus connects independent persistent OpenClaw agents to one coherent
spoken environment. Higgs Realtime 1.5 Preview supplies native speech;
Pipecat supplies realtime transport, the realtime service, tools and the
OpenClaw Gateway protocol client; Chorus supplies agent identity, fast
context, verified nonblocking work and the speaking floor.

Design intent: [ARCHITECTURE_AND_CONTRACTS.md](ARCHITECTURE_AND_CONTRACTS.md).
Run and test: [GETTING_STARTED.md](GETTING_STARTED.md). Implementation guide: [voice.md](voice.md).
Team mode: [team-voice.md](team-voice.md) and [higgs-overseer.md](higgs-overseer.md).
The current build is on `main`; branch `release/p1-stable` (tag `v0.1.0`) is the
Priority 1 baseline.

## Current layout

| Path | Responsibility | Current state |
| --- | --- | --- |
| `experiments/higgs-realtime-1.5/` | Direct API regression playground | Step 1 validated, preserved |
| `experiments/pipecat-higgs-1.5/` | Pipecat speech-to-speech validation | Step 2 validated, preserved |
| `infra/openclaw/` | Independent OpenClaw installations: Tari (Gateway + Gmail read-only, Primary tab / Calendar / unified planner) and Astra (isolated coding workspace + local preview) | Validated (Tari, Astra) |
| `services/voice/` | Chorus runtime: Pipecat/Higgs voice, generic OpenClaw adapter, hot-context cache with live fallback, background jobs, stop-speaking, local API; Team mode (Higgs overseer routing, a Higgs session per agent, shared speaking floor) and voice-only agents | Implemented |
| `apps/web/` | Local browser UI (one-on-one tabs and Team mode, orb, agent seats, tasks, live preview, context, diagnostics, push-to-talk) | Implemented |
| `services/context-cache/`, `services/orchestrator/` | Reserved names; P1 implements these as modules inside `services/voice` | Pointers only |
| `packages/agent-contracts/` | Shared contracts | P1 contracts live in `services/voice/clawvoice` (see README) |

One local process hosts everything: a per-connection Pipecat pipeline for a
one-on-one conversation, or a Team room (a silent Higgs overseer plus one Higgs
session per agent behind one connection), plus a process-level runtime
(`AgentRuntime` per configured agent) holding the OpenClaw adapter, hot-context cache
and job manager, so background work outlives voice connections. A voice-only agent's
runtime has no OpenClaw backend, tools or jobs.

One-on-one connection:

```text
Browser --WebRTC/RTVI--> Pipecat transport -> Higgs 1.5 Preview (tools)
                                   |  get_context      delegate_task / check_tasks / cancel_task
                                   v                        v
                        HotContextCache            JobManager -> OpenClawAgent -> Gateway (WS | HTTP)
                          ^  resolvers                 |  verifiers (independent read-back)
                          |                            |  invalidation -> announcement
                       trusted read helpers <----------+
```

## Boundaries kept

- The Gateway token and Boson key stay server-side; the UI receives neither.
- Google/Calendar logic stays in the OpenClaw agent's skills; Chorus only
  invokes the agent and reads results back through the agent's own helpers.
- Speech interruption, "stop talking" and job cancellation are separate controls.
- The cache is an accelerator, not a boundary: each topic declares its coverage,
  and anything outside it becomes a read-only OpenClaw lookup.
- Agent names, colors, voices, topics, helpers and verifiers are configuration
  (`services/voice/agents/*.yaml`); the core runtime names no agent.
- Two demo OpenClaw agents (`tari`, `astra`) run in separate containers, and
  Jester is voice-only; the production Tari installation is never accessed.

## Milestones

1. ~~Foundation: isolated OpenClaw, OAuth, Google reads/writes, planner.~~
2. ~~Priority 1: one real voiced agent, generic adapter, per-agent hot context,
   nonblocking verified jobs, spoken completions, local UI~~, plus follow-ups:
   unified schedule, Gmail Primary tab, live fallback, stop speaking (`v0.1.0`).
3. ~~Priority 2a: several independently configured agents with distinct voices,
   name routing and one shared speaking floor~~ (Astra, Team mode, and Jester as a
   voice-only agent).
4. Priority 2b: ~~relevance routing~~ (the Higgs overseer's `call_agent`
   decisions); structured agent-to-agent collaboration is **not implemented**.
5. Priority 3: permissioned multi-person rooms with independently owned agents
   (**not implemented**).

**Avoid reimplementing** Pipecat's transport or the Gateway protocol. Keep a
configurable per-agent cache of approved hot snapshots and recent verified
results; do not copy private account data into a shared-room cache.
