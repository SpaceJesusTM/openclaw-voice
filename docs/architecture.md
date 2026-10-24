# ClawVoice architecture and milestones

ClawVoice connects independent persistent OpenClaw agents to one coherent
spoken environment. Higgs Realtime 1.5 Preview supplies native speech;
Pipecat supplies realtime transport, the realtime service, tools and the
OpenClaw Gateway protocol client; ClawVoice supplies agent identity, fast
context, verified nonblocking work and the speaking floor.

Design intent: [ARCHITECTURE_AND_CONTRACTS.md](ARCHITECTURE_AND_CONTRACTS.md).
Priority 1 implementation guide: [voice.md](voice.md).

## Current layout

| Path | Responsibility | Current state |
| --- | --- | --- |
| `experiments/higgs-realtime-1.5/` | Direct API regression playground | Step 1 validated, preserved |
| `experiments/pipecat-higgs-1.5/` | Pipecat speech-to-speech validation | Step 2 validated, preserved |
| `infra/openclaw/` | Independent OpenClaw Gateway + Gmail (read-only) / Calendar / planner | Validated foundation (Tari) |
| `services/voice/` | ClawVoice runtime: Pipecat/Higgs voice, generic OpenClaw adapter, hot-context cache, background jobs, local API | **Priority 1 implemented** |
| `apps/web/` | Local browser UI (single agent, orb, tasks, context, diagnostics) | **Priority 1 implemented** |
| `services/context-cache/`, `services/orchestrator/` | Reserved names; P1 implements these as modules inside `services/voice` | Pointers only |
| `packages/agent-contracts/` | Shared contracts | P1 contracts live in `services/voice/clawvoice` (see README) |

One local process hosts everything in Priority 1: per-connection Pipecat
pipelines plus a process-level runtime (`AgentRuntime` per configured agent)
holding the OpenClaw adapter, hot-context cache and job manager, so background
work outlives voice connections.

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
- Google/Calendar logic stays in the OpenClaw agent's skills; ClawVoice only
  invokes the agent and reads results back through the agent's own helpers.
- Speech interruption and job cancellation are separate controls.
- Agent names, colors, voices, topics, helpers and verifiers are configuration
  (`services/voice/agents/*.yaml`); the core runtime names no agent.
- Only one demo OpenClaw agent (`tari`) is live; the production Tari
  installation is never accessed.

## Milestones

1. ~~Foundation: isolated OpenClaw, OAuth, Google reads/writes, planner.~~
2. ~~Priority 1: one real voiced agent, generic adapter, per-agent hot context,
   nonblocking verified jobs, spoken completions, local UI.~~
3. Priority 2a: several independently configured agents with distinct voices,
   name routing and one shared speaking floor (the floor coordinator and
   registry are built to extend).
4. Priority 2b: relevance routing and structured agent-to-agent collaboration.
5. Priority 3: permissioned multi-person rooms with independently owned agents.

**Avoid reimplementing** Pipecat's transport or the Gateway protocol. Keep a
configurable per-agent cache of approved hot snapshots and recent verified
results; do not copy private account data into a shared-room cache.
