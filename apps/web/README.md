# ClawVoice web UI (Priority 1)

A dependency-free local page served by the ClawVoice server at
http://127.0.0.1:7860 (`cd services/voice && make dev`). It uses native WebRTC
to Pipecat's SmallWebRTC transport and RTVI messages; it never receives the
Boson key or the OpenClaw Gateway token.

Shows the agent (name, role, colored orb with listening/speaking/working
states), the verified model, the Gateway transport, the conversation and
completion notices, background tasks (status, verification, timings, cancel),
fast-context freshness, and a diagnostics drawer. Typed-only mode works without
a microphone. The orb and identity come from agent configuration so later
multi-agent views can reuse them.

Files: `index.html`, `style.css`, `app.js`. Browser test: `services/voice/probes/ui_probe.mjs`.
