# Getting started: run and test Chorus

This guide expands the [README Quickstart](../README.md#quickstart): how to start
Chorus, what to try in each mode, and how to test it. References:
[voice runtime](voice.md) · [Team mode](team-voice.md) ·
[Higgs overseer](higgs-overseer.md) · evidence: [voice-validation.md](voice-validation.md).

## Prerequisites (once)

- macOS with Docker Desktop, Python 3.11, and Node 22 (only for the browser tests and
  probe).
- Tari's and Astra's isolated OpenClaw installations initialized and signed in
  ([README Quickstart](../README.md#quickstart) steps 3 and 4; full procedure in
  [openclaw-setup.md](openclaw-setup.md)), plus Tari's Google authorization for
  Calendar and Gmail. Never use a production assistant's installation.
- A Boson key: `BOSON_API_KEY` in `services/voice/.env` (copy
  `services/voice/.env.example`). A key in an existing
  `experiments/higgs-realtime-1.5/.env` is also picked up.

## Start

```sh
cd openclaw-voice                                       # repository root
python3 infra/openclaw/manage.py start                  # Tari's Gateway (127.0.0.1:18939)
python3 infra/openclaw/manage.py --agent astra start    # Astra's Gateway (18949) and preview port (18950)
cd services/voice
make setup                                              # first time only (uv 0.13.5 + locked deps)
make dev                                                # http://127.0.0.1:7860
```

Jester is voice-only and starts with the voice server. After pulling changes, refresh
the agents' workspace templates from the repository root (safe to repeat):

```sh
python3 infra/openclaw/manage.py update-workspace
python3 infra/openclaw/manage.py --agent astra update-workspace
```

Open **http://127.0.0.1:7860**, choose a tab, click **Connect** and allow the
microphone (use headphones; enable **Push to talk** in noisy rooms). Ctrl+C stops the
server; running background jobs are kept and recovered where possible. Stop the
OpenClaw containers with `python3 infra/openclaw/manage.py stop` and
`python3 infra/openclaw/manage.py --agent astra stop` (state is kept).

## Try it: Tari on its own

| Say | Expected |
| --- | --- |
| "What's on my Calendar today?" | Instant answer from the hot-context cache |
| "What events do I have in January 2027?" | "On it": read-only live OpenClaw lookup, then a spoken result |
| "Create a Calendar event tomorrow from 3 to 4 PM called Demo Sync." | Background job; announced only after an independent read-back |
| While it runs: "What's on my planner tomorrow?" | Answered immediately |
| "Did that finish?" | Status from tracked jobs |
| "What's the most recent email in my Primary Inbox?" | Read-only Gmail lookup of the newest Primary-tab message |
| Talk over Tari / "Stop talking." | Speech stops; "stop" stays silent; jobs keep running |
| "Cancel that task." | Explicit job cancellation (separate from stopping speech) |
| "Delete the Demo Sync event." | Removes the event you created |

The **Astra** tab works the same way for coding ("Create a simple website for a
fictional coffee shop."), and the **Jester** tab is a plain conversation ("Tell me
something funny.").

## Team mode: Tari, Astra and Jester together

Open **http://127.0.0.1:7860/?agent=team**, click **Connect** once (headphones) and
talk naturally. A silent Higgs overseer picks the agent for each turn with a
`call_agent` tool call: follow-ups stay with the current agent, a clear change of
topic switches, and a name always wins ("Hey Jester, ..."). Each agent has a seat that
shows speaking, waiting to speak, thinking, listening and background work; the
transcript labels who spoke.

| Say | Expected |
| --- | --- |
| "What's on my calendar tomorrow?" | Tari answers (cache or live lookup) |
| "Okay Astra, what coding projects are you working on?" | Astra answers from its project cache |
| "Astra, create a simple personal portfolio website." | Astra: "On it"; a real coding job runs; the preview pane opens when the site is served |
| "What's my next meeting?" (while Astra works) | Tari answers at once; Astra keeps working |
| "Hey Tari, ..." while Astra is still talking | Astra stops for good (no resumed acknowledgement), Tari answers next; Astra's job keeps running |
| "Explain my schedule tomorrow in a little more detail." | Tari answers; if Astra's job finishes meanwhile, the UI shows it and the announcement waits for a natural break |
| (after Astra announces) open http://127.0.0.1:18950 | The real site Astra built |
| "Astra, change the main heading to My Personal Portfolio." | A real edit, announced only after read-back |
| "Tell me a really funny joke." | Jester answers |
| "Another one." | Stays with Jester |
| "Jester, roast that website." | Jester, by name |
| Talk over an agent / "Stop." / "Tari, stop." | Speech stops (everyone's / Tari's); jobs keep running |
| "Astra, cancel the website task." | Only Astra's job is cancelled |
| Switch to **Tari**, **Astra** or **Jester** | One-on-one modes; jobs and history are kept |

**Name-only routing.** Set `CLAWVOICE_TEAM_ROUTER_MODE=names` in `services/voice/.env`
and restart the server. Every request must then start (or end) with a name, and
unnamed requests go to nobody (marked "not addressed"). See
[team-voice.md](team-voice.md#addressing).

## Test

Offline (no provider calls), from the repository root:

```sh
python3 -m unittest discover -s infra/openclaw/tests   # Gateway setup, Google wrapper, planner, unified schedule
node --test apps/web/tests/*.test.cjs                   # browser UI: mic gate, tabs, Team seats, preview, orb
cd services/voice && make check                         # voice runtime: pytest + Ruff + compile
```

Live (real Boson, OpenClaw and Google; with `make dev` running, from
`services/voice`). The Tari probes create only uniquely named `ClawVoice …` items and
remove them:

```sh
.venv/bin/python probes/voice_probe.py --live --scenario a   # voice, barge-in, reconnect
.venv/bin/python probes/voice_probe.py --live --scenario i   # cache hit vs live OpenClaw lookup
.venv/bin/python probes/voice_probe.py --live --scenario j   # stop speaking, notice dismissal
.venv/bin/python probes/p1_probe.py --live                   # Gmail latest + unified schedule lifecycle
node probes/ui_probe.mjs --live                              # browser path
.venv/bin/python probes/voice_probe.py --help                # all scenarios (a-j)
```

Team mode (read-only: nothing is created or changed):

```sh
# Overseer routing: four spoken turns, actual call_agent decisions, one speaker at a time
.venv/bin/python probes/overseer_probe.py --live --url http://127.0.0.1:7860
# Name-only router (set CLAWVOICE_TEAM_ROUTER_MODE=names in services/voice/.env and restart first)
.venv/bin/python probes/team_probe.py --live --speech --stop
```

## Optional: WebSocket transport

Tari and Astra work over the Gateway's HTTP API. To use the WebSocket transport (run
IDs, real abort, recovery after a restart), approve the device Chorus requests, an
owner decision:

```sh
python3 infra/openclaw/manage.py openclaw devices list
python3 infra/openclaw/manage.py openclaw devices approve <requestId>
curl -X POST http://127.0.0.1:7860/api/agents/tari/gateway/reconnect
```

For Astra, use `python3 infra/openclaw/manage.py --agent astra openclaw devices ...`
and `/api/agents/astra/gateway/reconnect`.

Troubleshooting: [README](../README.md#troubleshooting) and
[voice.md#troubleshooting](voice.md#troubleshooting).
