# The silent overseer: one listener that routes, voices that talk

Lessons from building the agent selector (`web/index.html`, branch
`agent-selector-demo`, 2026-10-24) on Boson Higgs Realtime. The code is in
[server/SelectorRoom.ts](../server/SelectorRoom.ts) and
[server/SelectorVoice.ts](../server/SelectorVoice.ts).

**These lessons are about the pattern, not one cast of agents.** The demo
happens to run three personas, but nothing here depends on who they are or how
many there are. Below, **A** is the agent you're talking to and **B** is
another agent: the one being swapped in, or the owner of a background task.
Quoted lines use placeholders like `<B>` for an agent's name.

## The concept

You talk to one voice agent at a time. A separate **overseer** agent is always
listening and **never speaks**. It acts only through tools, and its main one is
`call_agent(agent, reason)`. When it calls that tool, the agent you're talking
to goes quiet and the chosen one takes over. So there's always someone
answering you, and who that is gets decided by an agent you never hear.

```
your mic ──► overseer (text only, server VAD) ──call_agent──► switch
        └──► every voice agent (audio, manual turns)
                 the active one replies ──► your speaker
                 the sidelined ones just listen
```

The roles are split cleanly:

- **The overseer decides; it never talks.** It's a Higgs session with
  `output_modalities: ["text"]`, so it can't make a sound. Any text it writes
  ("Handing this over") is dropped. Only its tool calls have an effect.
- **The voices talk; they never decide who talks.** Each agent has a persona
  and is told never to hand off itself ("an overseer passes the conversation").
- **Sidelined agents keep listening.** Every agent hears every turn (yours and
  the other agents') as real audio, committed into its own session. So
  whoever takes over already knows the conversation; no summary handoff is
  needed. Tested: an agent brought back after being sidelined correctly
  recalled what you'd told a different agent in the meantime.
- **The overseer owns turn-taking too.** It's the one session with server VAD;
  its `speech_started` / `committed` events drive everyone's turns (see below).
- **Swaps are handoffs, not cuts.** The old agent wraps up with a short line
  while the new one's reply generates. The new one then plays straight after,
  opening with something like "I can help with that" (lesson 9).
- **Background news goes through whoever you're talking to.** Any agent can
  start a background task. When one finishes or needs your OK, the alert goes on
  one room-level queue, and the agent you're talking to tells you at the next
  natural pause (see "Background tasks and the alert queue").

## Recipe: building a silent overseer

1. **One overseer session, text only, with server VAD.** Give it its tools
   (`call_agent`, plus any other control decisions such as approvals) and a
   prompt that says it never speaks. Its VAD is the single authority on when
   your turn ends.
2. **One voice session per agent, manual turns.** On each confirmed turn,
   commit your audio into *every* agent's session, and ask only the active one
   to reply. Relay each agent's heard audio to the others the same way.
3. **Route on the overseer's tool call, judged on your turns only.** Agent
   replies reach the overseer as context notes, never as a prompt to decide.
4. **Track each reply as a state machine, matched by `response_id`**, with one
   event listener per session. Serialize every operation on a session.
5. **Pace playback on the server**, so you always know what was heard, and
   truncate each agent's memory to exactly that.
6. **Draft early, swap gracefully.** Start the active agent's reply at a short
   pause, and play it only once the turn is confirmed. On a swap, line up the old
   agent's wrap-up → its handoff line → the new agent, all generated in parallel.
7. **Retry server failures once**, and log every decision so problems can be
   found by reading, not guessing.
8. **Background news on one queue, told by whoever you're talking to**, at a
   natural pause, and only counted as told once it was actually heard.
9. **Agents talk, the overseer acts.** Decisions that change state (routing,
   approvals) belong to the overseer. Voice agents keep only actions they
   reliably take while talking (starting a task).

## Code map

| File | What it does |
|---|---|
| [server/SelectorRoom.ts](../server/SelectorRoom.ts) | The room: mic fan-out, overseer events, turn handling, drafts, swaps, playback pacer and lineup, relays, task alerts, logging |
| [server/SelectorVoice.ts](../server/SelectorVoice.ts) | One agent's voice session: operation queue, reply state machine, tool calls, cancel and retry rules |
| [server/TaskBoard.ts](../server/TaskBoard.ts) | Background tasks (fake timers for now): start, approval, done; emits alerts |
| [src/agent/selectorAgents.ts](../src/agent/selectorAgents.ts) | The roster (the demo's personas), the overseer prompt and its tools (`call_agent`, `answer_approval`), and the agents' `start_task`. Change the cast here; the room reads it. |
| [src/audio/LevelGate.ts](../src/audio/LevelGate.ts) | Adaptive speech/silence gate on mic chunks (pause detection for drafts) |
| [web/index.html](../web/index.html), `app.js`, `style.css` | The page: mic, playback, one tile per agent, overseer decisions, Tasks panel with Approve / Deny, transcript with per-reply latency |
| [tools/probe/selectorClient.ts](../tools/probe/selectorClient.ts) | Headless end-to-end test with synthesized speech: `routing` and `tasks` scenarios |
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
| Higgs 1.5 fails some responses outright (`upstream_error`, status `failed`), more often when several sessions generate at once. | Retry once: the overseer's decision per turn, and an agent reply that produced no audio. Without the retry you get silence or a missed switch. |
| A reply's first output item can be a **function call**, and truncating it is rejected ("Only role based items are supported"). | Track only `message` items as a reply's spoken item. |
| `transcription.completed` sometimes **replays every earlier user turn**, re-split. | Show only the new words: align against what's already shown. |
| Transcript fragments are whole clauses; `length_ms` is where each one ends in the audio (often 4 s or more in). | A reply cut early may have no complete fragment; estimate the heard words from the fragment that was playing. |
| Time to first audio after `response.create`: ~0.55–0.8 s on 1.5 (~0.45 s on Higgs 1). | Sets the floor for response latency, unless you start generating before the turn is confirmed over (see "Latency"). |

## Lessons from the build

**1. The overseer judges your turns only, never the agents'.** At first it was
also asked to look after every agent reply, so an agent saying "let's bring in
`<B>`" could trigger a handoff. Instead it **invented user requests** the user
never made, and bounced agents back and forth. Now agent replies reach it only
as context notes (`(Note: <A> said: "…")`) with no `response.create`, and the
tool description says "judge only the user's own words".

**2. Short VAD silences split sentences, and the router misjudges fragments.**
At Pipecat's 500 ms, a pause between sentences ended the turn, and a fragment
like "Okay, that makes sense." alone got routed to a different agent. 700 ms
fixed it. The cost is 200 ms per turn, which early drafting wins back.

**3. Tell the router when *not* to act.** Higgs 1 switched to a specialist on
small talk ("let's talk about my day"). The tool description now lists what is
not a reason to switch (small talk, thanks, "okay", half-finished sentences)
and says "when unsure, don't call it". Give one agent a catch-all ("general
conversation") so ordinary talk has a home.

**4. Routing on raw audio needs the better listener.** The overseer routes on
raw audio, not on transcripts (the user's choice). Higgs 1 misheard a request
that named one agent and sent it to another. Higgs 1.5 got every test turn
right, but decides in 0.5–1.9 s instead of ~0.5 s, and fails more often (hence
the retry).

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
swaps opened that way, and agents used the example phrase word for word. For
more variety, give each persona its own example opener.

**9. Swap gracefully: the old agent wraps up while the new one generates.**
A hard cut on a swap chopped the old agent mid-word. Now, when the overseer
swaps from A to B:

1. A plays on to the end of its current clause (if that's within 1.5 s), else
   0.9 s more. Its memory is truncated at that point in advance, so what it
   "said" matches what you'll hear.
2. A is asked for one short handoff line ("Actually, `<B>`'s better with this
   one") right away, so the line generates while its last words play.
3. B's reply is requested at the same moment and held.
4. A playback **lineup** plays them back to back: A's last words, then A's
   handoff line, then B. Each follows on the same tick the previous one ends.

Measured: B was heard ~0.1 s after A's handoff line ended. Total time from
when you stop to B rose to 5.3–6.0 s (from 3.2–4.2 s), because you now hear
A's wrap-up instead of a cut. But the line is never silent and nothing is
chopped. If A hadn't been heard yet, there's nothing to wrap up and B plays
directly.

Two Higgs 1.5 sessions generating at once (A's handoff line + B's reply) made
B's first try fail with `upstream_error` on both test swaps. The one-retry
rule recovered it in time, just as the handoff line ended. Without that retry,
this design would leave silence after the handoff.

Like the openers, the handoff lines copied the prompt's example word for word.

**10. Debug from logs, not by asking.** Every decision is logged to
`captures/web-*.log`: turns, drafts, switches, cuts, reply outcomes, task
alerts, latency. Every bug here was found by reading a log: the stale
listener, the missing `response.done`, `upstream_error`, the replayed
transcripts, the missed approvals. `tools/probe/selectorClient.ts` drives the
page headlessly with synthesized speech, so a change can be tested end to end
without a person talking.

## Background tasks and the alert queue

**The idea.** Agents don't only talk: any of them can kick off background work
("`<B>`, build me a login page"). When that work finishes or needs your OK,
you hear about it from **whoever you're talking to right now**, not from the
agent that did the work, and never mid-sentence. So the conversation keeps one
voice, and news comes in at a natural pause, the way a colleague would say "oh,
by the way…".

**How it works.**

- Every agent has one tool, `start_task(title, for_agent?)`, which starts work
  for itself or for another agent ("have `<B>` build it" works while you talk
  to A).
- **The overseer makes the control decisions**: `call_agent` (routing) and
  `answer_approval(task_id, decision)`. When an approval request is heard, it
  gets a note naming the task, and your spoken answer becomes its tool call.
  The agent you're talking to just acknowledges ("Great, I'll tell `<B>` to go
  ahead"). Agents talk; the silent overseer acts.
- `TaskBoard` runs the tasks. For now they're **fake timers**: each works for a
  while, then finishes or asks for approval (every other task asks).
  `SELECTOR_TASK_SCALE` shortens the timings for tests (the `web-fast-tasks`
  launch config uses 0.3).
- When a task needs attention, an **alert** goes on one room-level queue. The
  other agents and the overseer get a context note right away.
- **Natural gap**: nothing playing, lined up or drafted, you're not mid-turn,
  and 1.2 s of quiet from everyone. Then the agent you're talking to gets
  one reply that bundles all pending news.
- **Delivered only once heard.** If you talk over the announcement, or the reply
  says nothing (it only called a tool), the news goes back on the queue for
  the next gap. An approval answered in the meantime (say, with the page's
  button) is dropped from the queue, not announced.
- You answer **by voice to whoever you're talking to** (the overseer hears it
  and passes it on), or with **Approve / Deny buttons** on the page's Tasks panel.

**Lessons from testing it** (synthesized speech plus the browser, 2026-10-24):

- **Voice agents are unreliable at talking *and* acting in one response.** With
  `answer_approval` on the agents, A heard "Yes, approve it", said "Great, I'll
  apply it now", and never called the tool, so the task stayed stuck.
  Approvals are a control decision like routing, so the silent overseer makes
  them. It hears every word, never talks, and tool calls are its only job.
  `start_task` stayed with the agents: they called it reliably in every test.
- **Whose task decides the grammar.** When A owns the task, it speaks as
  itself: "By the way, I've got the `<title>` ready. OK if I apply it?", then
  "Great, I'll apply it now". Never "`<A>` has…" about itself, or "I've passed
  your approval along to `<A>`". When B owns it, it's the reverse: "By the way,
  `<B>` has the `<title>` ready and wants your OK", then "Great, I'll tell
  `<B>` to go ahead", never "I'll apply it". The announcement note spells out
  both: how to ask, and how to acknowledge your answer. A tool-only reply's
  spoken follow-up says exactly what happened, in first person.
- **Give tool choices explicit names.** With `approve: true/false`, a clear
  "Yes, approve it, go ahead" came through as `false`, and the agent reported
  passing on a "no". An enum, `decision: "approve" | "deny"`, fixed it.
- **Say when *not* to start a task.** "Let's plan the launch" made an agent
  start a "launch plan" job. The tool now says: only when the user explicitly
  asks for something to be made or done as a separate job, never for things
  you can talk through.
- **The same request reaches two agents.** A started the job for B, and B,
  once swapped in, started it again. The task board treats the same owner and
  title, while one is running or waiting, as the same task.
- **Tool calls in a draft act only when the draft is played.** A held draft that
  gets dropped (you kept talking) must not start a task you never heard agreed to.
- **The answer belongs to the agent who asked.** A asked "`<B>` wants your OK,
  go ahead?", you said "Yes, approve it", and the overseer swapped to B, because
  the approval was "about" B's work. Its tool description already said
  approvals aren't a reason to switch, so this is a room rule now: once an
  approval request is heard, the overseer can't swap to that task's owner until
  your first turn after the asking agent has replied to your answer. The rule
  spans split turns, and the reply can end before the overseer's decision
  arrives. The overseer gets a note saying so too. The cost: "yes, and put `<B>`
  on" in the same breath stays with the asker; ask again on your next turn.
- **Gaps race with you.** An announcement can start just as you start
  talking. It's cut and re-queued, and told at the next gap. That's expected.
- **Fake tasks get invented details.** With nothing real behind a "done", an
  agent described what it "built" ("fields, validation, a clean layout").
  Real task results will fix that.

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
  replies are heard **0.9–1.45 s** after you stop, down from ~1.5 s. The VAD is
  no longer the bottleneck (drafts are still producing first audio when it
  confirms the turn). What's left is the 200 ms pause trigger plus 1.5's
  ~0.65 s to first audio. Add ~0.1–0.2 s of browser capture and playback on top.
- Pauses inside a sentence start drafts that get dropped: about 1–2 per turn in
  the tests. A dropped draft costs output tokens but no time.
- **Drafting makes the old agent start answering before a swap.** Its draft is
  ready so fast that it's been talking for 1–1.7 s by the time the overseer
  decides (0.5–1.9 s on 1.5). At first that was hard-cut; now it wraps up
  gracefully (lesson 9).

## Open questions and next steps

- **Real background work.** Swap the fake timers for real runs (for example a
  Claude Agent SDK run in a workspace folder, whose permission prompts become
  approval alerts), and pass real results into the "done" news.
- **Get the right agent sooner on a swap.** Swaps are no longer silent, but the
  new agent arrives 5–6 s after you stop, because the old agent has already
  started answering when the overseer decides. Options: let the overseer
  decide at the pause (drafting time) instead of at the confirmed turn end. Or
  **draft every agent in parallel** and play the overseer's pick; the token
  cost grows with the number of agents (for N agents, about N× output tokens
  for the first second of each turn). Parallel generation also makes 1.5's
  `upstream_error` more likely, so the retry rule matters more.
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
