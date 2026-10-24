# Team mode: several real agents in one voice room (Priority 2a)

Branch `feat/p2a-team-voice` (from `feat/astra-coding-agent`). Tari and Astra share one
browser connection. You address either by name, both can work on separate requests
at the same time, and their replies take turns on one speaking floor. The Tari-only
and Astra-only modes are unchanged.

Run it: [GETTING_STARTED.md](GETTING_STARTED.md#team-mode-tari-and-astra-together).

## How it works

```
browser mic ──WebRTC──► TeamRoom ──WebRTC──► browser speaker      (Pipecat transport + RTVI, as before)
                           │
                           ├─ listener: one silent Higgs session (text output, server VAD with
                           │  create_response off, transcription). Finds turns and barge-in,
                           │  transcribes. Never generates, no tools, never sees agent output.
                           ├─ router: the transcript's leading name ("Hey Tari, ...") or vocative
                           │  ("..., Astra?") picks the agent. No name, no request.
                           ├─ one voice per agent: its own Higgs session (own voice, instructions,
                           │  tools), its own AgentRuntime (cache, OpenClaw jobs, verifiers).
                           │  Each runs its requests in order; agents run at the same time.
                           └─ speaking floor: every agent's replies queue for one audible slot,
                              paced at real time by the server, so the room knows what was heard.
```

Code: `services/voice/clawvoice/team/` (`room.py`, `voice.py`, `floor.py`,
`addressing.py`, `realtime.py`, `bot.py`). Single-agent mode is still
`clawvoice/bot.py` with one Pipecat `BosonRealtimeLLMService`.

Why the Team room drives Higgs sessions directly: Pipecat's realtime service models
one session per pipeline, while Team mode needs a listener plus one session per
agent behind one connection, with playback held for a shared floor. Pipecat still
provides WebRTC, RTVI, the output transport and the runner. This follows the
agent-selector experiment (`agent-selector-demo`: silent overseer, server-paced
playback, truncation to the heard audio, retry on `upstream_error`), adapted from
"one active agent" to many independent pending replies, and without its audio
fan-out into every agent or its fake tasks.

## Addressing

- Addressed: the name first, after greetings/fillers ("Tari, ...", "Hey Astra ...",
  "Okay Tari ...", "So um hey Astra ..."), or a trailing vocative ("..., Tari?").
  Case-insensitive. Likely mistranscriptions are aliases in each agent's YAML
  (`addressing.aliases`, e.g. `atari` for "Hey Tari", `astro` for Astra).
- A name in the middle ("ask Astra about it") is a mention, not an address.
- **Unnamed requests go to nobody** (the UI marks them "not addressed"). There is no
  topic-based routing. Two narrow continuations are allowed, both following an
  explicit name: a bare "Hey Astra." makes the next utterance (within 12 s) Astra's,
  and a fragment that starts within 1.5 s after a named utterance continues it
  (a pause mid-sentence split the turn).
- Two separate addressed sentences for different agents ("Tari, what's on tomorrow?
  Astra, build me a page.") become two requests.
- Each agent receives only what was addressed to it, as text, plus its own tool
  results and notices. Nothing from one agent's session, tools, Google data or
  workspace reaches the other. The browser shows one combined transcript; that is
  presentation only.

## The speaking floor

- One reply is audible at a time. Ready replies wait in one room queue, first ready
  first played (ties: creation order), and the next starts automatically.
- User speech comes first: nothing starts while you speak or while your finished
  turn is being transcribed (it might be "stop"). Talking over an agent cuts it
  (barge-in); its memory is truncated to what you actually heard. Queued replies
  keep their place.
- A newer request to the same agent supersedes its replies not yet heard. An answer
  still unplayed after 60 s is dropped rather than played late.
- **Addressing an agent hands it the floor** (it becomes the foreground agent):
  - Another agent that was already answering (you heard it start) stops for good:
    its speech is cut, its unheard replies are dropped, and the rest of its
    response chain runs silently. Tool calls it made finish, its OpenClaw jobs keep
    running, and its memory is truncated to what you heard. It does not resume.
  - Another agent that had not started answering keeps its answer queued, but the
    newly addressed agent speaks first: older speech waits up to 8 s for its reply.
  - With several names in one utterance, the first one named takes the floor.
- A reply whose tool call takes long yields the floor after 6 s and rejoins the queue.
- Higgs 1.5 occasionally fails a response (`upstream_error`); it is retried once.

## Background jobs and announcements

Unchanged per agent: `delegate_task` starts a real OpenClaw job in that agent's
runtime; "done" still needs the receipt plus independent read-back. When a job
finishes, its owner gets a notice; the UI shows the result at once, and the notice
is spoken in the owner's voice at a natural break, never over another agent and
with no new mention of the name. A natural break: no one speaking, no answer still
on its way, and 1.2 s of quiet, or 3.5 s if you are talking with another agent
(so a finished job does not cut into your conversation with Tari). Delivered at most once (same states as single-agent mode):
a result you did not hear before disconnecting is announced on the next
connection; one you already heard part of is not replayed.

## Interruptions, stop, cancel

- Barge-in stops speech only. Jobs keep running; a reply's tool chain continues and
  its answer queues, unless your words address another agent (handover, above):
  then the chain finishes silently.
- "Stop" / "That's enough" (no name) ends the current speech and drops every queued
  answer; queued job announcements are kept and wait until you say something new.
  "Tari, stop" does this for Tari only. No model reply, no job cancelled.
- Cancelling is a request to the owner: "Astra, cancel the website task" goes to
  Astra's `cancel_task`. The UI's Cancel button cancels that job's owner's job.

## Configuration

- Voices: `voice.boson_voice` per agent (Tari `default`, Astra `oliver`; this also
  applies to Astra-only mode). `CLAWVOICE_VOICE_OVERRIDE` overrides every agent.
- Aliases: `addressing.aliases` per agent YAML.
- Listener VAD: the single-agent threshold, silence `max(BOSON_VAD_SILENCE_MS, 700)`;
  override with `CLAWVOICE_TEAM_VAD_SILENCE_MS`.
- Team members: every configured non-fixture agent (`agents/team.yaml`).

## Astra host-helper trust boundary (fixed here)

`manage.py --agent astra project ...` used to run `astra-workspace/bin/astra-project`,
a file Astra's container can write, with the host Python and the full host
environment. Now the host runs only the git-tracked template
(`infra/openclaw/workspace-astra/bin/astra-project`) with `python -I` and a minimal
environment, and only read-only subcommands (`status`, `recent`, `latest-update`,
`verify`, `preview status`). `check` (can run a shell command), `init`,
`record-update` and `preview start/stop` (signals a PID read from agent-writable
state) are refused on the host; Astra runs them inside its container.

## Tests and live checks

Offline (no provider calls): `tests/test_team_addressing.py`,
`tests/test_team_floor.py`, `tests/test_team_room.py` (routing, queue order, one
voice at a time, notice gaps, barge-in, stop, supersede, stale, transcript replay);
`apps/web/tests/agent_selector.test.cjs` (Team tab, owners, labels);
`infra/openclaw/tests/test_astra.py` (host helper trust boundary).

Live, 2026-10-24 (`probes/team_probe.py --live --speech --stop`, plus a `--notice`
run): Tari greets; two typed requests 0.3 s apart reached Tari and Astra, each used
its own cache tool, Tari spoke then Astra started the moment Tari finished (no
overlap); an unnamed request reached nobody; a spoken "Hey Tari, ..." was routed and
answered; a spoken "Hey Astra, ..." over Tari's counting cut Tari and Astra answered;
a spoken "Stop." over Astra ended speech with no audio afterwards; Tari's read-only
OpenClaw job finished while Astra answered something else and Tari's notice was
spoken afterwards (announcement delivered). A Playwright check of the page in Team
mode (two seats, labels, routing chips, one connect) passed with no page errors.

## Known limitations and untested

- Names are required for every new request; answering an agent's clarifying
  question needs the name again ("Tari, tomorrow at three"), unless it follows a bare
  name or continues within 1.5 s.
- Routing depends on the transcript; a misheard name shows "not addressed" (tune
  aliases). In Team mode agents receive the transcript, not your audio.
- About 0.7 s more latency than single-agent mode (longer VAD silence, transcription
  before routing): roughly 2 s from the end of speech to first audio.
- Any speech, even to nobody, interrupts the current speaker (single-agent policy).
  Use headphones or push-to-talk.
- Handover drops the unheard rest of an answer you turned away from (by design);
  ask that agent again by name to hear it. If your transcript arrives more than
  2.5 s after you stop talking, the old agent's queued speech can start in that
  gap; the handover cuts it once your words are routed.
- Interrupted replies are not resumed. A voice session that Higgs closes (idle
  timeout) reconnects on the next request with a short text recap only.
- Not tested live: speakers without headphones (echo), sessions longer than about
  10 minutes, three or more agents, the WebSocket Gateway transport, and the full
  acceptance script with real website creation and editing (left for manual testing).
