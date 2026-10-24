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

Or, by hand:

```bash
npm install
npm run web
```

Set up 2–4 agents (role, goal, facts they'll defend, voice, model), describe
the situation, then talk or type to join in.

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

## Other tools

- `npm run demo`: a scripted headless conversation. Writes a WAV and a
  transcript to `captures/`.
- `npm run probe`: checks the live API behaviour the design depends on
  ([docs/probe-results.md](docs/probe-results.md)).
- `npm run probe -- compare`: Higgs 1 vs 1.5 side by side.
