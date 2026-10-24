# The silent overseer: one listener that routes, voices that talk

Lessons from building the agent selector demo (`web/selector.html`, branch
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

**7. A barge-in or switch cuts, and the cut is relayed.** What you heard of a
cut reply goes to the other agents as "(Note: … speaking, cut off.)" plus the
audio. Everyone's view of the conversation matches what was actually said aloud.

**8. A swapped-in agent announces itself.** Its handoff note asks it to open
with a few words like "I can help with that". Then a swap sounds like someone
stepping in, not like a glitch or a voice change mid-thought. In tests, 2 of 3
swaps opened that way, and both used the example phrase word for word. For
more variety, give each persona its own example opener.

**9. Debug from logs, not by asking.** Every decision is logged to
`captures/web-*.log`: turns, drafts, switches, cuts, reply outcomes, latency.
Every bug here was found by reading a log: the stale listener, the missing
`response.done`, `upstream_error`, the replayed transcripts.
`tools/probe/selectorClient.ts` drives the page headlessly with synthesized
speech, so a change can be tested end to end without a person talking.

## Latency

Where the time goes, from when you stop talking (measured before drafting):

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
- Measured (synthesized speech, 2026-10-24): same-agent replies are heard
  **0.90–1.23 s** after you stop, down from ~1.5 s. The VAD is no longer the
  bottleneck (drafts are still producing first audio when it confirms the
  turn). What's left is the 200 ms pause trigger plus 1.5's ~0.65 s to first
  audio. Add ~0.1–0.2 s of browser capture and playback on top.
- Pauses inside a sentence start drafts that get dropped: about 1–2 per turn in
  the tests. A dropped draft costs output tokens but no time.
- **Drafting makes a wrong agent more audible on swaps.** The old agent's draft
  is ready so fast that you hear it for 1–1.7 s before the overseer's
  decision (0.5–1.9 s on 1.5) cuts it. Swaps are heard 3.3–4.0 s after you stop.
  That's what parallel drafts (below) should fix.
- Transcript fragments are whole clauses (`length_ms` is where each one ends
  in the audio, often 4 s or more in). A reply cut early has no complete
  fragment, so the heard text is estimated from the fragment that was playing,
  in proportion to how much of it was heard.

## Open questions and next steps

- **Swaps are still slow** (overseer decision + a second first-audio wait).
  Structural fix: at the pause, **all three agents draft in parallel**, then
  play the overseer's pick and cancel the rest. Swap latency becomes the
  slower of the two instead of their sum. The cost is about 2–3× output tokens
  for the first second of each turn.
- **Stream your audio to the agents while you speak**, in small commits, so
  less is left to process at the end. Time to first audio was ~540 ms after a
  text-only request versus ~770 ms after a 5 s audio turn.
- **Tool-only overseer.** It still writes a sentence of text sometimes.
  `tool_choice: "required"` plus a "stay" tool would make it tool-only; Higgs
  support for `required` is untested.
- **Echo.** Use headphones. An agent heard through your mic triggers the
  overseer's VAD as a barge-in. A Bluetooth headset mic (AirPods) switches to
  hands-free mode and degrades both directions.
