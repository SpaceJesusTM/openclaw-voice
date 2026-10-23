# Higgs Realtime 1.5 Preview lab

A local browser playground and headless probe for **`higgs-realtime-1.5-preview`**.
No OpenClaw runtime, database, or agent routing is involved. See the existing
[acceptance plan](TEST_PLAN.md) and [recorded results](TEST_RESULTS.md).

## Run

Requires Node **22.12+** and npm. From the repository root:

```sh
cd experiments/higgs-realtime-1.5
npm ci
cp -n .env.example .env
# Edit .env: set BOSON_API_KEY to your actual Boson API key.
npm run dev
```

Open **http://127.0.0.1:3000**. The server binds to loopback. `PORT=3001 npm run dev`
changes the port. Restart after editing `.env`; the existing environment template
is preserved. Without a key, the UI and speaker tone work; Connect reports a
missing-key error without contacting Boson.

The backend reads `BOSON_REALTIME_MODEL` with the preview ID as its default and
rejects any other value. Both the WebSocket URL and `session.update.session.model`
use that value. The UI displays the **actual resolved acknowledgement**, including
a missing or wrong model, and stops on mismatch. There is no model fallback or
automatic reconnect. Voice ID and optional native web search apply on connection;
disconnect before changing either. Use a voice ID available to your Boson account.

`BOSON_API_KEY` stays in Node. `POST /api/token` mints a fresh 600-second secret via
`/v1/realtime/client_secrets`. The browser authenticates with the documented
`realtime` and `bai-client-secret.<ephemeral value>` WebSocket subprotocols.
Only the ephemeral value and expiry reach the browser; upstream error bodies and
session credentials are excluded. No API key, recording, or `.env` is bundled.
This loopback server is for local validation.

## Validate

```sh
npm run typecheck
npm test
npm run build
npx playwright install chromium
npm run test:browser
```

Unit and browser tests need no Boson key. Browser tests simulate Boson responses
and use a synthetic microphone; their passes are **not live API passes**.

After adding your key, run the live probe independently of the dev server:

```sh
npm run probe -- --live
# Optional additional native-search turn:
npm run probe -- --live --search "Search the web for the latest Boson AI news"
# Optional account-supported voice and a different initial typed prompt:
npm run probe -- --live --voice default --text "Say hello in one sentence"
```

`npm run probe -- --help` lists options. The probe checks the exact acknowledged
model, a typed turn with generated audio and transcript, then an actual planner
call, its local result, and an answer containing the first fixture title. It
exits nonzero on errors, unexpected completion status, missing evidence, early
socket closure, or timeouts. Search additionally requires server call/result
events and a subsequent audio answer. Reports and sanitized event metadata go to
ignored `captures/`; raw audio and credentials are omitted. These reports contain
conversation text. `--live` is required to make API requests.

For the built client:

```sh
npm run build
npm start
```

## Manual live procedure — run in order

1. **A / typed speech:** Connect. Confirm both model indicators show the preview
   ID and inspect Resolved session. Send “Say hello in one sentence.” Confirm
   audible streaming speech and a matching transcript. Test speaker plays a
   local tone independently of Boson.
2. **B / microphone:** Start microphone, allow access, and watch the level meter.
   Speak and pause naturally. Confirm `input_audio_buffer.speech_started` /
   `speech_stopped`, input transcription, and a spoken response. Stop microphone
   and confirm the browser releases it. Stopping sends the partial capture frame
   and 700 ms of paced silence so server VAD can finish the final turn.
3. **C / interruption:** Ask for several sentences, then speak while playback is
   ongoing. Audio must stop promptly. Confirm `conversation.item.truncate` carries
   the interrupted item ID and a plausible heard duration, including zero if no
   audio played. Repeat after generation finishes while audio is still queued.
   The transcript marks interruption; expandable generated text may include
   unheard words. Stop playback exercises this locally too. Neither path cancels
   a local function. Tool work finishes and returns its result; continuation is
   deferred while a user turn or another response is active.
4. **D / planner:** Click the planner prompt or ask “What's on my planner today?”
   Tool activity must show LOCAL `get_demo_planner`, arguments `{"day":"today"}`,
   result, elapsed milliseconds, and any error. The spoken answer must use
   **Copper Otter rehearsal** and **NEBULA-742** from
   [fixtures/planner.json](fixtures/planner.json). Change those values, reload,
   reconnect, and ask again to prove the answer comes from the handler. These
   are fictional test values; the function execution is real.
5. **E / optional:** Disconnect, enable Native web search, reconnect, and ask for
   current public information. Confirm a SERVER `web_search` item with
   `executor: "server"`, a matching `function_call_output` in received events,
   and a spoken answer. This must not produce a locally sent function output or
   client continuation. Separately reconnect with a supported voice ID and
   verify its resolved value and audible voice. Rejections are shown explicitly.

## Troubleshooting and limits

| Symptom | Check |
| --- | --- |
| Missing API key | Create this directory's `.env`, set `BOSON_API_KEY`, restart. No key is needed for offline tests. |
| Secret minting HTTP 401/403 | Verify the key and account access; the original key is never sent to the browser. |
| Model missing, different, or rejected | Inspect the error and resolved session. Confirm preview access with Boson. Do not switch to the legacy model. |
| No acknowledgement | Connection fails after 15 seconds in the UI / 20 seconds in the probe. Check network, entitlement, model, and socket close code. |
| Close 3000 / 4429 / 1013 | Authentication/expired secret / billing entitlement / concurrency limit. Reconnect manually after fixing the cause. |
| Silence or microphone failure | Try the speaker tone, browser permissions, selected OS device, and Chrome on localhost. The graph must support 24 kHz. Use headphones to avoid echo triggering VAD. |
| Voice rejected | Use `default` or a known account-supported voice ID, then reconnect. No voice fallback is attempted. |
| Planner call absent | Inspect the log for actual function events. Run the probe; it fails if the model invents a planner answer or omits the fixture title. |
| Probe timeout | A timeout is a failure, not a pass. Inspect the saved report; optionally use `--timeout 60000`. |
| Port busy | Set a different `PORT`. Browser tests use port 3105 and require it to be free. |

Playback uses scheduled PCM buffers and per-item rendered sample timing.
Truncation is sent when the interrupted audio/item completes; late chunks from
interrupted items are discarded. Heard time is an estimate using the browser's
reported device latency. Human hearing, real device latency, Boson VAD behavior,
and preview reliability still require the live/manual checks above. Sessions
remain in memory; reloading or reconnecting starts a new one. The browser log
keeps the latest 300 entries.

## Code and references

- `server/`: local environment/configuration, client-secret endpoint, dev/build serving.
- `src/protocol.ts`, `sessionConfig.ts`, `session.ts`: shared wire parsing, strict
  model verification, and function batching/deduplication, also used by the probe.
- `src/tools.ts`, `fixtures/planner.json`: validated local handler and editable data;
  replace the handler through `ToolHandler` to connect an adapter later.
- `src/audio.ts`, `pcm.ts`, `interruptions.ts`: capture, PCM streaming, playback positions,
  and interrupted-item bookkeeping; independent of local function lifetime.
- `tools/probe.ts`, `tests/`: live diagnostics and offline unit/browser validation.

Protocol patterns were checked against Boson's Apache-2.0
[preview tutorial](https://github.com/boson-ai/Higgs-Realtime-Tutorial/tree/f8d23e7eaacf412a175522f5996e5eb3a4d9a42e)
(`RealtimeClient.ts`, `sessionConfig.ts`, `tools/probe.ts`, and `docs/part-4.md`).
This implementation was authored here; tutorial source files were not copied.
Native tool handling follows the current official
[tool-use documentation](https://docs.boson.ai/models/higgs-realtime/guides/tool-calling).
Some general documentation examples still name `higgs-realtime`; this experiment
always requires the preview ID from the preview branch.
