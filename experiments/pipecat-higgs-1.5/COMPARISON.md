# Direct WebSocket versus Pipecat

Recommendation: adopt Pipecat for the next single-agent ClawVoice prototype,
using the pinned versions and the two tested compatibility adapters in this
experiment. Keep the direct playground as the protocol reference and regression
harness. Acoustic testing and longer worker/queued-audio tests should precede
promoting this stack to the main application foundation.

## What the experiment establishes

Both implementations have actually acknowledged **`higgs-realtime-1.5-preview`**
in live sessions, generated speech from the same typed prompt, and answered the
same planner question using a locally executed function and the same fixture.
Pipecat additionally passed native server search and a real mock worker-bus job
while retaining the speech-to-speech voice loop. Full results and limits are in
[TEST_RESULTS.md](TEST_RESULTS.md).

| Concern | Direct Step 1 | Pipecat Step 2 |
| --- | --- | --- |
| Runtime/setup | Node, small local token server, browser AudioContext/Worklet | Python 3.11, uv, 119-package lock graph, WebRTC/Opus/native wheels and runner |
| Where Boson connects | Browser, using a minted short-lived secret | Python service, using the server-side API key; browser connects to local WebRTC |
| Audio and turn handling | Explicit PCM capture, scheduling, interruption bookkeeping | Pipecat transport, realtime aggregators, Boson service, and interruption frames |
| Tools | Shared custom protocol/tool loop with call-ID dedupe | Python callables, automatic schema/handler registration, result callbacks, async tool options |
| Visibility | Dedicated model indicators, debug log, tool display, interruption labels | Stock captions/metrics/events UI, RTVI custom diagnostics, sanitized server traces |
| Native search | Optional declaration in session config | ToolsSchema custom server tool; SDK tracks executor and handles server results |
| Worker jobs | No worker implementation in scope | WorkerRunner, PipelineWorker, typed jobs, updates/results/cancellation and registry |
| Reuse boundary | Extract protocol, tool adapter, and browser audio independently | Replace the planner handler/backend worker while retaining transport and S2S service |

## Concrete compatibility findings

**Typed input needs a routing adapter on this pinned stack.** In Pipecat 1.12.0,
the user context aggregator consumes `LLMMessagesAppendFrame` and sends an
updated context. Boson 0.1.4 treats later context frames as tool-result updates,
so no typed user item or response was emitted in our first live run.
`RealtimeTextBridge` sends that existing frame to the service's native
append handler using Pipecat's queue API. A real-pipeline unit test verifies
exactly one user item and response request.

**Generation completion can precede the interruption event.** We observed a
cancelled `response.done` immediately before `input_audio_buffer.speech_started`.
The SDK cleared `_current_audio_response` at generation completion. Playback
interrupted, but the server received no conversation truncation. The service
adapter retains that native reference through generation completion until the
SDK receives `BotStoppedSpeakingFrame`, letting its existing truncation method
operate. The regression test covers that event order, and the final live WAV
barge-in probe passed with a truncation and new spoken reply.

The adapter does not replace audio buffering or tool dispatch. Its private
hooks (`_dispatch_boson_server_event`, `_handle_evt_response_done`) tie it to
the pinned SDK and need review during upgrades. Pipecat-boson 0.1.4 explicitly
requires Pipecat below 1.13; the newer async invocation API is incompatible.

**Prompt fidelity varies.** One Pipecat planner response named the correct
fixture title but omitted the requested verification code. Other runs included
both. The probe requires actual handler evidence and the distinctive first
title; exact wording or inclusion of every fixture field is not guaranteed.

## Realtime visibility and future workers

The installed service exposes public session-created, session-terminated and
conversation-item hooks. Pipecat observers expose transcript, error,
interruption, and metrics frames; RTVI carries client text, captions, tool
metadata, and custom server messages. Raw events and subsequent session
acknowledgements required the small private dispatch observer here. This is
sufficient visibility for the next prototype, with upgrade coupling documented.

We inspected Pipecat **v1.12.0**'s
[OpenClaw worker example](https://github.com/pipecat-ai/pipecat/tree/v1.12.0/examples/multi-worker/openclaw-agent)
and the installed `OpenClawGatewayService`. The example uses a conventional
STT → text LLM → TTS voice worker and a separate Gateway worker. The bus operates
at the worker/job level, so the voice pipeline can instead contain Higgs S2S
with realtime aggregators, as this experiment demonstrates using the mock job.

`CLAWVOICE_MOCK_WORKER=true` attaches both the voice pipeline and a mock planner
PipelineWorker to the same WorkerRunner. The actual function uses
`params.pipeline_worker.job(..., JobParams(...))`; the backend sends a progress
update and a final response. The live model then speaks the returned fixture
data. An offline bus test also interrupts the voice pipeline while the job
finishes. The tool opts into `cancel_on_interruption=False`; stopping audio
does not request cancellation of the backend job.

Important remaining work:

- The real Gateway needs its own URL, authentication token and session key.
  None was read, configured, or contacted in this experiment.
- Gateway steering aborts a run and starts a replacement; it must not be
  presented as concurrent continued work. Explicit job cancellation maps to
  an abort separately from voice interruption.
- The current realtime service path supports final asynchronous function results;
  the inherited implementation rejects streamed intermediate tool-result
  payloads. Bus progress can be shown via RTVI separately. Long-running work
  needs a completion/announcement policy rather than occupying a spoken turn.
- Backend workers need idle-timeout handling appropriate to jobs. This mock
  disables its own speech-based idle timeout, following the example's reasoning.
- Multiple independent agent contexts, name routing, agent voices, speaker floor,
  and multi-user transports have not been tested or implemented.
- The native truncation estimate still uses wall-clock/audio-size timing. Long
  queued multi-item replies and physical output latency deserve dedicated tests.

## Latency observations

These are diagnostic observations on one Mac and one network, not a benchmark.
The common metric below is the locally received `response.created` event to
the first `response.output_audio.delta` for that response. It excludes waiting
before the server starts a response and all subsequent playback latency.

| Live case | Direct probe | Pipecat baseline probe |
| --- | ---: | ---: |
| Same typed verification prompt | 337 ms | 301 ms |
| Planner acknowledgement/preamble | 748 ms | 312 ms |
| Planner answer after the local result | 935 ms | 846 ms |

Sources: direct capture ending `002214.388Z` and Pipecat session
`20261024T002915345149Z` (local ignored captures). Pipecat's typed prompt to first
Boson audio was 340 ms in that baseline run; the completed spoken turn took
2788 ms. Short mock-worker lookup execution measured 56 ms in another run,
including a deliberate 50 ms sleep. Replies varied in duration/content, so
completion times across runs do not isolate framework overhead.

The first cold Pipecat process took about 36 seconds between initial model ack
and the first response request while its local pipeline initialized. Later
sessions reached acknowledgement in hundreds of milliseconds. We did not
instrument each initialization component, and do not attribute the whole cold
delay to a particular dependency. These samples do not establish a latency
advantage for either implementation.

## References

- [Official Boson preview tutorial](https://github.com/boson-ai/Higgs-Realtime-Tutorial/tree/realtime-1.5-preview/pipecat)
- [Boson integration source](https://github.com/boson-ai/pipecat-boson/tree/01fd52c3ae6138d2f071bdd38f8f1956722a756a)
- [Pipecat v1.12 worker source](https://github.com/pipecat-ai/pipecat/tree/v1.12.0/src/pipecat/workers)
