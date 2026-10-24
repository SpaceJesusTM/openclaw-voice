# ClawVoice architecture and milestones

ClawVoice connects independent persistent OpenClaw agents to one coherent
spoken environment. Higgs Realtime 1.5 Preview supplies native speech;
Pipecat supplies realtime transport, tool and worker primitives; ClawVoice
supplies application-specific identity, permissions, routing and caches.

## Current layout

| Path | Responsibility | Current state |
| --- | --- | --- |
| `experiments/higgs-realtime-1.5/` | Direct API regression playground | Step 1 validated |
| `experiments/pipecat-higgs-1.5/` | Pipecat speech-to-speech validation | Step 2 validated |
| `infra/openclaw/` | Independent, minimal OpenClaw Gateway + Gmail/Calendar | Docker validated; owner auth/live reads pending |
| `apps/web/` | Future presentation-ready frontend | Reserved |
| `services/voice/` | Production Pipecat/Boson bridge | Reserved |
| `services/orchestrator/` | Agent identity, floor, delegated tasks | Reserved |
| `services/context-cache/` | Per-agent hot context and recent results | Reserved |
| `packages/agent-contracts/` | Typed cross-component data contracts | Reserved |

## Foundation boundaries

Only one demo OpenClaw agent (`tari`) is configured initially, with strictly
separate `infra/openclaw/.local/` secrets and mutable state. It does not access
the user's production Tari instance. Gmail and Calendar read access come from a
different Google account after an explicit OAuth authorization.

The current foundation does not depend on running the earlier Higgs experiments,
nor does it connect voice to OpenClaw yet.

For trusted future backend calls, the private Gateway exposes
`POST /v1/chat/completions` at the loopback host port, selecting the agent
with `"model": "openclaw/tari"` and using the Gateway's own Bearer token.
Do not confuse that agent selector with the actual model
`openai/gpt-6.1-sol`. Keep the Gateway token server-side. The `user` field is
a stable session key, never an authentication credential.

## Next milestones

1. Owner completes ChatGPT OAuth and verifies Sol conversations/session continuity.
2. Owner separately authorizes the Gmail/Calendar OAuth read profile.
3. Validate Google CLI reads and agent-initiated reads in the container.
4. Add a server-side Pipecat to OpenClaw Gateway bridge, retaining nonblocking jobs.
5. Priorities 2a/2b: multiple independent agents, distinct Higgs voices,
   name routing, collaboration, and turn/floor coordination.
6. Later: permissioned multi-person shared conversations.

**Avoid reimplementing** Pipecat's worker bus or audio transport. Keep a
configurable per-agent cache of approved hot snapshots AND recent verified results;
do not copy private account data into a shared-room cache.
