# openclaw-voice: agent selector

Talk to **planner**, **coder** and **jester**, out loud, on
[Boson Higgs Realtime](https://docs.boson.ai/models/higgs-realtime/overview).
You talk to one at a time. A silent **overseer** listens to every turn you take
and has one tool, `call_agent`, that hands the conversation to whoever fits.
The others are sidelined: quiet, but they hear everything as real audio, so
whoever takes over knows the conversation. Planner starts. Click a tile to
switch yourself.

It follows Boson's
[basic Pipecat integration](https://docs.boson.ai/models/higgs-realtime/integrations/pipecat)
(speech-to-speech sessions fed by the browser mic), written in TypeScript on
raw WebSockets. How the silent overseer works, and everything learned building
it: [docs/silent-overseer.md](docs/silent-overseer.md).

## Run it

Double-click **`start.bat`** (Windows). The first time, it installs
dependencies and opens `.env` for your Boson API key. Then it starts the server
and opens http://localhost:3000. Use Chrome, and headphones, so the agents
don't hear themselves through your mic. If the server is already running, it
just opens the page.

Or, by hand:

```bash
npm install
npm run web
```

## How it behaves

- **Everything runs on Higgs 1.5** (`BOSON_REALTIME_MODEL` in `.env`). The
  overseer is a text-only session with server VAD: it never makes a sound,
  routes on your raw audio, and its turn detection drives everyone. Agents run
  manual turns.
- **Drafting early:** at a 200 ms pause the agent you're talking to starts a
  held reply. It plays once your turn is over, or is dropped unheard if you
  keep talking. Measured 2026-10-24: replies are heard **0.9–1.45 s** after you
  stop. Each line in the page shows its time.
- **Barge-in:** talking over an agent cuts it off.
- **Graceful swaps:** the old agent finishes its clause and says a short
  handoff line ("Actually, Coder's better with this one") while the new
  agent's reply generates. The new agent plays right after (~0.1 s), opening
  with something like "I can help with that". About 5–6 s from when you stop.
- **Failures:** 1.5 sometimes fails a response (`upstream_error`). A failed
  overseer decision, or an agent reply that produced no audio, is retried once.
- **Background tasks:** ask any agent for work ("Coder, code up a login page in
  the background"). When a task finishes or needs your OK, **whoever you're
  talking to** tells you at the next natural pause ("By the way, Coder has the
  login page ready and wants your OK"), or in first person if it's their own
  task. Answer out loud (the overseer hears it and passes it on), or with
  Approve / Deny on the page's Tasks panel.
  Tasks are fake timers for now; start the `web-fast-tasks` launch config
  (`SELECTOR_TASK_SCALE=0.3`) to make them quick.

## Cost

Higgs Realtime bills by tokens: $0.75 per 1M input, $0.25 per 1M cached input,
$4.50 per 1M output ([docs.boson.ai/pricing](https://docs.boson.ai/pricing.md)).
Every response reports its usage. The page header shows a running total and a
per-minute rate, and the server log (`captures/web-*.log`) has a usage line
when a session closes. The scripted tests cost about $0.002–0.004 per minute
(four sessions, one speaking at a time). These are estimates from reported
tokens; check the real spend on Boson's
[usage page](https://www.boson.ai/workspace/billing/usage).

## Tools

- `npx tsx tools/probe/selectorClient.ts [port] [routing|tasks]`: talks to a
  running server with synthesized speech, the way the page does, and prints
  what happens. `tasks` runs a background task through approval and done.
- `npm run probe`: checks the live API behavior the design depends on
  ([docs/probe-results.md](docs/probe-results.md)). The selector's own probes
  (`vadToggle`, `cancelEarly`, `responseIds`, `transcriptLength`) are in
  `tools/probe/`; run each with `npx tsx`.
- Model notes: [docs/model-comparison.md](docs/model-comparison.md).

Every decision (turns, drafts, swaps, cuts, reply outcomes, latency) is logged
to `captures/web-*.log`.
