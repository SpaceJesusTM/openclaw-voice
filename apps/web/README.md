# Chorus web UI

A dependency-free local page served by the Chorus server at
http://127.0.0.1:7860 (`cd services/voice && make dev`). It uses native WebRTC
to Pipecat's SmallWebRTC transport and RTVI messages; it never receives the
Boson key or the OpenClaw Gateway token.

The top bar lets you switch between **Tari** (personal assistant), **Astra** (coding
assistant), and **Team** (Tari and Astra in one shared room, with automatic Higgs
routing and optional explicit names). The experiment and name-only rollback are
documented in [higgs-overseer.md](../../docs/higgs-overseer.md).

The whole app fits one window with no page scrollbar. Follow
[docs/design-language.md](../../docs/design-language.md) for any change here; the
redesign plan and data mapping are in [docs/ui-redesign.md](../../docs/ui-redesign.md). At the center is an orb whose
radial waveform shows who is talking: your microphone in ink, the agent's voice in
clay, a gentle ripple while it thinks, a slow clay arc while a background task runs.
Below it are the conversation (the agent's replies in serif), **Now** (what the agent
is doing this turn: lookups with cache and timing, handoffs, and the coding agent's
tool steps), and **Background tasks**. The model and Gateway status, push to talk,
type-only mode, fast-context freshness and diagnostics live in the settings menu
(top right). The keyboard button switches to type-only before connecting, and
focuses the message box once connected.

Files: `index.html`, `style.css`, `app.js`, `stage.js` (orb), `activity.js` (Now),
`preview.js` (live preview). Offline checks: `node --test` on each file in
`apps/web/tests/`. Browser test: `services/voice/probes/ui_probe.mjs`.

**Live preview (coding agents).** For an agent with the `projects.active` topic (Astra),
a preview pane slides in beside the conversation while a coding task runs, and shows
the agent's local preview server (`http://127.0.0.1:18950`) once it answers. A wireframe
shimmers while it builds; each finished task or new latest update reloads the site in a
second frame that cross-fades in over the old one. Close it with ×; **Preview** in the
top bar brings it back and the next build reopens it. Below 1180 px wide it takes a
band between the orb and the conversation. It reads only state the page already has and probes the
port from the browser. Offline checks: `node --test apps/web/tests/preview.test.cjs`.

Enable **Push to talk** for noisy rooms, then hold the button or Space outside the
message field. Releasing, changing tabs or losing focus closes the microphone.
Open-mic mode still supports natural interruption. Echo cancellation and noise
suppression are requested; automatic gain is disabled to avoid boosting room noise.
Offline mic-gate checks: `node --test apps/web/tests/microphone.test.cjs` from repo root.
