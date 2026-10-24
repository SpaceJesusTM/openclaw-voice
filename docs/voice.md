# ClawVoice voice runtime (Priority 1)

Talk to a real OpenClaw agent with **Boson Higgs Realtime 1.5 Preview**
(`higgs-realtime-1.5-preview`) over **Pipecat**, in your browser, on your Mac.
Tari is the first configured agent; nothing in the runtime is specific to Tari.

Three behaviors define Priority 1:

1. **Fast cached context.** "What's on my planner today?" is answered from a
   per-agent hot-context cache of real planner/Calendar data (sub-millisecond
   reads) without waiting for an OpenClaw model run.
2. **Real agent actions.** "Create a Calendar event tomorrow from 3 to 4 PM called
   Project Meeting" is handed to the OpenClaw agent, which uses its own Calendar
   skill. ClawVoice then reads the event back independently and only says it is
   done when the read-back matches.
3. **Nonblocking conversation.** You keep talking while the agent works. The
   result is announced when nobody is speaking. Interrupting speech never
   cancels the work.

Evidence and measured numbers: [voice-validation.md](voice-validation.md).

## Start it

Prerequisites: the isolated OpenClaw demo from [openclaw-setup.md](openclaw-setup.md)
running (`python3 infra/openclaw/manage.py start`), Docker Desktop, Python 3.11,
and the Boson key already used by the experiments.

```sh
python3 infra/openclaw/manage.py start      # if the demo Gateway is not running
python3 infra/openclaw/manage.py doctor     # optional health check

cd services/voice
make setup                                   # first time: uv 0.13.5 + locked environment
make dev                                     # http://127.0.0.1:7860
```

Open **http://127.0.0.1:7860**, click **Connect**, allow the microphone, and
speak (headphones avoid echo). Tick "Connect without microphone" for typed-only
use. The stock Pipecat playground is still available at `/client/`.

Stop with Ctrl+C. Background jobs that are still running when the server stops
are recovered on the next start where the transport allows it (see below).

### Environment variables (values never printed or sent to the browser)

| Name | Where | Purpose |
| --- | --- | --- |
| `BOSON_API_KEY` | process env, `services/voice/.env`, or the existing `experiments/higgs-realtime-1.5/.env` (reused automatically) | Boson realtime key |
| `OPENCLAW_GATEWAY_TOKEN` | `infra/openclaw/.local/runtime.env` (created by `manage.py init`) | Gateway credential; referenced from `agents/tari.yaml` as `envfile:infra/openclaw/.local/runtime.env#OPENCLAW_GATEWAY_TOKEN` |
| `BOSON_REALTIME_MODEL`, `BOSON_REALTIME_URL` | optional | Must stay the preview model/endpoint; anything else fails at startup |
| `BOSON_VAD_THRESHOLD`, `BOSON_VAD_SILENCE_MS`, `BOSON_NOISE_REDUCTION` | optional | Boson turn-detection tuning |
| `CLAWVOICE_AGENTS`, `CLAWVOICE_DEFAULT_AGENT` | optional | Which configured agents to serve |
| `CLAWVOICE_VOICE_OVERRIDE` | optional | Override every agent's Boson voice |
| `CLAWVOICE_IDLE_TIMEOUT_SECS` | optional, default 1800 | Silence before a voice session closes (jobs continue) |
| `CLAWVOICE_PERSIST_TRACES` | optional, default true | Sanitized traces in ignored `services/voice/captures/` |

See `services/voice/.env.example`. The browser only ever talks to the local
Python server; it never receives the Boson key or the Gateway token.

### Optional: pair the WebSocket transport

ClawVoice reaches the Gateway in one of two supported ways, chosen per agent by
`openclaw.gateway.transport` (`auto` by default):

- **HTTP** (works immediately): streaming `/v1/chat/completions` with the
  server-side bearer token, agent selector `openclaw/<agentId>` and an explicit
  session key. If the HTTP connection drops mid-run, the Gateway cancels the
  run, so ClawVoice marks such a change as *uncertain* and never resubmits it.
- **WebSocket** (preferred once approved): protocol v4 via Pipecat's
  `OpenClawGatewayClient`, with run IDs, Gateway-side idempotency keys, real
  `chat.abort`, and recovery with `agent.wait` after a dropped socket or a
  ClawVoice restart.

The Gateway only grants WebSocket operator scopes to an approved device, and the
Docker-published port means ClawVoice is never a loopback client. On startup
ClawVoice creates a private device key (`services/voice/.local/gateway-device.json`,
mode 0600) and requests pairing with `operator.read` + `operator.write` only.
To approve it (an owner decision; the token ClawVoice already holds is a full
operator credential, so this grants no broader access):

```sh
python3 infra/openclaw/manage.py openclaw devices list     # find the request from clawvoice-tari
python3 infra/openclaw/manage.py openclaw devices approve <requestId>
curl -X POST http://127.0.0.1:7860/api/agents/tari/gateway/reconnect   # or restart ClawVoice
```

The UI's Gateway badge shows `WebSocket (paired)` afterwards. Pairing requests
expire after five minutes; ClawVoice re-requests on start, on reconnect and
every ten minutes while it runs on HTTP. Revoke with `openclaw devices remove`.

## Primary Inbox, unified schedule and microphone controls

Email questions delegate to OpenClaw using its Gmail read-only helper. The
default is the Gmail Primary tab (`in:inbox -category:promotions -category:social`;
see [voice-gmail-latest.md](voice-gmail-latest.md)), including read and unread,
sorted newest first, without a last-day cutoff for "latest". Explicit unread or broader searches are honored.
The broader helper switch is `gmail search QUERY --all-mail`. Security content
is withheld before it reaches the agent; no codes or recovery links are spoken.

Calendar and timed planner entries now refer to the same Calendar event. New
planner bookings create no local mirror. Existing local blocks and checklist
remain local; no automatic migration runs. See [planner.md](planner.md).

ClawVoice now explicitly sends VAD threshold **0.90** and silence **600 ms**.
The pinned Boson SDK/server default was 0.85 ([SDK changelog](https://github.com/boson-ai/pipecat-boson/blob/main/CHANGELOG.md)).
Tune `BOSON_VAD_THRESHOLD` in `services/voice/.env` (e.g. 0.85 for quieter speech,
0.95 for louder/noisier input), then reconnect; Boson fixes VAD at session start.
The browser requests echo cancellation and noise suppression and disables
automatic gain to avoid amplifying background noise. Browser/device support
varies. Boson `BOSON_NOISE_REDUCTION=near_field|far_field` remains optional.

For noisy environments, enable **Push to talk**, then hold the button or Space
(outside text/button controls) while speaking. Release closes the microphone;
blur/hidden-tab/pointer cancellation also closes it. Audio output and natural
barge-in while held still use Pipecat/Boson. This gates the browser audio track;
it is not a wake word or speaker recognition system. Nearby speech can still
activate open-mic mode, so validate with your actual microphone and headphones.

To update an existing isolated P1 demo safely:

```sh
python3 infra/openclaw/manage.py update-workspace
# Upgrades known P1 templates with backups; refuses customized files for manual merge.
# Never touches planner files, credentials, or production Tari.
python3 infra/openclaw/manage.py start
cd services/voice
make dev
```

## Using it

The page shows the agent (name, role, colored orb), the verified model badge,
the Gateway transport, the conversation (speech transcripts, typed messages,
replies and ClawVoice notices), background tasks with live status,
verification and timings (with a Cancel button), the fast-context topics with
age and freshness, and a diagnostics drawer (tool calls with cache status and
read time, Boson events, latencies). Orb states: idle, listening, speaking,
working (spinning ring) and an update waiting to speak (amber dot).

Things to try:

- "What's on my planner today?" / "What do I have tomorrow?" / "What's on my
  Calendar today?" / "What's my next meeting?" / "What are my priority
  checklist tasks?" — cached answers.
- "Create a Calendar event tomorrow from 3 to 4 PM called Project Meeting." —
  real Calendar write, verified, announced.
- "Schedule Deep work tomorrow from 2 to 3 PM." — one Calendar/planner entry.
- "Mark Review prototype as in progress." — local checklist change.
- While a task runs: "Also, what's on tomorrow's planner?" — answered at once.
- "Did that finish?" — read from tracked job state. "Cancel that task." —
  explicit cancellation (speech interruption never cancels).

## Architecture

```text
Browser (apps/web)  --WebRTC audio + RTVI data-->  Pipecat SmallWebRTC transport
                                                        |
        RealtimeTextBridge -> user aggregator -> VerifiedBosonService (Higgs 1.5 Preview)
                                                        | tools: get_context, delegate_task,
                                                        |        check_tasks, cancel_task
                 FloorObserver / AnnouncementCoordinator (per voice session)
                                                        |
        ------------------------ process-level runtime (outlives voice sessions) ----------
        AgentRuntime per configured agent:
          HotContextCache  <- resolvers <- HelperRunner -> trusted read helpers (no model run)
          JobManager -> OpenClawAgent adapter -> Gateway (WebSocket or HTTP) -> agent skills
                     -> verifiers -> HelperRunner (independent read-back)
                     -> cache invalidation -> announcement to the voice session
```

| Path | Responsibility |
| --- | --- |
| `services/voice/clawvoice/bot.py` | Per-connection Pipecat pipeline (unchanged shape from the validated experiment) |
| `clawvoice/voice/service.py`, `text_bridge.py` | Preview-model verification and the two preserved compatibility fixes |
| `clawvoice/voice/tools.py`, `instructions.py` | Higgs tools and instructions generated from agent config |
| `clawvoice/voice/floor.py`, `session.py` | Speaking floor, notice delivery, client liveness |
| `clawvoice/openclaw/` | Device identity, WebSocket client (Pipecat subclass), HTTP transport, generic adapter, request envelope, receipt parser |
| `clawvoice/context/` | Generic hot-context cache and built-in resolvers |
| `clawvoice/jobs/` | Job model, persistent store, verifier contract, job manager |
| `clawvoice/plugins/day_planner.py` | Resolvers/verifiers over the planner and Calendar helper JSON |
| `clawvoice/runtime.py`, `server.py` | Process wiring, local API, lifespan |
| `services/voice/agents/*.yaml` | Agent configurations (Tari live, Astra fixture) |
| `apps/web/` | Local UI (no build step, no external scripts) |
| `clawvoice/testing/` | Fake Gateway and fixture helpers (tests and portability demo only) |

Why OpenClaw jobs are not Pipecat worker-bus jobs: a bus job belongs to the
voice worker that requested it, so it would end when the browser disconnects.
Jobs must outlive voice sessions, survive restarts where possible and be
verified, so they live in a process-level job manager. Pipecat still provides
the transport, RTVI, the realtime service, function calling, frame observers and
the OpenClaw Gateway protocol client.

## Agent configuration

Agents are YAML files in `services/voice/agents/`, listed in `agents/team.yaml`.
Everything agent-specific lives there; a test asserts the core code never names
an agent. Abbreviated `tari.yaml`:

```yaml
id: tari
display_name: Tari
color: "#22c55e"
role_file: tari.md                 # Markdown role/personality for the voice model
owner_scope: local-owner           # cache, jobs and session namespace
timezone: America/Toronto
voice: {boson_voice: default}
openclaw:
  agent_id: tari                   # OpenClaw agent (selector openclaw/tari)
  gateway:
    ws_url: ws://127.0.0.1:18939    # loopback only unless allow_remote: true
    http_url: http://127.0.0.1:18939
    token_ref: envfile:infra/openclaw/.local/runtime.env#OPENCLAW_GATEWAY_TOKEN
    transport: auto                # auto | ws | http
  session: {key_template: "agent:{agent_id}:clawvoice-{owner_scope}"}
  timeouts: {question_seconds: 180, change_seconds: 300}
plugins: [day_planner]
helpers:                           # trusted deterministic read helpers (argv, no shell)
  planner: ["{python}", "{repo}/infra/openclaw/manage.py", "planner"]
  google: ["{python}", "{repo}/infra/openclaw/manage.py", "google"]
hot_context: {defaults: {...}, topics: [...]}
delegation:
  targets: [calendar, planner, checklist, gmail, other]
  clarify: "the date, times and title for the unified schedule, or the checklist task"
  receipt_types:
    calendar_event:
      verifier: calendar_event
      invalidates: [calendar.today, calendar.next, planner.today, planner.tomorrow]
      format: '{"type":"calendar_event","action":"created|updated|deleted",...}'
```

Secrets are references (`env:NAME` or `envfile:PATH#NAME` inside the repo),
resolved only in the server. All job/session/cache state is namespaced by agent
ID and owner scope; each agent has its own Gateway adapter, helper runner, cache
and job store file.

### Add another OpenClaw agent

1. Provision the agent in its own OpenClaw installation (or as another agent on
   a Gateway you own) with its own credentials. ClawVoice never shares one
   agent's OAuth or mailbox with another.
2. Copy `agents/astra.yaml` (the fixture example) to `agents/<id>.yaml`: set
   identity, `openclaw.agent_id`, the Gateway URLs and `token_ref`, `helpers`,
   `hot_context.topics`, `delegation.targets` and `receipt_types`, and write
   `agents/<id>.md`.
3. Add it to `agents/team.yaml` (or run with `CLAWVOICE_AGENTS=<id>`), restart,
   and open `http://127.0.0.1:7860/?agent=<id>`.

`agents/astra.yaml` is a complete second agent (coding assistant) with
different topics (`project.active`, `tests.last`), targets, receipt type
(`test_run`) and the generic `helper_lookup` verifier. Try it with fixtures:

```sh
cd services/voice
make fixture-gateway        # terminal 1: fake Gateway on 127.0.0.1:18999 (fixtures only)
PORT=7861 make fixture-dev  # terminal 2: ClawVoice serving Astra
```

Priority 1 serves one agent per voice session; multi-agent rooms, name routing
and collaboration are Priority 2 and are not implemented.

## Hot context

Each topic is a named, read-only view refreshed by a **resolver** from a trusted
source. The cache is a materialized view, never a source of truth:

| State | Meaning | Spoken behavior |
| --- | --- | --- |
| fresh | age ≤ `ttl_seconds`, same local day, not invalidated | answered at once |
| stale | age ≤ `max_staleness_seconds` | answered with its age; refreshed in background |
| expired | refresh failed, older than max staleness | last-known data, clearly labelled possibly outdated |
| refreshing | invalidated by a write, or a new local day | data withheld until re-read (within `read_wait_seconds`) |
| unavailable | never loaded / source down | "can't get this right now" — never treated as empty |

Refresh: warm-up at start; while a voice session is active about every 0.8 ×
TTL (jittered); `idle_refresh_seconds` otherwise; exponential backoff on errors;
one refresh per topic at a time; identical helper calls share one process
(single-flight) with a short memo, and helper concurrency is bounded. Day-scoped
topics roll over at local midnight in the agent's IANA zone (23/25-hour DST
days included). Incomplete sources (for example an unreadable Calendar) are
kept with `complete: false` and warnings.

**Coverage and live fallback.** The cache is an accelerator, not a capability
boundary or a source of truth. Each topic declares `coverage` (for example
"Google Calendar events today only"), and a resolver can narrow it to the exact
window it read ("timed Google Calendar events from now through Monday, October
26 only"). Every `get_context` result carries `covers` and a note: answer from
the snapshot only inside that scope. For anything outside it (another date or
period, more detail, or when the owner asks for a live check), Higgs calls
`delegate_task` with kind `question`. The OpenClaw agent then looks it up
read-only with its own tools and says which range it checked, and the result
arrives as a normal background notice. An unavailable, incomplete or narrow
snapshot is never treated as proof that nothing exists. A lookup that fails says
so ("nothing can be concluded"), never "no results". Broad questions without a
period get a clarifying question or a clearly bounded search.

Writes: after any change job ends (verified or not), the topics listed by the
receipt types are invalidated and re-read from the source; an invalidation that
arrives while a refresh is running forces another read, so answers after a write
reflect it (read-your-writes). Cached data never authorizes an action.

Tari's topics come from `day-planner show DAY --json`, `checklist --json`
and the planner's own Calendar merge (its inclusion and completeness rules stay
authoritative); `jobs.recent` comes from the job store. Gmail is never cached.

### Add a hot-context topic

If a helper prints JSON, configuration is enough:

```yaml
hot_context:
  topics:
    - key: project.active
      description: "the active project's status"     # also shown to the voice model
      coverage: "the active project's current status only"   # beyond this, Higgs delegates a live lookup
      resolver: command_json
      args: {helper: project, argv: [status, --json], summary_field: summary}
      ttl_seconds: 180
      max_staleness_seconds: 900
```

For richer shaping, write a resolver `async def my_topic(ctx, spec) -> Resolved`
(see `clawvoice/plugins/day_planner.py`), register it in a plugin's
`register(registry)`, list the plugin under `plugins:`, and reference it by name.
Return a compact `value`, a deterministic spoken `summary`, `complete`,
`warnings`, and `day` for day-scoped data. Refresh a topic on demand with
`POST /api/agents/<id>/cache/<topic>/refresh`.

## Background jobs

```text
accepted -> queued -> running -> verifying -> completed | failed | cancelled | uncertain
```

- `delegate_task` records the job and returns **accepted** in milliseconds;
  Higgs acknowledges briefly ("On it."). Accepted never means done.
- One lane per agent session runs jobs in order (an OpenClaw session handles one
  turn at a time); later jobs show as **queued**.
- The request envelope frames the turn as the owner's live voice request, marks
  it CHANGE or QUESTION, includes the voice model's resolved details and the
  raw speech transcript, asks for clarification instead of guesses, and asks the
  agent to end with a `CLAWVOICE_RECEIPT {...}` line listing what it changed.
- **completed / verified**: every change in the receipt was read back
  independently (Calendar `get`, planner `show`, checklist `--all`) and matches
  the requested title/date/times (Toronto local time). **completed / mismatch**:
  the read-back differs; the actual state is announced. No receipt, an
  unverifiable receipt, a timeout, a lost connection or a run that errored
  during a change: **uncertain**, with "check before asking again". Questions
  complete with the agent's answer; "no change" is said explicitly.
- Duplicate tool calls (same call ID, or the same request while it is pending
  or just finished) return the existing job. Dispatch is retried only when the
  Gateway provably never received it, or over WebSocket with the same
  idempotency key; a possibly-started HTTP run is never resubmitted.
- Records persist in `services/voice/.local/jobs/jobs-<agent>.json` (0600,
  bounded to 200 jobs / 7 days). On restart, unfinished WebSocket runs are
  recovered with `agent.wait`; anything else unfinished is marked uncertain or
  "not started".
- Recent results stay queryable ("Did that finish?" → `check_tasks`), and
  finished results that nobody heard are announced on the next connection.

## Interruption and cancellation

- **Speech interruption** (barge-in) stops playback; Pipecat/Boson truncate the
  assistant's item. Tools are registered with `cancel_on_interruption=False`
  and jobs run in the process-level lane, so nothing is cancelled.
- **Explicit cancellation** ("cancel that task", the UI's Cancel button, or
  `POST /api/jobs/<id>/cancel`): a queued job is dropped before dispatch; a
  running job is aborted (`chat.abort` on WebSocket, closing the stream on
  HTTP). A cancelled change is flagged "may already have been made".
- **Disconnects**: a browser disconnect ends only the voice session; jobs
  continue and their results are kept. A WebSocket drop to the Gateway is
  reported as *disconnected* (not failed or cancelled) and recovered.

## Announcements and the speaking floor

A per-session coordinator watches real pipeline frames: user speech (server
VAD), bot speech (transport playback), active responses and tool calls. A
finished job's result is injected as a clearly marked `[ClawVoice notice]` only
after the floor has been free for a short quiet gap, and only while the client
is demonstrably connected (inbound audio or the UI keepalive). It never speaks
over the user or itself, coalesces several results, and counts as delivered at
most once (re-queued once if interrupted before any audio played). Results go to
the session that asked, otherwise to the latest session of that agent, otherwise
they wait for the next connection. Priority 2 can extend the same state machine
with a speaking-agent field and a cross-agent floor queue.

## Security and privacy

- Server binds `127.0.0.1`; state-changing API calls from other web origins are
  refused. No key, token, device key, endpoint or helper command is sent to the
  browser (`/api/agents` and `/api/state` are tested for this).
- The Gateway token stays in `infra/openclaw/.local/runtime.env`; the Boson key
  in its existing ignored `.env`. Both are redacted from logs and traces.
- Helpers run without a shell, with a minimal environment that excludes all
  credentials, and with output, time and concurrency limits. Receipt IDs passed
  to helpers must be plain identifiers.
- Calendar titles, planner text and agent output are treated as data; the voice
  instructions say so, and tool results are bounded and whitespace-normalized.
- Gmail stays read-only and is never cached or spoken by ClawVoice.
- Private traces, probe reports, job records and the device key live only in
  ignored `services/voice/captures/` and `services/voice/.local/`.

## Live demo walkthrough

1. `make dev`, open the page; the model badge turns to
   `higgs-realtime-1.5-preview · verified` after Connect.
2. "Hey Tari, what's on my planner today?" — the diagnostics drawer shows
   `get_context … cache hit`, read time in microseconds.
3. "Create a Calendar event tomorrow from 3 to 4 PM called Demo Sync." — Tari
   says "On it."; a task appears as running.
4. While it runs: "Also, what's on tomorrow's planner?" — answered immediately.
5. A few seconds later Tari announces the verified event; the task shows
   `completed · ✓ verified`, and the planner/Calendar topics refresh.
6. "Move Demo Sync to 4 PM." then "Did that finish?"; finally "Delete the Demo
   Sync event." (only events you created for the demo).
7. Barge in while Tari is speaking; the task list shows nothing was cancelled.

## Tests

```sh
cd services/voice
make check                    # offline tests + Ruff + compile; no API calls
make dev                      # then, in another terminal:
.venv/bin/python probes/voice_probe.py --live --scenario all    # live a-e, g, h (Boson, OpenClaw, Google)
node probes/ui_probe.mjs --live                                 # browser path with a virtual microphone
```

The live probes create only uniquely named `ClawVoice …` items (tomorrow/today,
no attendees) and delete them; if a probe fails midway, its cleanup removes the
item with the demo's own helper and reports whether it is gone. Scenario `f`
(fixture agent) needs the two fixture processes above and `--fixture-url`.
`--help` lists every scenario.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| "ClawVoice server is not reachable" | `make dev` running; port 7860 free |
| Gateway badge "unavailable" | `python3 infra/openclaw/manage.py status` / `doctor` |
| Badge "HTTP (pairing pending)" | Normal; optional pairing above |
| Microphone denied | Browser site settings for 127.0.0.1, or typed-only mode |
| Topic shows `unavailable` or errors | Docker running; `manage.py planner show today --json` works |
| No spoken notice | Notices wait for silence and a connected client; see the task's `announce:` state |
| Model error at connect | Key valid; model must be exactly `higgs-realtime-1.5-preview` |

## Known limitations

See [voice-validation.md](voice-validation.md#limitations-and-untested-areas).
