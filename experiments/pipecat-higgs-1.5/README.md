# Pipecat + Higgs Realtime 1.5 Preview

An isolated ClawVoice validation experiment using the official Pipecat runner,
WebRTC playground, and Boson speech-to-speech service. Step 1 is preserved in
`../higgs-realtime-1.5/`. No OpenClaw process or additional provider key is needed.

## Start locally

From the repository root, with Python 3 available, on macOS, Linux, or Windows:

```sh
cd experiments/pipecat-higgs-1.5
python tasks.py setup
python tasks.py dev
```

`tasks.py` uses only the standard library and handles platform differences
(`.bootstrap/bin` vs `.bootstrap\Scripts`, UTF-8 console output on Windows). Use
`python3` where `python` is not on the PATH. With `make` installed, `make setup`,
`make dev`, and the other targets below are aliases for the same tasks.

Open **http://127.0.0.1:7860**, leave **SmallWebRTC** selected, allow microphone
access, let devices initialize, then click **Connect**. Wait for the greeting.
If the stock UI remains at **INITIALIZED**, click Connect again once device
initialization completes.
Use the text box for a typed prompt or speak into the microphone. The runner
ships the browser UI; this experiment adds no frontend audio implementation.

`setup` installs uv 0.13.5 into ignored `.bootstrap/`, lets uv download Python
3.11 into `.bootstrap/python`, and syncs the committed `uv.lock`. If uv is already
available, `uv sync --locked --python 3.11` and
`uv run --locked bot.py -t webrtc --host 127.0.0.1 --port 7860` are equivalent;
on Windows, also set `PYTHONUTF8=1` for any other Python commands you run directly.
Runtime versions are **pipecat-ai 1.12.0** and **pipecat-boson 0.1.4**. Do not
upgrade Pipecat to 1.13 with this integration: its invocation API is incompatible.

The key is automatically read from **`../higgs-realtime-1.5/.env`**. Nonempty
values in this directory's optional `.env` override that file; nonempty process
variables override both. `.env.example` lists the settings. Credentials stay in
Python; the browser connects only to the local Pipecat transport. Unlike Step 1,
no Boson browser client secret is needed because Python owns the Boson socket.

The URL query and `BosonRealtimeLLMService(model=...)` explicitly select
**`higgs-realtime-1.5-preview`**. Other model values or endpoints fail at startup.
Every resolved `session.created` / `session.updated` is checked before the SDK
processes it. Missing/mismatched models terminate the pipeline; no fallback or
automatic reconnect is attempted. Look for `model.verified` in the terminal and
session acknowledgements in the playground's server-message events.

## Test

```sh
python tasks.py check        # Offline tests, Ruff, Python compilation; no API calls
python tasks.py live-probe   # In another terminal while dev is running
```

The headless probe uses the same `/start`, WebRTC audio, and RTVI channel as the
playground. It requires the exact resolved model, decoded non-silent media,
completed spoken turns, and a real `get_demo_planner` result followed by a reply
using the first fixture title. Timeouts, unexpected disconnects, and missing
evidence fail with a nonzero exit status. Arguments after `live-probe` go to
`probe.py`; `python tasks.py live-probe --help` makes no call.

Manual checks, in order:

1. Type **“Say: Higgs preview connection verified.”** Listen for speech and check
   its transcript. Confirm the terminal's acknowledged preview model.
2. Speak a short question, pause, and check your transcript and the spoken answer.
3. Ask the bot to count slowly to forty, then speak over it. Playback should stop
   and the new request should receive a reply. Session traces should include
   `conversation.item.truncate` identifying the interrupted assistant item.
4. Ask **“What's on my planner today?”** Look for `tool.started` and `tool.result`
   with arguments, `call_id`, result, and timing. The response should use the
   distinctive **Copper Otter rehearsal** from Step 1's shared
   [planner fixture](../higgs-realtime-1.5/fixtures/planner.json). Edit its values
   and start a fresh connection to verify the change. Tool details are also
   exposed through RTVI for this one fictional-data function.

For a real Pipecat mock-worker job plus native Boson web search, stop the current
server, then run:

```sh
python tasks.py worker-demo
# In another terminal:
python tasks.py live-probe --search "Search the web for the official Boson AI website and tell me its domain."
```

The function delegates through Pipecat's actual worker bus when
`CLAWVOICE_MOCK_WORKER=true`. The worker's data is mocked. `web_search` is declared
as a native server tool and never executed locally. The probe waits for its
result and subsequent spoken answer, rather than accepting the search preamble.

Optional synthetic microphone/barge-in checks. `test-audio` generates the test
utterances without another API or key, using macOS `say` or the built-in Windows
speech synthesizer:

```sh
python tasks.py test-audio
python tasks.py live-probe --audio-wav captures/planner-input.wav --barge-wav captures/interrupt-input.wav
```

On Linux, record `captures/planner-input.wav` (“What is on my planner today?”) and
`captures/interrupt-input.wav` (“Stop counting. Say interruption confirmed.”)
yourself, as 48 kHz 16-bit mono WAV files. `probe_audio.py`
is a test microphone using aiortc's MediaPlayer; production capture/playback stays
in Pipecat. A spoken planner repeat can use the earlier tool result in context;
the audio test checks VAD, transcription, and output, while the separate planner
turn checks actual function execution.

For equivalent live tests of the unchanged Step 1 app, install its Playwright
browser (`npm ci` and `npx playwright install chromium` in that directory), start
it on port 3002 (`PORT=3002 npm run dev`; in PowerShell,
`$env:PORT=3002; npm run dev`), then run here:

```sh
node compare_direct.mjs --live
```

This script uses the same WAV inputs through a virtual browser microphone. It
checks text, planner, speech input, interruption markings, server truncation,
and the new spoken reply. It does not change Step 1 source.

## Diagnostics and limitations

- Each session writes sanitized event metadata and generated transcripts to
  ignored `captures/session-*.jsonl`; probes save `captures/probe-*.json`. These
  files contain conversation text, but omit audio payloads and credentials.
- Incorrect keys produce an authentication diagnostic, including close code
  3000. Fix the key and reconnect. Invalid models fail locally. Tool validation
  or fixture failures are returned as useful function results.
- The stock runner can take time to initialize devices; wait before Connect.
  Use localhost, check microphone permissions, and wear headphones. Browser
  initialization may request a camera; it is unnecessary for this audio bot.
- `BOSON_VAD_THRESHOLD`, `BOSON_VAD_SILENCE_MS`, and `BOSON_NOISE_REDUCTION`
  expose documented options for later experiments. Blank values use server
  defaults. Background-voice rejection and acoustic-device behavior need manual
  testing; no VAD optimization is claimed here.
- Two small compatibility fixes are included: forwarding typed append frames
  past the user aggregator, and retaining the SDK's audio reference until
  transport playback finishes so its native truncation survives event ordering.
  Both have regression tests. The raw dispatcher/response hooks are private APIs,
  making the version pin important. No custom socket, playback buffer, or local
  tool dispatcher was added.
- Pipecat's native truncation uses an estimate based on elapsed time and received
  audio duration. Multiple queued assistant items and device latency need more
  coverage before relying on exact heard-content accounting.
- Worker compatibility is demonstrated with one short mocked backend job.
  Actual Gateway authentication, long-running jobs, routing, cancellation policy,
  and speaker-floor control remain future work.

See [results](TEST_RESULTS.md), [comparison and recommendation](COMPARISON.md),
and [source attribution](NOTICE.md) for the measured evidence and investigation.
