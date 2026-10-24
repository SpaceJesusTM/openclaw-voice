# ClawVoice design language

How the ClawVoice web UI (`apps/web/`) looks and why. Read this before adding or
changing anything on screen. The tokens live at the top of `apps/web/style.css`;
this document explains the decisions behind them.

## The idea in one paragraph

ClawVoice is a voice conversation with an agent that also does real work. The screen
exists to make three things obvious at a glance: **who is talking right now**, **what
the agent is actually doing**, and **what it has built**. Everything else is quiet.
The look is warm, calm and editorial: off-white or charcoal surfaces, hairline
borders, a serif for the agent's voice, and one warm color, clay, that belongs to the
agent alone.

## Principles

1. **The voice is the focus.** The orb and its waveform are the largest, most alive
   thing on screen. Nothing else animates for attention while someone is speaking.
2. **Typography says who is speaking.** The agent's words and name are serif; your
   words and the interface are sans. A reader can tell the speaker before reading.
3. **Clay means the agent.** `--clay` marks the agent's voice, its thinking or
   working motion, and the current step in "Now". Never use it for your own voice,
   for buttons you press, or for decoration. Your voice is ink.
4. **One window.** The page never scrolls. Regions share the window height, and a
   region that overflows (the conversation, the task list) scrolls inside itself.
   New content must fit this model, not push the page taller.
5. **Quiet by default.** No rows of pills, badges or chips. Status is a word, a small
   dot or a thin line. Developer information (model, gateway, cache, logs) lives in
   the settings menu, not on the main surface.
6. **Honest UI.** Show only what really happened, from data the page actually
   receives. No predicted next steps, no invented progress percentages, no claims
   about a model's private reasoning. If something isn't known, don't draw it.
7. **Motion explains change.** Animate to show that something arrived, moved or
   finished (the preview sliding in, a new site fading over the old). Don't animate
   for decoration. Everything stops under `prefers-reduced-motion`.

## Tokens

Defined on `:root` in `style.css`, with dark values under `prefers-color-scheme: dark`.
Always use the token, never a raw color.

| Token | Light | Dark | Use |
|---|---|---|---|
| `--canvas` | `#faf9f5` | `#262624` | Page background |
| `--surface` | `#ffffff` | `#30302e` | Panels, popovers, inputs |
| `--surface-2` | `#f0eee6` | `#3a3a37` | Your messages, pressed states, preview backdrop |
| `--ink` | `#1f1e1d` | `#ecebe6` | Primary text, your voice, the main call button |
| `--muted` | `#73726c` | `#a6a49c` | Labels, secondary text, idle icons |
| `--faint` | `#a6a39a` | `#75736c` | Hints, empty states, counts, addresses |
| `--line` | 10% ink | 9% white | Hairline borders, hover washes |
| `--line-strong` | 18% ink | 17% white | Control outlines, input borders |
| `--clay` | `#d97757` | `#d97757` | The agent only (see principle 3) |
| `--clay-wash` | 14% clay | 20% clay | The orb's fill while the agent speaks |
| `--good` / `--warn` / `--bad` | muted green / amber / red | lighter | Status dots and status words only |

Never put status color on large areas. A failed state is a red dot or a red word,
never a red block.

### Type

| Token | Stack | Use |
|---|---|---|
| `--serif` | `ui-serif`, Iowan Old Style, Palatino, Georgia | Agent name, agent replies, wordmark, preview title |
| `--sans` | `system-ui`, Segoe UI, Roboto | Everything else |
| `--mono` | `ui-monospace`, Cascadia Mono, Menlo, Consolas | Agent tool steps, topic keys, addresses, logs |

No web fonts, so the app works offline. Weights are 400 and 500 only. Section labels
are 12.5px muted sans in sentence case. Body is 14.5–15px; agent replies are 16.5px serif.

### Shape and spacing

- Hairline borders are `1px solid var(--line)`. Cards are rare; prefer whitespace
  and a section label over a box.
- Radius: 8–10px for small controls, 14–16px for panels and inputs, full circles for
  call buttons, `999px` only for the few floating captions and toasts.
- Spacing comes in steps of roughly 10 / 14 / 18–22 / 28–40px. Larger gaps separate
  regions; smaller ones group items.
- Shadows only on floating layers (the settings popover). Nothing in the page flow
  casts a shadow.

### Motion

- Easing: `--ease-out` (`cubic-bezier(.16, 1, .3, 1)`). Durations 160ms for hover and
  press, 300–560ms for layout changes.
- Layout changes grow or shrink a region (flex or grid tracks) while its content
  slides a few pixels and fades. Never pop.
- Replacing content cross-fades: load the new version behind the old one, then fade it
  in on top (see `reload()` in `preview.js`). Never flash white or empty.
- On page load, things that are already true appear in place (`body.booting`
  suppresses transitions for the first 1.5s). Entrance animations are for changes
  that happen while you're watching.
- `prefers-reduced-motion: reduce` turns off every animation and transition.

## Layout

Three regions, always inside one window:

```
┌ top bar: wordmark · agent tabs · (Preview) ⚙ ───────────────────────┐
│ stage: orb + name + state + call controls                            │
│ panels: Conversation │ Now / Background tasks       │ preview pane   │
└───────────────────────────────────────────────────────────────────────┘
```

- **Default:** the stage is centered on top; the panels below split about 1.55 : 1.
- **Preview open (≥ 1180px):** the left column narrows to 440px, the stage becomes a
  single row (small orb, name, controls), and the panels stack. The preview takes the
  rest of the width at full height.
- **Under 1180px:** the stage stays on top, the preview becomes a band beneath it, and
  conversation and the side lists sit side by side below. Under 760px they stack.
- The orb's size comes from the window height: `clamp(128px, 30vh, 250px)`.

When adding a region, give it a share of the height (`flex` or `grid-template-rows`
with `minmax(0, …)`), set `min-height: 0`, and let its own list scroll. Verify
`document.documentElement.scrollHeight <= innerHeight` at 1440×900, 1024×760 and
1280×620.

## Components

**Orb (`stage.js`).** A circle with 64 radial bars, drawn on a canvas from the audio
analysers app.js already creates. It reads its state from the classes app.js writes on
`#orb`:

| State | Look |
|---|---|
| Not connected | Faint ring, flat bars |
| Ready | Gentle breathing |
| You speaking | Ink bars from your microphone's spectrum |
| Agent speaking | Clay bars from the agent's audio, clay-tinted fill |
| Thinking | A soft ripple in neutral bars |
| Working (a background task) | A slow clay arc inside the ring, on top of any other state |
| Has an update waiting | A small clay dot |

Keep the orb abstract: no faces, no icons inside it, no text on it.

**Call controls.** Three round buttons: keyboard (type instead), the main button, and
end. The main slot shows exactly one of Connect, Mute or Hold to talk, chosen by CSS
from `body[data-call]` and `body[data-ptt]`. Labels live in `aria-label`, not visible
text. The end button stays in place but invisible when not connected, so the row
doesn't shift.

**Conversation.** Your messages: right-aligned, `--surface-2` background, sans.
Agent replies: left-aligned, no background, serif. Notices (task finished, disconnected)
are centered 12.5px muted lines. No avatars, no name labels (they're kept for screen
readers only).

**Now (`activity.js`).** A short list of what the agent did this turn: what it heard,
thinking, lookups (with cache state and timing), handoffs, the coding agent's tool
steps (mono), and task outcomes. The newest step is at the bottom; the current one is
ink with a clay icon. It clears on each new request and holds at most seven items.
Every line comes from a real event; see the "Honest UI" principle.

**Background tasks.** Title in 500 weight; status as a plain word on the right
(`running · 42s`, `completed · verified`); a thin clay line that sweeps while it runs
(not a percentage); the result clamped to two lines with the full text on hover;
"Cancel" as a small underlined text button. Ids, transport and timings go in the
`title` tooltip.

**Preview pane (`preview.js`).** A large rounded panel showing the agent's live site.
The header holds a status dot (clay while building, green when live), a word, the
project name in serif, and icon buttons. A wireframe shimmers while it builds; the site
fades in when live; updates cross-fade. It never covers the conversation.

**Settings popover.** The only floating layer. It holds connection status (dot and
text), voice options (checkboxes), fast-context freshness and diagnostics. It closes
on outside click or Escape.

**Problem line (`#banner`).** One calm line with a small status dot, scoped to the
selected agent. Say what's wrong and what it affects in plain words.

## Writing

- Sentence case everywhere. No ALL CAPS, no Title Case labels.
- Short and plain: "Checked the active project · fresh · 4 ms", "Handed off to the
  agent", "Quiet for now."
- No "please", no exclamation marks, no "successfully". Ellipsis only for things in
  progress ("Connecting…").
- The interface never speaks as "I". Only the agent, in the conversation, says "I".

## Honesty rules (data)

Before drawing anything, find the event or field that proves it. Today the page has:

- Audio levels for both sides (analysers), and the speaking and thinking events.
- Transcripts (`user-transcription`, `bot-llm-text`).
- Voice-tool calls (`clawvoice.tool` started and result: name, arguments, status,
  cache, topic, read time, job id).
- Jobs (`/api/state` and `clawvoice.job`: title, status, age, outcome, verification,
  `tools_seen`).
- The preview server's liveness (a browser probe of port 18950).

Not available, so never shown: which file the agent is editing, progress percentages,
the models' private reasoning, and plans or next steps the agent hasn't taken. Adding
any of these needs a backend change first; don't fake them in the UI.

## Accessibility

- Every icon-only button has an `aria-label` or visually hidden text.
- Focus rings use `:focus-visible` with a 2px clay outline.
- Live regions (`aria-live="polite"`) on the transcript, Now and tasks.
- Contrast: text tokens stay readable on both canvases; never put muted text on
  `--surface-2`.
- Reduced motion is respected everywhere.

## Adding something new: checklist

1. Can you name the real data it shows? If not, stop.
2. Does it belong on the main surface, or in settings? Default to settings.
3. Use existing tokens; if you need a new one, add it to both light and dark.
4. Agent-related emphasis is clay; your own is ink; status uses the dot pattern.
5. Give it a share of the window height; it scrolls inside, the page doesn't.
6. Keep the element IDs that `app.js`, the tests and `services/voice/probes/ui_probe.mjs`
   use (listed in `docs/ui-redesign.md`).
7. Add a Node test for any logic (pure functions in a classic script, as in `stage.js`,
   `activity.js` and `preview.js`), then check light and dark at the three sizes above.

## Files

| File | Role |
|---|---|
| `apps/web/style.css` | Tokens, layout, all component styles |
| `apps/web/index.html` | Structure and the stable element IDs |
| `apps/web/app.js` | Connection, state, rendering, event wiring |
| `apps/web/stage.js` | Orb and waveform |
| `apps/web/activity.js` | The "Now" list |
| `apps/web/preview.js` | Live preview pane |
| `apps/web/tests/*.cjs` | Offline tests (`node --test <file>`) |
| `docs/ui-redesign.md` | The redesign plan, data mapping and permissions |
