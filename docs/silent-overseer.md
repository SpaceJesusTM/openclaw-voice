# The silent overseer: one listener that routes, voices that talk

Lessons from building the agent selector (`web/index.html`, branch
`agent-selector-demo`, 2026-10-24) on Boson Higgs Realtime. The code is in
[server/SelectorRoom.ts](../server/SelectorRoom.ts) and
[server/SelectorVoice.ts](../server/SelectorVoice.ts).

## The concept

You talk to one voice agent at a time. A separate **overseer** agent is always
listening, **never speaks**, and has exactly one tool: `call_agent(agent, reason)`.
When it calls the tool, the agent you're talking to goes quiet and the chosen
one takes over. So there's always someone answering you, and who that is gets
decided by an agent you never hear.

```
your mic ──► overseer (text only, server VAD) ──call_agent──► switch
        └──► planner / coder / jester (audio, manual turns)
                 active one replies ──► your speaker
                 sidelined ones just listen
```

The roles are split cleanly:

- **The overseer decides; it never talks.** It's a Higgs session with
  `output_modalities: ["text"]`, so it can't make a sound. Any text it writes
  ("Let me get the coder") is dropped. Its only output with an effect is the tool call.
- **The voices talk; they never decide who talks.** Each agent has a persona
  and is told never to hand off itself ("an overseer passes the conversation").
- **Sidelined agents keep listening.** Every agent hears every turn (yours and
  the other agents') as real audio, committed into its own session. So
  whoever takes over already knows the conversation; no summary handoff is
  needed. Tested: a sidelined coder, brought back, correctly recalled what
  you'd said to the jester.
- **The overseer owns turn-taking too.** It's the one session with server VAD;
  its `speech_started` / `committed` events drive everyone's turns (see below).
- **Swaps are handoffs, not cuts.** The old agent wraps up with a short line
  while the new one's reply generates. The new one then plays straight after,
  opening with something like "I can help with that" (lesson 9).

## Recipe: building a silent overseer

1. **One router session, text only, with server VAD.** Give it one tool
   (`call_agent`) and a prompt that says it never speaks. Its VAD is the single
   authority on when your turn ends.
2. **One voice session per agent, manual turns.** On each confirmed turn,
   commit your audio into *every* agent's session, and ask only the active one
   to reply. Relay each agent's heard audio to the others the same way.
3. **Route on the router's tool call, judged on your turns only.** Agent
   replies reach the router as context notes, never as a prompt to decide.
4. **Track each reply as a state machine, matched by `response_id`**, with one
   event listener per session. Serialize every operation on a session.
5. **Pace playback on the server**, so you always know what was heard, and
   truncate each agent's memory to exactly that.
6. **Draft early, swap gracefully.** Start the active agent's reply at a short
   pause, and play it only once the turn is confirmed. On a swap, line up old
   agent's wrap-up → its handoff line → new agent, all generated in parallel.
7. **Retry server failures once**, and log every decision so problems can be
   found by reading, not guessing.

## Code map

| File | What it does |
|---|---|
| [server/SelectorRoom.ts](../server/SelectorRoom.ts) | The room: mic fan-out, overseer events, turn handling, drafts, swaps, playback pacer and lineup, relays, logging |
| [server/SelectorVoice.ts](../server/SelectorVoice.ts) | One agent's voice session: operation queue, reply state machine, cancel and retry rules |
| [src/agent/selectorAgents.ts](../src/agent/selectorAgents.ts) | Personas, the overseer prompt, the `call_agent` tool |
| [src/audio/LevelGate.ts](../src/audio/LevelGate.ts) | Adaptive speech/silence gate on mic chunks (pause detection for drafts) |
| [web/index.html](../web/index.html), `app.js`, `style.css` | The page: mic, playback, tiles, overseer decisions, transcript with per-reply latency |
| [tools/probe/selectorClient.ts](../tools/probe/selectorClient.ts) | Headless end-to-end test: talks to the room with synthesized speech |
| `tools/probe/vadToggle.ts`, `cancelEarly.ts`, `responseIds.ts`, `transcriptLength.ts` | Probes for the Higgs behaviors in the next section |

## What Higgs Realtime does (probed, 2026-10-24)

These shaped the design. Probes are in `tools/probe/`, and results are in
[probe-results.md](probe-results.md).

| Fact | Consequence |
|---|---|
| `turn_detection` **can't change after session start** ("turn_detection cannot be changed after session start"). | You can't hand VAD from the old agent to the new one on a switch. So VAD lives in one session that never changes role: the overseer. Agents use manual turns (`commit` + `response.create`). |
| Server VAD works on a **text-only** session: `speech_started`, `committed`, and an auto-created response that calls a tool. | The overseer can be both the turn detector and the router, with no audio output. |
| Every response event carries **`response_id`** (`response.created`/`done` carry `response.id`). | Route events to the reply they belong to by id. Never assume "the next event is mine". |
| `response.cancel` **after** first audio: ends with `response.done` status `cancelled`. | Safe. |
| `response.cancel` **before** first audio: `response.created` arrives, **`response.done` never does**, and the session reports nothing more, though it's actually free (a commit afterwards works). | Don't cancel before the first audio. Mark the reply cancelled and send the cancel when its first audio arrives. Keep a fallback timer that marks the session idle if nothing ever comes. |
| A commit is rejected while that session is generating (`voice_output_task_ongoing`). | Serialize everything per session in a queue: hear, note, respond. |
| Higgs 1.5 fails some responses outright (`upstream_error`, status `failed`). | Retry once: the overseer's decision per turn, and an agent reply that produced no audio. Without the retry you get silence or a missed switch. |
| `transcription.completed` sometimes **replays every earlier user turn**, re-split. | Show only the new words: align against what's already shown. |
| Time to first audio after `response.create`: ~0.55–0.8 s on 1.5 (~0.45 s on Higgs 1). | Sets the floor for response latency, unless you start generating before the turn is confirmed over (see "Latency"). |

## Lessons from the build

**1. The overseer judges your turns only, never the agents'.** At first it was
also asked to look after every agent reply, so an agent saying "let's get Coder
in" could trigger a handoff. Instead it **invented user requests** ("User asked
for a joke") and bounced agents back and forth. Now agent replies reach it
only as context notes (`(Note: planner said: "…")`) with no `response.create`,
and the tool description says "judge only the user's own words".

**2. Short VAD silences split sentences, and the router misjudges fragments.**
At Pipecat's 500 ms, a pause between sentences ended the turn, and "Okay, that
makes sense." alone got routed to planner. 700 ms fixed it. The cost is 200 ms
per turn, which early drafting wins back.

**3. Tell the router when *not* to act.** Higgs 1 switched on small talk ("let's
talk about my day" went to jester). The tool description now lists what is not
a reason to switch (small talk, thanks, "okay", half-finished sentences) and
says "when unsure, don't call it". Give the default agent a catch-all
("planner: plans and general conversation").

**4. Routing on raw audio needs the better listener.** The overseer routes on
raw audio, not on transcripts (the user's choice). Higgs 1 misheard ("Coder,
quick one" went to jester). Higgs 1.5 got every test turn right, but decides in
0.5–1.9 s instead of ~0.5 s, and fails more often (hence the retry).

**5. One listener per session, with a state machine per reply.** The worst bug:
each reply attached its own event listener to the shared session socket. A
listener left over from a cut reply saw the *next* reply's `response.created`
and cancelled it. Result: after a switch the new agent never spoke. That
wasn't fixable with patches. The fix was structural:
`SelectorVoice` has one listener for its lifetime, and each reply moves
`queued → requested → live → done`. Events are matched by `response_id`, and a
response nobody asked for is cancelled. A cut reply can't touch another one.

**6. Pace the audio yourself, so a cut knows what was heard.** Higgs generates
~10× faster than real time. The server plays the active agent at real time in
100 ms ticks and counts what it sent. A cut can then truncate the agent's
memory to what you actually heard (`conversation.item.truncate`), and relay
exactly that much to the other agents.

**7. A barge-in cuts, and the cut is relayed.** Talking over an agent stops it
at once (and drops anything lined up after it). What you heard of a cut reply
goes to the other agents as "(Note: … speaking, cut off.)" plus the audio.
Everyone's view of the conversation matches what was actually said aloud.
Swaps don't cut anymore (lesson 9).

**8. A swapped-in agent announces itself.** Its handoff note asks it to open
with a few words like "I can help with that". Then a swap sounds like someone
stepping in, not like a glitch or a voice change mid-thought. In tests, 2 of 3
swaps opened that way, and both used the example phrase word for word. For
more variety, give each persona its own example opener.

**9. Swap gracefully: the old agent wraps up while the new one generates.**
A hard cut on a swap chopped the old agent mid-word. Now, when the overseer
calls `call_agent`:

1. The old agent plays on to the end of its current clause (if that's within
   1.5 s), else 0.9 s more. Its memory is truncated at that point in advance,
   so what it "said" matches what you'll hear.
2. It's asked for one short handoff line ("Actually, Coder's better with this
   one") right away, so the line generates while its last words play.
3. The new agent's reply is requested at the same moment and held.
4. A playback **lineup** plays them back to back: last words, then the handoff
   line, then the new agent. Each follows on the same tick the previous one
   ends.

Measured: the new agent was heard ~0.1 s after the handoff line ended. Total
time from when you stop to the new agent rose to 5.3–6.0 s (from 3.2–4.2 s),
because you now hear the wrap-up instead of a cut. But the line is never silent and
nothing is chopped. If the old agent hadn't been heard yet, there's nothing
to wrap up and the new agent plays directly.

Two Higgs 1.5 sessions generating at once (handoff line + new reply) made the
new agent's first try fail with `upstream_error` on both test swaps. The
one-retry rule recovered it in time, just as the handoff line ended. Without
that retry, this design would leave silence after the handoff.

Like the openers, the handoff lines copied the prompt's example word for word
("Actually, Coder's better with this one").

**10. Debug from logs, not by asking.** Every decision is logged to
`captures/web-*.log`: turns, drafts, switches, cuts, reply outcomes, latency.
Every bug here was found by reading a log: the stale listener, the missing
`response.done`, `upstream_error`, the replayed transcripts.
`tools/probe/selectorClient.ts` drives the page headlessly with synthesized
speech, so a change can be tested end to end without a person talking.

## Latency

Where things stand (synthesized speech, 2026-10-24, measured server-side; the
browser adds ~0.1–0.2 s):

| From when you stop talking | Before | Now |
|---|---|---|
| Same agent replies | ~1.5 s | **0.9–1.45 s** (early drafts) |
| After a swap, new agent heard | 3.2–4.2 s (old agent hard-cut) | 5.3–6.0 s (old agent wraps up first) |
| Gap between old agent's handoff line and the new agent | n/a | **~0.1 s** |

The swap number went up on purpose: it now includes the old agent's wrap-up
and handoff line, so the time is spent talking, not in silence.

Where the time went, before any of these changes:

| Step | Same agent | After a swap |
|---|---|---|
| VAD confirms the turn is over (700 ms of silence) | 700 ms | 700 ms |
| Your audio committed to the agents | ~100 ms | ~100 ms |
| Overseer decides | n/a | 0.5–1.9 s |
| First audio after `response.create` (1.5) | 0.55–0.8 s | 1.3–2.3 s |
| Playback buffer | 0.1–0.2 s | 0.1–0.2 s |
| **Total** | **~1.5 s** | **~3–5 s** |

**Drafting early.** A level gate on the mic watches for pauses inside your
turn. After 200 ms of quiet, the active agent hears your turn so far and
starts a reply that is **held, not played**. If you keep talking, the draft is
cancelled unheard. When the VAD confirms the turn is over, the draft plays at
once. The VAD stays the one authority on when a turn ends; drafting only
starts the work earlier.

Lessons from drafting:

- Don't cross-check the draft against the VAD's `audio_end_ms`. Assuming it
  equals end of speech plus the VAD silence made every final draft look stale
  by ~700 ms. The draft is valid exactly when the mic gate heard no speech
  after it started, because speech drops it.
- Drafts are usually dropped *before* their first audio, which is the Higgs
  cancel case that never ends. Waiting it out stalled the next reply by
  ~1.4 s. Deferring the cancel to the first audio fixed that.
- Measured over three test runs (synthesized speech, 2026-10-24): same-agent
  replies are heard **0.9–1.45 s** after you stop, down from ~1.5 s. The VAD is no longer the
  bottleneck (drafts are still producing first audio when it confirms the
  turn). What's left is the 200 ms pause trigger plus 1.5's ~0.65 s to first
  audio. Add ~0.1–0.2 s of browser capture and playback on top.
- Pauses inside a sentence start drafts that get dropped: about 1–2 per turn in
  the tests. A dropped draft costs output tokens but no time.
- **Drafting makes the old agent start answering before a swap.** Its draft is
  ready so fast that it's been talking for 1–1.7 s by the time the overseer
  decides (0.5–1.9 s on 1.5). At first that was hard-cut; now it wraps up
  gracefully (lesson 9).
- Transcript fragments are whole clauses (`length_ms` is where each one ends
  in the audio, often 4 s or more in). A reply cut early has no complete
  fragment, so the heard text is estimated from the fragment that was playing,
  in proportion to how much of it was heard.

## Open questions and next steps

- **Get the right agent sooner on a swap.** Swaps are no longer silent, but the
  new agent arrives 5–6 s after you stop, because the old agent has already
  started answering when the overseer decides. Options: let the overseer
  decide at the pause (drafting time) instead of at the confirmed turn end. Or
  **draft all three agents in parallel** and play the overseer's pick, at about
  2–3× output tokens for the first second of each turn. Parallel generation
  also makes 1.5's `upstream_error` more likely, so the retry rule matters
  more.
- **Varied openers and handoff lines.** Agents copy the prompt's example
  phrases word for word. Give each persona its own examples.
- **Stream your audio to the agents while you speak**, in small commits, so
  less is left to process at the end. Time to first audio was ~540 ms after a
  text-only request versus ~770 ms after a 5 s audio turn.
- **Tool-only overseer.** It still writes a sentence of text sometimes.
  `tool_choice: "required"` plus a "stay" tool would make it tool-only; Higgs
  support for `required` is untested.
- **Echo.** Use headphones. An agent heard through your mic triggers the
  overseer's VAD as a barge-in. A Bluetooth headset mic (AirPods) switches to
  hands-free mode and degrades both directions.
