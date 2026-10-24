# ClawVoice repository instructions for coding agents

Goal: reusable real-time voice conversations with independently operating
OpenClaw agents. Distinct characters, per-agent context, nonblocking work,
agent-to-agent collaboration, and eventual shared human/agent rooms.

## Hard boundaries

- Higgs model MUST be `higgs-realtime-1.5-preview` in voice code and runtime
  acknowledgement. Never silently fall back to `higgs-realtime`.
- Use one production framework: Pipecat; do not add LiveKit by default.
- Preserve completed experiments under `experiments/` as regression references.
- The `infra/openclaw/` demo is isolated from the production Tari installation.
  Never read, copy, mount, or overwrite production Tari tokens, state or private mail.
- No secrets, OAuth callback URLs, API keys, Google JSON, mutable state or
  raw sensitive voice transcripts in Git. All belong in ignored `.local/`.
- Tari's initial default model is `openai/gpt-6.1-sol` with no automatic
  fallbacks in the demo. Models, credentials and tools are configurable through
  OpenClaw's native management; do not impose a platform-wide model or OAuth profile.
  Verify actual inference separately from configured model/auth status.
- Gmail is read-only. Calendar has full event management via a separate
  authorized profile. Only perform changes the user requested; verify results.
- Reuse Pipecat transport, audio, workers and OpenClaw Gateway primitives
  rather than rebuilding generic framework functionality.
- Keep names, voices, frontend appearances, routing, and caches configurable.

## Current milestone

**Priority 1 is complete.** Stable baseline: branch `release/p1-stable`, tag
`v0.1.0` (see `docs/GETTING_STARTED.md`, `docs/voice.md`). One real voiced agent
(Tari) with a generic OpenClaw adapter, per-agent hot-context cache with live
fallback, nonblocking verified jobs and spoken completions, read-only Gmail,
unified Calendar/planner, push-to-talk and stop-speaking.

**Priority 2a** is implemented on `feat/p2a-team-voice` and awaits owner
acceptance: Astra (isolated coding agent) and Team mode (`docs/team-voice.md`):
name-addressed agents in one room, concurrent work, one speaking floor. Unnamed
requests go to nobody; topic-based routing is Priority 2b.

- Team mode: each agent's Higgs session receives only what was addressed to it;
  never fan out audio, tool output or private data between agents.
- The host runs only read-only Astra helper commands from the tracked template;
  agent-writable workspace files are never executed on the Mac.

- Agent specifics belong in `services/voice/agents/*.yaml`; the core runtime
  must not branch on agent names (a test enforces this).
- "Done" requires an agent receipt plus independent read-back; never report an
  accepted or unverified job as complete.
- The cache is an accelerator, not a capability boundary or source of truth:
  topics declare `coverage`; anything outside it is a read-only OpenClaw lookup.
- Speech interruption, "stop talking" and task cancellation are separate:
  stopping speech never cancels a job; `cancel_task` is explicit.
- Gmail's "Primary" means the Primary tab (inbox minus Promotions and Social),
  never `category:primary`. New timed planner entries are Calendar events;
  legacy local blocks are never migrated automatically.
- Keep the two Pipecat compatibility fixes and the exact preview model checks.
- Workspace skill/helper changes reach the demo only through
  `manage.py update-workspace` (known-version upgrade with backups).

When user input or account approval is required (for example WebSocket device
pairing), stop at a documented checkpoint. Tests without real provider access are
not proof of live integration. Develop on a feature branch from
`release/p1-stable`; do not merge, force push or delete branches without the
owner's request.
