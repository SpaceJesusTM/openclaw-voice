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

Foundation: isolated Docker OpenClaw, explicit ChatGPT/Codex OAuth, different
Google account through its own desktop OAuth client, and verified Gmail/Calendar
reads and user-requested Calendar management. No voice-to-OpenClaw bridging in this milestone.

When user input or account approval is required, stop at a documented checkpoint.
Tests without real provider access are not proof of live integration.
Keep development on an isolated feature branch, do not merge or force push.
