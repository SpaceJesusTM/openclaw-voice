# Chorus



**Your entire AI agent team, at the tip of your tongue.**

Chorus is a real-time voice harness for talking to several independently configured
AI agents in one natural conversation. Each agent keeps its own backend, tools,
memory, permissions and voice. Chorus listens, routes each request to the right
agent, lets one agent speak at a time, and keeps everyone's background work running
while you talk.

Voice runs on **Boson Higgs Realtime 1.5 Preview** (`higgs-realtime-1.5-preview`)
through **Pipecat**; the demo's working agents run on **OpenClaw**.

- [Why Chorus?](#why-chorus)
- [Meet the demo team](#meet-the-demo-team)
- [Features](#features)
- [How it works](#how-it-works)
- [Quickstart](#quickstart)
- [Using Chorus](#using-chorus)
- [Configuring your own agents](#configuring-your-own-agents)
- [Troubleshooting](#troubleshooting)
- [Status and roadmap](#status-and-roadmap)
- [Documentation](#documentation)

## Why Chorus?

Specialized AI agents are useful, but working with several of them usually means
juggling separate chat windows, or squeezing everything through one generic
assistant that does not have their tools, memory or permissions.

Chorus keeps the agents separate and puts them in one room. You speak naturally; a
silent Higgs "overseer" decides which agent should handle each turn, the chosen agent
answers in its own voice, and work it starts (a calendar change, a website build)
continues in the background while the conversation moves on. When the work is done
and checked, the agent tells you at a natural pause.

## Meet the demo team

| Agent | Role | Backend | Higgs voice |
| --- | --- | --- | --- |
| 🟢 **Tari** | Personal assistant: Google Calendar (read and manage), Gmail (read-only), a day planner and checklist | OpenClaw (Docker, Gateway on `127.0.0.1:18939`) | `default` |
| 🔵 **Astra** | Coding assistant: creates and edits websites in an isolated workspace, with a live preview | OpenClaw (separate Docker container, Gateway on `127.0.0.1:18949`, preview on `127.0.0.1:18950`) | `oliver` |
| 🩷 **Jester** | Entertainment: jokes, banter, friendly roasts, short funny stories | Voice-only (a Higgs session with no tools and no OpenClaw) | `jake` |

Use each agent on its own tab, or all three together in **Team** mode.

## Features

- **Intelligent routing.** In Team mode a silent Higgs Realtime session hears every
  turn and picks the agent with a real `call_agent(agent, reason)` tool call. Follow-ups
  stay with the current agent; saying a name ("Hey Jester, ...") always takes priority.
  A name-only router is available as a fallback.
- **Distinct real-time voices.** Every agent has its own Higgs voice session, voice,
  personality and instructions.
- **Natural handoffs and interruptions.** Talk over an agent to interrupt it. Turning to
  another agent hands over the floor at once; "stop" silences speech without
  cancelling any work. Only one agent speaks at a time.
- **Background tasks that keep running.** Requests that take real work become
  OpenClaw jobs. You keep talking; the result is independently read back before it is
  reported as done, then announced when the conversation pauses.
- **Fast answers from a hot-context cache.** Each OpenClaw agent keeps cached
  snapshots of its common data (today's schedule, the active project) for instant
  answers, and falls back to a live OpenClaw lookup for anything outside them.
- **Live website preview.** While Astra builds, a preview pane opens beside the
  conversation and shows the site as soon as it is served.
- **Configurable agents.** Agents are YAML files: identity, voice, role, backend,
  cache topics and verifiers. OpenClaw-backed and voice-only agents share the same
  room and speaking floor.
- **Per-agent isolation.** Each agent receives only what is routed to it plus its own
  tool results; credentials stay on the local server and never reach the browser.

## How it works

```text
Browser UI (apps/web) ── WebRTC (Pipecat) ──▶ Chorus server (services/voice, 127.0.0.1:7860)

Team mode
  microphone ──▶ silent Higgs overseer: turn detection, transcription, call_agent(agent, reason)
                       │ your words, as text, to the selected agent only
                       ▼
                 one Higgs voice session per agent (own voice, instructions, tools)
                   ├─ Tari   ──▶ agent runtime ──▶ OpenClaw Gateway :18939  (Calendar, Gmail, planner)
                   ├─ Astra  ──▶ agent runtime ──▶ OpenClaw Gateway :18949  (coding workspace, preview :18950)
                   └─ Jester (voice-only: no tools, no OpenClaw)
                       │ audio
                       ▼
                 speaking floor (one voice at a time) ──▶ speaker
```

- **Single-agent tabs** connect your microphone to one agent's Higgs session through
  Pipecat's Boson realtime service.
- **Team mode** uses one browser connection. The overseer only routes: it never speaks
  and has no agent tools. Agents never receive your raw audio or each other's data.
- **Agent runtimes** live for the whole server process: a hot-context cache, a
  background job manager and result verifiers for each OpenClaw agent, so work
  survives a browser disconnect.
- **OpenClaw** runs each OpenClaw-backed agent in its own isolated Docker container with
  its own model sign-in, credentials and workspace. Chorus talks to it through the
  local Gateway.

Details: [architecture](docs/architecture.md) · [Team mode](docs/team-voice.md) ·
[Higgs overseer](docs/higgs-overseer.md) · [voice runtime](docs/voice.md).

## Prerequisites

- **macOS with Docker Desktop** (Compose v2). This is the tested setup; the OpenClaw
  manager also documents Linux (Docker usable without root) and Windows via WSL.
- **Python 3.11** and **Git**.
- **A Boson API key** with access to `higgs-realtime-1.5-preview`.
- **A ChatGPT account** entitled to the OpenClaw agents' model (`openai/gpt-6.1-sol` by
  default). Tari and Astra each sign in once with a device code.
- **For Tari's Gmail and Calendar:** a Google account used only for this demo, and a
  Google Cloud OAuth client (Desktop app) with the Gmail and Calendar APIs enabled.
  Tari's calendar and email features need it; the rest of the demo does not.
- A browser with microphone access, and **headphones** (open speakers can echo
  agent speech back into the microphone).
- Optional: **Node.js 22** to run the frontend tests and browser probe.

## Quickstart

These steps set up the full three-agent demo, including the real OpenClaw backends for
Tari and Astra. Run commands from the repository root unless a step says otherwise.

### 1. Clone the repository

```bash
git clone https://github.com/SpaceJesusTM/openclaw-voice.git
cd openclaw-voice
```

### 2. Add your Boson key

```bash
cp services/voice/.env.example services/voice/.env
```

Edit `services/voice/.env` and set `BOSON_API_KEY=`. The file is ignored by Git; never
commit it. Chorus reads its settings from this file when the server starts.

### 3. Set up Tari's OpenClaw backend

```bash
python3 infra/openclaw/manage.py init --timezone America/Toronto
python3 infra/openclaw/manage.py start
python3 infra/openclaw/manage.py openclaw models auth login --provider openai --device-code --agent tari
python3 infra/openclaw/manage.py doctor
```

- `init` creates private state (Gateway token, OpenClaw config, Tari's workspace) under
  `infra/openclaw/.local/`, which Git ignores. Re-running it never overwrites existing
  files.
- `start` builds the pinned OpenClaw image on first run and waits for the container to
  become healthy.
- Run the sign-in in your own terminal and follow its instructions. Never paste device
  codes, callback URLs or tokens anywhere else.
- For Gmail and Calendar, follow
  [OpenClaw setup, step 3](docs/openclaw-setup.md#3-register-and-authorize-a-different-google-account):
  it covers the Google Cloud client, setting `TARI_GOOGLE_ACCOUNT` and authorizing the
  read-only (`tari-read`) and Calendar (`tari-calendar-write`) profiles.

The demo agents are configured for `America/Toronto`. To use another timezone, pass
your IANA timezone to both `init` commands and change `timezone:` in
`services/voice/agents/*.yaml` to match.

### 4. Set up Astra's OpenClaw backend

```bash
python3 infra/openclaw/manage.py --agent astra init --timezone America/Toronto
python3 infra/openclaw/manage.py --agent astra start
python3 infra/openclaw/manage.py --agent astra openclaw models auth login --provider openai --device-code --agent astra
python3 infra/openclaw/manage.py --agent astra doctor
```

Astra gets its own container, private state in `infra/openclaw/.local-astra/` and a
coding workspace in `astra-workspace/` (both ignored by Git). It has no access to
Tari's data or credentials.

### 5. Install the voice service

```bash
cd services/voice
make setup
```

This creates a locked Python 3.11 environment with uv 0.13.5 (first time only).

### 6. Start Chorus

```bash
make dev
```

Chorus serves **http://127.0.0.1:7860** (loopback only). Team mode uses the Higgs
overseer by default (`CLAWVOICE_TEAM_ROUTER_MODE=overseer` in `services/voice/.env`).
Leave this terminal running.

### 7. Open Chorus and connect

1. Open **http://127.0.0.1:7860/?agent=team**. The tabs should read **Team**, **Tari**,
   **Astra** and **Jester**. The settings menu (top right) shows each agent's
   connection: Tari and Astra over HTTP (or WebSocket, if paired), Jester as
   "voice only".
2. Put on headphones, click **Connect** and allow the microphone.
3. Talk. Try "What's on my calendar tomorrow?", then "Tell me a joke."

You can also check the server from a terminal:

```bash
curl -s http://127.0.0.1:7860/api/agents
```

It lists `tari`, `astra` and `jester`, with `"team": {"available": true, ...}` and
`"router_mode": "overseer"`.

### Stop and restart

Stop the voice server with **Ctrl+C**. Background jobs are stored and recovered on the
next start where possible. Then, from the repository root, stop the OpenClaw
containers (their state is kept):

```bash
python3 infra/openclaw/manage.py stop
python3 infra/openclaw/manage.py --agent astra stop
```

Start again later, from the repository root:

```bash
python3 infra/openclaw/manage.py start
python3 infra/openclaw/manage.py --agent astra start
cd services/voice && make dev
```

After pulling new changes, refresh the agents' workspace templates (safe to repeat;
private data and projects are preserved):

```bash
python3 infra/openclaw/manage.py update-workspace
python3 infra/openclaw/manage.py --agent astra update-workspace
```

### Just want to hear Jester?

Jester needs only a Boson key. After steps 1, 2 and 5, set `CLAWVOICE_AGENTS=jester`
in `services/voice/.env`, run `make dev` and open http://127.0.0.1:7860. Team mode
needs at least two agents, so clear that value again for the full demo.

## Using Chorus

**Tabs.** **Tari**, **Astra** and **Jester** are one-on-one conversations. **Team** puts
all three on one connection. Switching tabs ends the current voice session; agents'
background jobs and results are kept.

**Connecting.** Click **Connect** once. In Team mode Tari greets you and mentions the
others. Results that finished while you were away are announced at the next pause.
Use the keyboard button to type instead of speaking, and enable **Push to talk** in
settings for noisy rooms (hold the button or Space).

**Who answers in Team mode.** Just talk. The overseer keeps the current agent for
follow-ups, thanks and small talk, and switches when you clearly change topic or say a
name. A name always wins: "Hey Jester, ...", "Astra, ...". The seats under the orb show
who is speaking, waiting to speak, thinking or working, and the transcript labels
every reply.

**Interrupting.** Start talking to cut the current speaker off. Turning to another
agent mid-sentence hands over the floor; the first agent does not resume, and its work
continues. "Stop" ends speech without cancelling anything; to cancel work, ask the
owning agent ("Astra, cancel the website task") or use **Cancel** in Background tasks.

**Background work.** **Now** shows lookups and handoffs as they happen, and
**Background tasks** lists each agent's jobs. A job is reported as done only after
Chorus independently reads the result back; the announcement waits for a natural pause
and never talks over another agent.

**Astra's preview.** When Astra builds or edits a website, a preview pane slides in
and shows **http://127.0.0.1:18950** once the site is served. You can also open that
address directly.

### Things to try in Team mode

| Say | What happens |
| --- | --- |
| "What's on my calendar tomorrow?" | Tari answers from its cached calendar snapshot, or looks it up live |
| "What's the most recent email in my Primary inbox?" | Tari does a read-only Gmail lookup |
| "Astra, create a simple personal portfolio website." | Astra starts a real coding task; the preview opens when the site is ready |
| "Can you tell me the status of my website?" | Routed to Astra |
| "Tell me a really funny joke." | Routed to Jester |
| "Another one." | Stays with Jester |
| "Jester, roast that website." | Jester, by name |
| "Stop." | Speech stops; every task keeps running |

More scripts and expected results: [Getting started](docs/GETTING_STARTED.md).

## Configuring your own agents

Agents live in `services/voice/agents/` as `<id>.yaml` plus a Markdown role file, and
`agents/team.yaml` lists which ones run (Tari is the default). The core runtime never
names an agent; everything agent-specific is configuration.

- **OpenClaw-backed agents** (like Tari and Astra) declare a Gateway and token
  reference, cache topics, helpers and result verifiers. See
  [Agent configuration](docs/voice.md#agent-configuration) and
  [Add another OpenClaw agent](docs/voice.md#add-another-openclaw-agent).
- **Voice-only agents** (like Jester) set `backend: voice` and need only identity,
  personality and a voice. See
  [Voice-only agents](docs/team-voice.md#voice-only-agents-jester).
- **Settings** go in `services/voice/.env`; it is loaded when the server starts and
  overrides shell variables of the same name, so restart after editing it. Useful
  ones: `CLAWVOICE_TEAM_ROUTER_MODE` (`overseer` or `names`), `CLAWVOICE_AGENTS`,
  `CLAWVOICE_DEFAULT_AGENT` and `CLAWVOICE_VOICE_OVERRIDE`. All settings:
  [environment variables](docs/voice.md#environment-variables-values-never-printed-or-sent-to-the-browser).

## Development and tests

Offline checks (no Boson, OpenClaw or Google calls):

```bash
cd services/voice && make check && cd ../..        # pytest, Ruff, compile
node --test apps/web/tests/*.test.cjs               # browser UI (Node 22)
python3 -m unittest discover -s infra/openclaw/tests
```

Live probes that use the real services are described in
[Getting started](docs/GETTING_STARTED.md#test).

| Path | Contents |
| --- | --- |
| `apps/web/` | Browser UI, no build step |
| `services/voice/` | Chorus server: Pipecat + Higgs voice, Team room and overseer, agent runtimes (`clawvoice` package), agent configs, tests, live probes |
| `infra/openclaw/` | Isolated OpenClaw installations for Tari and Astra: `manage.py`, Compose files, workspace templates |
| `docs/` | Guides and design notes |
| `experiments/` | Earlier Higgs and Pipecat validation, kept for reference |
| `references/` | The original hackathon brief |

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| "Chorus server is not reachable" | `make dev` is running in `services/voice` and port 7860 is free |
| Server stops at startup with `BOSON_API_KEY missing` | Set the key in `services/voice/.env` |
| An agent's connection shows "unavailable", or a banner says it isn't connected to its OpenClaw gateway | `python3 infra/openclaw/manage.py [--agent astra] status`, then `doctor` and `logs`; run that agent's `init` and `start` if you skipped them |
| An OpenClaw agent's tasks fail to run | Its model sign-in: `python3 infra/openclaw/manage.py openclaw models status --agent tari --json --check`, or for Astra `python3 infra/openclaw/manage.py --agent astra openclaw models status --agent astra --json --check` |
| Tari can't read the calendar or email | Google setup, step 3 of [OpenClaw setup](docs/openclaw-setup.md#3-register-and-authorize-a-different-google-account); Google grants for apps in Testing generally expire after about seven days |
| Agents hear themselves or interrupt each other | Use headphones, or enable **Push to talk** |
| Connection shows "HTTP (pairing pending)" | Normal; WebSocket pairing is optional ([how to pair](docs/GETTING_STARTED.md#optional-websocket-transport)) |
| A port is already in use | Leave the other service alone. Gateway and preview ports are set by `init` (`--port`, `--preview-port`) and must match the agent YAML; the web port can be changed with `make dev PORT=7861` |

More: [voice runtime troubleshooting](docs/voice.md#troubleshooting) and
[OpenClaw diagnostics](docs/openclaw-setup.md#5-diagnostics-and-stopping).

## Status and roadmap

**Implemented** (on `main`):

- One-on-one voice with Tari, Astra and Jester
- Team mode: one connection, Higgs overseer routing with `call_agent`, explicit-name
  priority, a name-only fallback router, one shared speaking floor and handoffs
- Background OpenClaw jobs with independent read-back and spoken completion notices
- Per-agent hot-context caches with live OpenClaw fallback
- Astra's isolated coding workspace and live preview
- Voice-only agents (`backend: voice`)

**Not implemented yet:**

- Agents collaborating with each other (consulting one another, sharing results or
  splitting a task). Today each agent works only on what is routed to it.
- Several people in one room, per-person permissions, or teams of independently owned
  agents.
- Hosted deployment. Chorus runs locally and binds to loopback.

**Known limitations:**

- Routing by intent is probabilistic; say the agent's name when it matters.
- Routing adds a short delay before the agent starts answering.
- OpenClaw agents use the Gateway's HTTP API unless you approve WebSocket pairing,
  which adds run IDs, real abort and recovery after a restart.
- Each OpenClaw agent runs one job at a time; a long job delays that agent's next one.
  Cached answers are unaffected.

The full plan is in the [roadmap](docs/ROADMAP_ACCEPTANCE_DEMO.md).

## Security and privacy

- The server binds to `127.0.0.1`. The Boson key and Gateway tokens stay on the server
  and are never sent to the browser.
- Each OpenClaw agent runs in an isolated container with its own credentials and
  state under ignored `infra/openclaw/.local*/` directories. Use dedicated demo
  accounts, never a production assistant's data.
- Tari's Gmail access is read-only. Calendar changes use a separately authorized
  profile and are read back before they are reported as done.

## Documentation

| Guide | Contents |
| --- | --- |
| [Getting started](docs/GETTING_STARTED.md) | Run, try and test every mode |
| [OpenClaw setup](docs/openclaw-setup.md) | Model sign-in, Google authorization, diagnostics |
| [`infra/openclaw`](infra/openclaw/README.md) | The OpenClaw manager and Astra's isolated agent |
| [Team mode](docs/team-voice.md) | The shared room, speaking floor, jobs, voice-only agents |
| [Higgs overseer](docs/higgs-overseer.md) | How routing decisions are made and bounded |
| [Voice runtime](docs/voice.md) | Configuration, cache, jobs, interruptions, troubleshooting |
| [Web UI](apps/web/README.md) and [design language](docs/design-language.md) | The browser interface |
| [Architecture](docs/architecture.md) and [contracts](docs/ARCHITECTURE_AND_CONTRACTS.md) | Components and design intent |
| [Product vision](docs/PRODUCT_VISION.md) and [roadmap](docs/ROADMAP_ACCEPTANCE_DEMO.md) | Where Chorus is going |

Validation records from development are kept in `docs/` (for example
[voice-validation.md](docs/voice-validation.md)).

## About the name

Chorus was developed under the working name **ClawVoice**. Internal identifiers keep
that name for compatibility: the `clawvoice` Python package, `clawvoice.*` API events,
`CLAWVOICE_*` environment variables, file paths and some log messages.

## Contributors

- **SpaceJesusTM**: Individual AI agent implementations and integrations.
- **[Jefferson-Liu](https://github.com/Jefferson-Liu)**: Designed and implemented multi-agent overseer architecture.

## License

[MIT](LICENSE)
