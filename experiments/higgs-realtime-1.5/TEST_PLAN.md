# Higgs Realtime 1.5 Preview — Validation Plan

## Source of truth
- Official reference: https://github.com/boson-ai/Higgs-Realtime-Tutorial/tree/realtime-1.5-preview
- **Required model ID:** `higgs-realtime-1.5-preview`
- Expected WebSocket URL: `wss://api.boson.ai/v1/realtime?model=higgs-realtime-1.5-preview`
- Set the same ID in `session.update.session.model`; check the returned resolved session. **Do not silently fall back to `higgs-realtime`.**

## Scope
A small, isolated, reproducible browser + backend playground in `experiments/higgs-realtime-1.5/`. Do not modify OpenClaw/Tari or rely on an OpenClaw runtime yet. Preserve modular code to enable future integration.

## Acceptance checks
1. Browser playground starts locally after npm install and `.env` setup, displaying configured and acknowledged model.
2. Short-lived browser credential is minted by backend from `BOSON_API_KEY` (never expose the original key).
3. Text input produces a spoken response and a readable transcript.
4. Microphone audio produces an appropriate spoken response, with streaming playback and VAD.
5. Speaking over the assistant interrupts playback and truncates or marks only heard content.
6. A custom local `get_demo_planner` function is invoked and returns a distinctive value from the actual handler (verified by logged tool call), not an invented claim. Validate arguments, dedupe `call_id`, and return a useful error on failure.
7. If supported by preview and time allows, try Boson-server-side `web_search` separately; it must not be treated as a local custom function.
8. Include a headless smoke/probe script or small automated checks that surface configuration, model acknowledgement, tool events, and errors.
9. Unit tests run without an API key; live tests are clearly labelled and require an explicit key and network access.
10. README documents prerequisites, setup, exact commands, pass/fail results, limitations, and what was **not** tested. No real-world success claims without actual live execution.

## Test design notes
- Prefer the official TypeScript tutorial as the implementation reference. It includes `src/realtime/RealtimeClient.ts`, `src/realtime/sessionConfig.ts`, `tools/probe.ts`, and `docs/part-4.md`.
- Reuse patterns as appropriate, respecting Apache 2.0 attribution for copied code.
- Keep local credential files, logs containing secrets, and recordings uncommitted.
- Do not rely on user-specific private data. The planner result is fixture/mock data for a **real** function-call demonstration.
- Record model in both outgoing config and resolved server response. Fail visibly if the returned model is missing, rejected, or different after update.
- Leave multi-agent voice routing for the next phase; this milestone validates the foundation.

## Next milestone
Higgs tool calls -> harness adapter -> a real OpenClaw agent. After that, name-directed multi-agent routing, independent personalities/voices, and agent-to-agent collaboration.
