# openclaw-voice

Voice rooms where AI agents talk to each other and to you, out loud, on
[Boson Higgs Realtime](https://docs.boson.ai/models/higgs-realtime/overview).

Every agent runs on the same harness: a **voice** session that hears the
room's real audio and speaks, and a private **mind** session that decides when
to speak, cut in, or back off. There is no orchestrator. The only shared piece
is an audio bus that carries, paces and records sound, and each agent decides
for itself. The web page shows each agent's private thoughts live.

## Run it

Double-click **`start.bat`** (Windows). The first time, it installs
dependencies and opens `.env` for your Boson API key. Then it starts the server
and opens http://localhost:3000. Use Chrome, and headphones if you talk to it,
so the agents don't hear themselves through your mic.

To see the agents' private thoughts, double-click **`demo-thoughts.bat`**
instead. It opens the "Ship or slip, with private thoughts" preset, where both
agents have a private mind and their thoughts show live on their tiles. If the
server is already running, either launcher just opens the page.

Or, by hand:

```bash
npm install
npm run web
```

Set up 2–4 agents (role, goal, facts they'll defend, voice, model), describe
the situation, then talk or type to join in.

## Agent selector demo

A simpler, Pipecat-style demo (after Boson's
[basic Pipecat integration](https://docs.boson.ai/models/higgs-realtime/integrations/pipecat)):
you talk to **planner**, **coder** and **jester**, one at a time. A silent
**overseer** listens to every turn you take and has one tool, `call_agent`,
that hands the conversation to whoever fits. The others are sidelined: quiet,
but they hear everything as real audio, so whoever takes over knows the
conversation. Planner starts. Click a tile to switch yourself.

Double-click **`selector.bat`**, or open http://localhost:3000/selector.html.

- Everything runs on Higgs 1.5. The overseer is a text-only session with server
  VAD: it never makes a sound, routes on your raw audio, and its turn detection
  drives everyone. Agents run manual turns. A failed overseer decision
  (1.5's occasional `upstream_error`) is retried once.
- Talking over an agent cuts it off. A switch cuts the old agent and the new one replies.
- Measured 2026-10-24: an agent is audible ~0.5–0.8 s after it's asked; after a
  switch, ~1.3–2.3 s. The overseer decides 0.5–1.9 s after you stop, so the old
  agent is sometimes heard for a moment before it's cut. A failed agent reply is
  retried once, about 2 s later.
- `npx tsx tools/probe/selectorClient.ts [port]` talks to it with synthesized speech and prints what happens.

## Models

Each agent picks **1.5 voice · 1 mind** (recommended), **Higgs 1.5**, or **Higgs 1**:

- 1.5 sounds like speech and argues better.
- Higgs 1 is faster and doesn't fail under load.

The hybrid gives you 1.5 for what you hear and Higgs 1 for fast decisions.
See [docs/model-comparison.md](docs/model-comparison.md).

## Latency

Measured from the moment someone stops talking until an agent is audible, on
hybrid agents: **about 1.0–1.2 s**, down from 2.4–2.6 s. At a 200 ms pause, each
listener's mind decides while its voice drafts a reply, side by side. The turn
counts as over after 500 ms of silence, and the draft plays if the mind said
speak. If the speaker carries on, or the mind says wait, the draft is thrown
away unheard. Every reply logs its own timeline in the transcript.

With a mic, add about 0.3 s (the voice gate waits that long before deciding
you've stopped) and about 0.1 s of browser buffering.

## Cost

Higgs Realtime bills by tokens: $0.75 per 1M input, $0.25 per 1M cached input,
$4.50 per 1M output ([docs.boson.ai/pricing](https://docs.boson.ai/pricing.md)).
Every response reports its usage, so the app tracks it per agent, split into
**speech** (voice) and **thoughts** (private mind):

- **Web app:** a running total and per-minute rate in the header, and speech vs thoughts on each agent's tile.
- **Server log:** a usage line in `captures/web-*.log` when a room closes.
- **Demo:** `npm run demo` ends with a cost summary. Add `--no-mind` to compare against voice-only agents.

Measured on the scripted standup (2 agents, 3 runs per mode of ~1.3–1.8 min
each, 2026-10-24): about **$0.007/min voice-only** and about **$0.043/min
with private minds** (hybrid). That's ~6× more, per minute and per spoken
turn. Minds were ~90% of the cost. Each mind decides ~20 times a minute and
re-reads the whole conversation every time: ~2,800 input tokens per decision
on average (over 90% cached), and growing as the conversation gets longer.

These are estimates from reported tokens. Boson's own per-minute audio figures
($0.014/min output) are higher, so check the real spend on the
[usage page](https://www.boson.ai/workspace/billing/usage). You can also set
a monthly cap there; at the cap, sessions close with code `4429`.

## Other tools

- `npm run demo`: a scripted headless conversation. Writes a WAV and a
  transcript to `captures/`.
- `npm run probe`: checks the live API behaviour the design depends on
  ([docs/probe-results.md](docs/probe-results.md)).
- `npm run probe -- compare`: Higgs 1 vs 1.5 side by side.
