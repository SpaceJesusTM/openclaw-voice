# Validation results — 2026-10-23 (America/Toronto)

Environment: macOS arm64, Python 3.11.6, uv 0.13.5, Pipecat 1.12.0,
pipecat-boson 0.1.4, aiortc 1.15.0, Node 24.16.0 and Playwright Chromium.
The existing direct experiment's local key was reused server-side.

## Automated checks

| Check | Result |
| --- | --- |
| Locked Python installation | PASS: `uv sync --locked`, 112 packages installed from a 119-package lock graph |
| Pipecat offline suite | PASS: 18 tests, including real pipeline text routing, model checks, worker bus, failures, WAV clock continuity and cancellation/truncation event ordering |
| Static checks | PASS: Ruff, Python compilation, and Node syntax check for the optional direct comparison script |
| Stock runner browser UI | PASS: Chromium connected, rendered greeting and typed assistant reply, no page errors; synthetic devices |
| Direct implementation regression checks | PASS: typecheck, 24 unit tests, production build, 7 mocked browser tests |
| Credential leakage | PASS: scanned 60 source/artifact files and direct captures against the actual configured key; zero matches (credential files excluded) |

Python emitted upstream `audioop` and `importlib.resources.path` deprecation
warnings. They did not fail these checks. No dependency source was patched.

## Live API capability results

These calls were executed against Boson with the existing key. Microphone inputs
below are synthetic WAV speech, not physical-device or acoustic measurements.

| Capability | Direct Step 1 | Pipecat Step 2 |
| --- | --- | --- |
| Exact preview session model | PASS: resolved server acknowledgement | PASS: initial and updated acknowledgements checked before SDK processing |
| Typed prompt → speech/transcript | PASS: original headless probe and browser comparison | PASS: runner WebRTC probe, generated audio, transcripts and decoded non-silent media |
| Audio input → speech/transcript | PASS: same WAV through virtual browser microphone | PASS: WAV over WebRTC, server speech-start/stop events, finalized STT text and spoken answer |
| Barge-in | PASS: interruption marker, server truncation, new reply | PASS after native-audio lifetime fix: interruption event, server truncation, new reply |
| Actual planner function | PASS: real local handler/result and fixture-based answer | PASS: native Pipecat handler/result and fixture-based answer |
| Native Boson web search | Implemented; not live-tested in this comparison | PASS: server call/result plus subsequent answer; no local execution |
| Mock backend worker + Higgs S2S | Outside Step 1 scope | PASS: actual bus job returns mock fixture and Higgs speaks it |
| Invalid credentials | Offline validation; no deliberate invalid-key live call | PASS negative test: explicit close 3000, “Invalid API key”; probe exits 1 |
| Unsupported model | PASS offline rejection; no legacy model requested | PASS offline startup/runtime/ack rejection; no legacy model requested |
| Failed local tool / disconnect | PASS offline tests | PASS offline tests; normal client disconnect also ends live runner sessions |
| Physical microphone and speaker | User reports basic Step 1 behavior; not independently measured here | MANUAL: physical devices, echo, room noise and perceived audio quality remain unverified |

The final Pipecat speech/barge-in run received **1,463 non-silent decoded media
frames** across its turns. The barge-in truncation identified assistant item
`3acfbd16` at **450 ms**, and the subsequent reply included “Interruption
confirmed.” The direct comparison marked its interrupted item at approximately
**395 ms** and received the same requested confirmation. Those are individual
test runs, not comparable accuracy measurements or latency benchmarks.

## Windows rerun

Environment: Windows 11, uv 0.13.5 with uv-managed Python 3.11.17, the same
locked packages, through `tasks.py` (no `make`).

| Check | Result |
| --- | --- |
| `python tasks.py setup` | PASS: bootstrap, Python download into `.bootstrap/python`, locked sync; a rerun only re-checks |
| `python tasks.py check` | PASS: 18 tests, Ruff, Python compilation |
| Runner startup without `PYTHONUTF8` | PASS after the `utf8_console()` fix; previously crashed printing Pipecat's banner under cp1252 |
| `python tasks.py test-audio` | PASS: Windows speech synthesizer wrote both 48 kHz 16-bit mono WAVs |
| `python tasks.py live-probe` with both WAVs | PASS: model, typed response, planner function, synthetic audio input, barge-in with server truncation |

One earlier live run failed with `Unexpected response status: cancelled`. The
greeting had called the planner; the probe accepted the greeting after its first
completed response and sent the next prompt while the tool follow-up was still
speaking, which interrupted it. This is a probe timing race, not Windows-specific.

## Evidence artifacts

Ignored local reports (available in this checkout; intentionally not committed):

- `../higgs-realtime-1.5/captures/probe-2026-10-24T00-22-14.388Z.json`:
  original direct probe PASS, exact model, typed response, planner handler,
  **439 generated audio chunks**.
- `captures/direct-browser-comparison.json`: PASS for model, text, planner,
  same-WAV microphone input, and barge-in using unchanged Step 1 source.
- `captures/probe-20261024T003634815349Z.json`: final Pipecat PASS for
  greeting/model, typed response, planner function, synthetic audio input and
  interruption with server truncation.
- `captures/probe-20261024T004233164007Z.json`: final Pipecat mock-worker/native-search
  PASS; result-grounded planner answer and final search answer naming boson.ai.
- `captures/probe-20261024T003343972593Z.json`: expected FAIL for deliberately
  invalid credentials, with an explicit authentication diagnostic.

Earlier failed captures are retained locally. They exposed the typed-input
routing issue and loss of truncation state at response completion. Both fixes
have regression tests and were validated live. The search probe also has a
regression test ensuring a preamble alone cannot satisfy its answer check.

See [COMPARISON.md](COMPARISON.md) for the framework recommendation, measured
timing definitions, private-API coupling, and unresolved worker compatibility.
