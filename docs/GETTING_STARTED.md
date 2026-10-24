# Getting started: run and test ClawVoice (Priority 1 baseline)

Baseline: branch `release/p1-stable`, tag `v0.1.0`. One voiced agent (Tari)
on Boson Higgs Realtime 1.5 Preview + Pipecat, backed by a real OpenClaw agent.
Full reference: [voice.md](voice.md). Evidence: [voice-validation.md](voice-validation.md).

## Prerequisites (once)

- macOS with Docker Desktop, Python 3.11, Node 22 (only for the browser probe).
- The isolated demo OpenClaw initialized and authorized:
  [openclaw-setup.md](openclaw-setup.md) (`manage.py init`, ChatGPT OAuth, the
  separate Google account). Never the production Tari installation.
- A Boson key in `experiments/higgs-realtime-1.5/.env` or `services/voice/.env`
  (`BOSON_API_KEY`); see `services/voice/.env.example`.

## Start

```sh
cd openclaw-voice                                  # repository root
python3 infra/openclaw/manage.py start             # demo OpenClaw Gateway (Docker)
python3 infra/openclaw/manage.py update-workspace  # safe to repeat: upgrades known skill/helper templates
cd services/voice
make setup                                         # first time only (uv 0.13.5 + locked deps)
make dev                                           # http://127.0.0.1:7860
```

Open **http://127.0.0.1:7860**, click **Connect**, allow the microphone (use
headphones; enable **Push to talk** in noisy rooms). Ctrl+C stops the server;
running background jobs are kept and recovered where possible.

## Try it

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

## Test

Offline (no provider calls):

```sh
python3 -m unittest discover -s infra/openclaw/tests   # Gateway setup, Google wrapper, planner, unified schedule
node --test apps/web/tests/microphone.test.cjs          # browser mic / push-to-talk gate
cd services/voice && make check                         # voice runtime: pytest + Ruff + compile
```

Live (real Boson, OpenClaw and Google; with `make dev` running, from
`services/voice`). Probes create only uniquely named `ClawVoice …` items and
remove them:

```sh
.venv/bin/python probes/voice_probe.py --live --scenario a   # voice, barge-in, reconnect
.venv/bin/python probes/voice_probe.py --live --scenario i   # cache hit vs live OpenClaw lookup
.venv/bin/python probes/voice_probe.py --live --scenario j   # stop speaking, notice dismissal
.venv/bin/python probes/p1_probe.py --live                   # Gmail latest + unified schedule lifecycle
node probes/ui_probe.mjs --live                              # browser path
.venv/bin/python probes/voice_probe.py --help                # all scenarios (a-j)
```

## Optional: WebSocket transport

Tari works over the Gateway's HTTP API. To use the WebSocket transport (run IDs,
real abort, recovery after a restart), approve the device ClawVoice requests,
an owner decision:

```sh
python3 infra/openclaw/manage.py openclaw devices list
python3 infra/openclaw/manage.py openclaw devices approve <requestId>
curl -X POST http://127.0.0.1:7860/api/agents/tari/gateway/reconnect
```

Troubleshooting: [voice.md#troubleshooting](voice.md#troubleshooting).
