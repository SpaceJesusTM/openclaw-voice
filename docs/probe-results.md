# Higgs Realtime probe results

Live results from `npm run probe` (see [tools/probe/](../tools/probe/index.ts)), run 2026-10-23.
Raw event streams are written to `captures/` (gitignored). Rerun a check before relying on a result
that is more than a few weeks old.

## Results

| Check | Result |
|---|---|
| `connect` | The model is **`higgs-realtime`**. The tutorial's `higgs-realtime-1.5-preview` is rejected: *"not available on this endpoint"*. Server-side `Authorization: Bearer` works. |
| `commit` | **Manual turns work.** With `turn_detection: null`, `append` + `commit` + `response.create` gets a correct answer in 5 sessions at once. It makes no difference whether audio is paced at 100 ms per chunk or sent all at once. |
| `commit` (busy) | A commit sent while that session is generating is rejected with error `voice_output_task_ongoing` ("Skipping"). |
| `commit` (steering) | Steering one reply: `response.create {instructions}` steers it, but the **model loses the conversation** (it answers "hello, how can I help?"). A user note (`[note to self] …`) and a `session.update` to the prompt **both work**. A `system`-role message is **rejected outright**: *"Invalid role: system"*. A first reading called it "ignored" because the probe missed the error. |
| `relay` | **Agents hear real audio.** Alice's output audio, sent into Bob's input buffer at playback pace, is transcribed accurately ("…moved to Thursday at 3 p.m. Does that time work for you?"). Bob answers it in context ("Thursday at 3 PM works for me."), with first audio 474 ms after `response.create`. |
| `mind` | **A text-mode session can act as an agent's mind.** It hears committed audio and picks the right tool 3/3: `interrupt` for a wrong fact, `wait` for chit-chat, `speak` when addressed. The decision lands 520–620 ms after `response.create`. Left on its own, the model calls the tool without writing a thought first. A required first `thought` argument fixes that. The arguments arrive in one piece, not streamed progressively. |
| `timing` | Audio arrives about **10× faster than real time** (36.3 s of audio in 3.6 s). The first transcript text arrives 319 ms after `response.create`. Text comes in clause-sized pieces. `response.output_audio_transcript.length` carries `delta` (the text) and **`length_ms` = how far into the audio that fragment ends**. That gives an exact map from text to playback time. The full text was available about 32 s before playback would end. |
| `modality` | `session.update` can switch `output_modalities` between audio and text in the middle of a session, and back again. |
| `voices` | Presets **`chloe`, `eleanor`, `jake`, `marcus`, `nora`, `oliver`** all work in Realtime. Each one's first attempt failed with *"Could not validate voice …: voices API returned HTTP 429"*, even one at a time, and a retry after 2 s succeeded. The mid-session voice change also hit the 429, so that result is **inconclusive**. `GET /v1/audio/voices` lists only custom voices (empty here). |
| `idle` | **Committed audio counts as activity:** a session fed 3 s of committed speech every 60 s did not idle out in 7 minutes. **Text messages do not:** a session fed only text every 60 s idled out at exactly 5.00 min, the same as the control session that got nothing. |

## Higgs Realtime 1.5 (`higgs-realtime-1.5-preview`), rerun 2026-10-24

The table above is from `higgs-realtime`. The 1.5 preview was rejected (*"not available on this endpoint"*) until about 00:20 UTC on 2026-10-24, then accepted on every URL form (`/realtime` or `/realtime/`, with or without `?model=`). `higgs-realtime-1.5` is not a valid name. It is now the default, set with `BOSON_REALTIME_MODEL` in `.env`.

| Check | 1.5 result |
|---|---|
| `commit` | Unchanged: manual commit works in parallel sessions, a commit during generation is rejected, and steering works with a user note or `session.update`. The `instructions` override is now worse: it returns an **empty reply** (0 ms of audio). |
| `relay` | Works. Bob transcribed Alice's audio and replied in context. First audio came 973 ms after `response.create`, up from 474 ms. |
| `timing` | Same shape: about 9.6× real time, and the full text arrives about 27 s before playback ends. |
| `mind` | It still picks the right tool when it answers, but **decisions take 0.6–1.5 s** (up from 0.5–0.6 s). It **writes a spoken-style filler sentence** before the call ("I need to correct that.") even when told not to. And **2 of 6 responses failed server-side** with `upstream_error` (status `failed`, no output). The agent's mind retries a failed response once. |

## Agent selector probes, 2026-10-24

From `tools/probe/vadToggle.ts` and `tools/probe/cancelEarly.ts`.

| Check | Result |
|---|---|
| VAD toggle | `turn_detection` **can't change after session start**: *"turn_detection cannot be changed after session start"*. Pick it at connect time. |
| VAD on a text session | Works. A text-mode session with `server_vad` emits `speech_started` / `committed` and auto-creates a response, which calls a tool (Higgs 1, ~0.5 s after commit). It sometimes writes a sentence of text first. |
| `response.cancel` after audio started | Ends with `response.done`, status `cancelled`. |
| `response.cancel` before any audio | `response.created` arrives but **`response.done` never does**, and `generating` stays true. The session is actually free: a commit sent afterwards is accepted. Treat it as idle after a short wait. |
| Input transcription | `transcription.completed` sometimes replays every earlier user turn, not just the new item. |

## What this means for the design

- **Model name:** use `higgs-realtime`, configurable with `BOSON_REALTIME_MODEL`.
- **Agents hear each other through real audio relay.** Each agent's output audio goes into every other agent's input buffer at playback pace. When the speaker finishes, each listener commits. A listener that is still generating can't commit, so queue the commit until that listener's `response.done`. Generation runs 10× faster than real time, so that window is short.
- **The mind:** a text-mode session per agent, with tools `speak` / `interrupt` / `wait`, each with a required `thought` as its first argument. Expect about 0.5–0.6 s per decision. Thoughts arrive whole, not word by word.
- **Passing `speak(intent)` to the voice:** use `session.update` to set the voice's prompt to *role + intent* just before `response.create`. Never use the per-response `instructions` override.
- **Cut-offs:** the speaker's text is known long before it is heard, and `length_ms` says exactly when each fragment plays. So a listener's mind can judge each fragment ahead of time, and the orchestrator can schedule the cut-off at the fragment that's wrong. Truncate the speaker's item at the played position.
- **Spawning agents:** stagger voice setup and retry on the voices API's 429.
- **Idle timeout:** every voice and mind session hears the room's audio, so sessions stay alive while anyone talks. After 5 silent minutes, all of them close. Text can't keep a session alive, so reconnect with a summary message instead.
- **One session per agent may be possible:** modality switching works, so a single session could think in text, then switch to audio and speak. The cost is that the thoughts would sit in the voice's own history. Two sessions keep the thoughts fully private, which is what was decided. Keep two for now.
