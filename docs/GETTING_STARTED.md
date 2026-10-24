# Getting started: run and test ClawVoice

Baseline: branch `release/p1-stable`, tag `v0.1.0`: one voiced agent (Tari) on
Boson Higgs Realtime 1.5 Preview + Pipecat, backed by a real OpenClaw agent.
Branch `feat/p2a-team-voice` adds Astra (a coding agent) and **Team mode**, both
agents in one room ([team-voice.md](team-voice.md)).
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
node --test apps/web/tests/microphone.test.cjs apps/web/tests/agent_selector.test.cjs  # mic gate, agent/Team selector
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

## Team mode: Tari and Astra together

Start both isolated OpenClaw agents (Astra once initialized and authorized, see
[infra/openclaw/README.md](../infra/openclaw/README.md#isolated-astra-coding-agent---agent-astra)),
then the voice server:

```sh
python3 infra/openclaw/manage.py start                          # Tari's Gateway (127.0.0.1:18939)
python3 infra/openclaw/manage.py update-workspace
python3 infra/openclaw/manage.py --agent astra start            # Astra's Gateway (18949) and preview port (18950)
python3 infra/openclaw/manage.py --agent astra update-workspace
cd services/voice && make dev                                   # http://127.0.0.1:7860
```

Open the page, choose **Team**, click **Connect** once (headphones). Start every
request with a name. Tari and Astra each have a seat that shows speaking, waiting to
speak, thinking, listening and background work; the transcript labels who spoke.

| Say | Expected |
| --- | --- |
| "Hey Tari, what's on my calendar tomorrow?" | Tari answers (cache or live lookup) |
| "Okay Astra, what coding projects are you working on?" | Astra answers from its project cache |
| "Astra, create a simple personal portfolio website." | Astra: "On it"; a real coding job runs (Astra's seat shows work) |
| "Hey Tari, what's my next meeting?" (while Astra works) | Tari answers at once; Astra keeps working |
| "Tari, explain my schedule tomorrow in a little more detail." | Tari answers; if Astra's job finishes meanwhile, its announcement waits for a pause |
| (after Astra announces) open http://127.0.0.1:18950 | The real site Astra built |
| "Astra, change the main heading to My Personal Portfolio." | A real edit, announced only after read-back |
| "Hey Tari, what's on my planner today?" then at once "Okay Astra, what's the latest website update?" | Both answer, one after the other, never together |
| "What's the weather like?" (no name) | Nobody answers; marked "not addressed" |
| Talk over an agent / "Stop." / "Tari, stop." | Speech stops (everyone's / Tari's); jobs keep running |
| "Astra, cancel the website task." | Only Astra's job is cancelled |
| Switch to **Tari** or **Astra** | The single-agent modes, unchanged; jobs and history are kept |

Quick automated check (live, nothing changed; `--notice` adds a read-only OpenClaw question):

```sh
cd services/voice && .venv/bin/python probes/team_probe.py --live --speech --stop
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
