# ClawVoice UI redesign plan

Branch: `astra-coding-agent-frontend` (local). Status: built (sections 1–5); not yet
checked in a live voice session.

**One window, no page scrollbar.** The page is fixed to the window height. The orb
scales with the height (`clamp(128px, 30vh, 250px)`), and the conversation, Now and
task lists fill what's left, each scrolling inside itself if it overflows. Under
1180 px the preview takes a band of the height instead of growing the page.

A calmer, Claude-like voice screen. The voice is the focus: one orb with a live
waveform that shows who is talking, the conversation underneath, a short list of
what the agent is doing right now, and its background tasks. Little chrome, no
rows of pills or badges.

**Constraint: frontend only.** Everything below is built from data the page already
receives. No changes to the voice server, the job loop, the agents or their tools.

## 1. Look and feel

| Token | Light | Dark | Use |
|---|---|---|---|
| Canvas | warm off-white `#F5F4EE` | warm charcoal `#1F1E1D` | page |
| Surface | `#FFFFFF` | `#262624` | panels, user message tint |
| Text | `#1F1E1D` | `#ECEBE6` | body |
| Muted text | `#6B6A65` | `#A3A29C` | labels, timestamps |
| Hairline | 8% ink | 10% white | all borders, 1px |
| Clay | `#D97757` | `#D97757` | the agent speaking or working, nothing else |

- **Type:** the agent's words and name use a serif (`ui-serif, Georgia, serif`), and the
  interface uses the existing sans stack. You can tell who is talking from the typeface.
  No web fonts, so it works offline.
- **Clay means "the agent".** Your own voice is drawn in ink, never clay.
- **No status pills.** Status is a word or a small dot. Hairline borders, generous spacing.
- **Motion:** `cubic-bezier(.16,1,.3,1)`, 300–560 ms. Every animation is turned off
  under `prefers-reduced-motion`.

## 2. Layouts

### Default (no preview)

```
┌───────────────────────────────────────────────────────────────┐
│ ClawVoice                              Tari  Astra    ⚙        │
├───────────────────────────────────────────────────────────────┤
│                         ( orb + waveform )                     │
│                               Astra                            │
│                             Speaking                           │
│                       ⌨      ( 🎙 )      ⏻                    │
├──────────────────────────────────────┬────────────────────────┤
│ Conversation                         │ Now                    │
│                     [ your message ] │  • Checked project 4ms │
│ Astra's reply in serif…              │  • Handed off          │
│ [ Type to Astra                    ] │ Background tasks       │
│                                      │  Moonlight site  0:42  │
└──────────────────────────────────────┴────────────────────────┘
```

### Preview open (wide, ≥ 1180 px)

The orb shrinks into a row with the call controls at the top of the left column;
conversation, Now and tasks stack beneath. The preview takes the right side.

```
┌──────────────────────────────┬────────────────────────────────┐
│ (orb) Astra · Speaking ⌨ 🎙 ⏻ │ ● Applying changes  Moonlight ↻ ↗ ✕ │
├──────────────────────────────┤ ┌────────────────────────────┐ │
│ Conversation                 │ │                            │ │
│ Now                          │ │       live website         │ │
│ Background tasks             │ │                            │ │
└──────────────────────────────┴─┴────────────────────────────┴─┘
```

### Narrow (< 1180 px)

A single column: stage, then the preview when it's open, then conversation, Now and
tasks. Under 860 px the Now and tasks sections stack below the conversation.

## 3. Components and where their data comes from

| Component | Data source (already reaches the page) | Backend change |
|---|---|---|
| Orb waveform, you speaking | Mic analyser (`ui.localAnalyser`) and `user-started/stopped-speaking` events | None |
| Orb waveform, agent speaking | Speaker analyser (`ui.remoteAnalyser`) and `bot-started/stopped-speaking` events | None |
| Orb: thinking | `bot-llm-started` until the first audio | None |
| Orb: working | Any job in `accepted/queued/running/verifying` | None |
| Conversation | `user-transcription`, `bot-llm-text`, `bot-interrupted`, existing notices | None |
| Type to the agent | Existing composer (`send-text` RTVI message) | None |
| Now: lookups | `clawvoice.tool` (name, cache state, read ms) and `llm-function-call-in-progress` | None |
| Now: handoff | `delegate_task` call events and `clawvoice.job` `job.accepted` | None |
| Now: coding-agent tools | `jobs[].tools_seen`, e.g. `exec: ./bin/astra-project check …`. The page refreshes `/api/state` every 2–3 s while a job is active. Needs the WebSocket pairing (section 6) | None |
| Background tasks | `jobs[]`: title, status, age, outcome, verification, cancel | None |
| Task "running" line | Job status only. An animated line, not a percentage | None |
| Live preview pane | Existing `apps/web/preview.js`, restyled | None |
| Settings menu | Existing model and gateway badges, push-to-talk, typed-only, diagnostics log, fast-context list | None |
| Agent switcher | Existing `/api/agents` | None |
| Setup problem line | `gateway.config_error`, shown only for the selected agent | None |

**Out of scope** (each would need backend work):
- Which file the coding agent is editing. The server records only tool names and command heads.
- Progress percentages. No progress number exists.
- The models' private reasoning. Neither Boson nor OpenClaw sends it.

**The "Now" rules:** it shows only steps that happened, newest at the bottom, with the
current one in clay. It clears on each new request. It never shows predicted next steps.

## 4. Build order

1. Tokens, type and layout shell: light and dark, three breakpoints.
2. Orb and waveform: a `<canvas>` ring of about 64 bars driven by the existing analysers.
   Ink for the user, clay for the agent, a slow arc while working, breathing when idle.
3. Call controls (mic, type, end) and the settings menu.
4. Conversation column: user messages tinted, agent replies in serif, live sentence.
5. Now list, plus the faster state refresh while a job runs.
6. Background tasks.
7. Restyle the preview pane and add the preview-open layout.
8. Tests, probe selectors and a browser check (section 7).

## 5. Files

**Will change:**
- `apps/web/index.html`, `apps/web/style.css`, `apps/web/app.js`
- `apps/web/preview.js` (restyle hooks only)
- New: `apps/web/stage.js` (orb and waveform) and `apps/web/activity.js` (Now list), each
  a classic script exposing a small API so the Node tests can load it
- `apps/web/tests/*.cjs`: new tests for the stage and activity, plus updates to existing tests
- `services/voice/probes/ui_probe.mjs`: selector updates only (see below)
- `apps/web/README.md`

**Will not change:** anything under `services/voice/clawvoice/`, `infra/`, `services/voice/agents/`.

**Element IDs and classes to keep,** because `app.js`, the Node tests or the browser
probe depend on them: `#connect`, `#disconnect`, `#mute`, `#push-talk`, `#ptt-mode`,
`#typed-only`, `#message`, `#send`, `#composer`, `#transcript`, `#jobs`, `#jobs-count`,
`#topics`, `#cache-metrics`, `#tools`, `#events`, `#latency`, `#model-badge`,
`#gateway-badge`, `#banner`, `#agent-selector`, `#agent-name`, `#agent-role`,
`#agent-state`, `#orb`, `#bot-audio`, all `#preview-*`, `.turn.user`, `.turn.bot`,
`.turn.notice`, `.turn .text`, `.hint`, `.job`, `.job .chip`.

**Probe updates needed:**
- `#ptt-mode` and the badges move into the settings menu. The probe must open the menu
  before `#ptt-mode` can be checked. Text-based checks on hidden badges still pass.
- The probe selects agents with `[data-agent-id="astra"]`, but the app renders
  `data-agent`. This mismatch already exists before the redesign; fix it in the same pass.

## 6. Permissions and commands needed

### File access
- Read and write in `apps/web/`, `services/voice/probes/ui_probe.mjs` and `docs/`.
- Read only everywhere else.

### Commands (no approval needed beyond the normal tool prompts)
| Purpose | Command |
|---|---|
| Web unit tests (Windows) | `node --test apps/web/tests/agent_selector.test.cjs apps/web/tests/microphone.test.cjs apps/web/tests/preview.test.cjs` plus the new test files |
| Start or restart the app | Browser pane `preview_start` with `clawvoice` from `.claude/launch.json` (runs the server in WSL as `dev`) |
| Check the server log | `preview_logs` |
| Browser checks | Built-in browser on `http://localhost:7860` and `http://127.0.0.1:18950`; viewport and light/dark emulation |
| Stand-in preview site | `docker exec <astra container> python3 -m http.server 18950 --directory /tmp/preview-demo` (temporary files only; never touches Astra's workspace) |

### Needs your explicit OK each time
| Action | Why it needs a yes | Command |
|---|---|---|
| Approve the WebSocket pairing | Grants the app's device operator read/write on Astra's gateway (an owner decision per `docs/voice.md`). Needed only for the coding-agent tool lines in "Now" | `python3 infra/openclaw/manage.py --agent astra openclaw devices list`, then `… devices approve <request-id>`, run in Ubuntu as `dev` from the repo |
| A real end-to-end voice test | Uses your Boson key and the ChatGPT account Astra is signed in to, and makes Astra build a real project in its workspace | Connect in the app (typed-only) and ask Astra to build a page |
| Live browser probe | Same costs as above, and drives Tari, which isn't set up here | `node services/voice/probes/ui_probe.mjs --live` |
| Commit | Nothing is committed without asking | `git commit` on `astra-coding-agent-frontend` |
| Push | Never without asking. Never open a PR or merge to `main` | — |

## 7. Verification checklist

- [ ] Node tests pass: existing, preview, stage and activity.
- [ ] Light and dark mode both checked at 1500 px and 1024 px wide.
- [ ] Orb: idle, you speaking, agent speaking, thinking and working each look distinct;
      with reduced motion there is no animation.
- [ ] Conversation shows the live sentence and keeps notices.
- [ ] Now list fills from real `get_context` and `delegate_task` events and clears on a new request.
- [ ] Background task appears, runs, completes, and cancels.
- [ ] Preview-open layout: orb row, stacked sections, no horizontal scrollbar.
- [ ] Tari: no preview, no "Now" coding lines; its setup problem shows as one calm line.
- [ ] Probe selectors updated; live probe run only with your OK.

## 8. Current local setup (for reference)

- WSL Ubuntu user `dev` runs the server and the OpenClaw manager. Don't run git from
  Ubuntu on this checkout: it misreads Windows line endings as changes.
- The voice environment is in `/home/dev/.cache/clawvoice/` and is started by `.claude/launch.json`.
- `services/voice/.env` holds the Boson key and `CLAWVOICE_PERSIST_TRACES=false` (traces
  written to `C:` from WSL caused audio stutter).
- Astra's OpenClaw state and workspace are in `/home/dev/.local/share/clawvoice/`; the
  gateway is on port 18949 and the preview on port 18950.
- Tari's OpenClaw isn't set up on this machine.
