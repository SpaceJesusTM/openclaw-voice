# Validation results — 2026-10-23

Environment: macOS arm64, Node 24.16.0, npm 11.13.0, Playwright Chromium.
No experiment `.env` or process `BOSON_API_KEY` was available.

| Check | Result | Evidence / limit |
| --- | --- | --- |
| Typechecking | PASS | `npm run typecheck` |
| Protocol, tool dispatch, interruption state, credential boundary | PASS | `npm test`: 24 tests |
| Browser flows with simulated Boson connection | PASS | `npm run test:browser`: 7 tests; synthetic microphone |
| Production build | PASS | `npm run build` |
| Production serving | PASS | `npm start` on port 3106; page/config/worklet HTTP 200, `.env` 403, server source 404; Chromium load without page errors |
| Desktop/mobile layout | PASS | Chromium screenshots inspected; mobile overflow assertion passed |
| Probe help / missing-key handling | PASS | `--help` makes no requests; `--live` exits 1 with an explicit missing-key message |
| Live API probe | BLOCKED | No API key; no authenticated API requests executed |

Capability status is deliberately split between client verification and real service behavior:

| Capability | Offline verification | Live / physical verification |
| --- | --- | --- |
| Required/acknowledged preview model | PASS: exact URL/session model, accepted preview, rejected legacy/missing ack | NOT RUN: Boson's actual resolved model requires key |
| Short-lived client-secret authentication | PASS: HTTP boundary, TTL, secret filtering, auth header and failures with mocked upstream | NOT RUN: actual minting/handshake requires key |
| A: Typed message → speech/transcript | PASS: browser sends message + response request; consumes simulated PCM and transcript | NOT RUN: actual synthesis and audible speech |
| B: Microphone conversation | PASS: synthetic device captures 24 kHz PCM16 in 100 ms frames; start/stop and VAD event plumbing | NOT RUN: real microphone, speaker, STT, and server VAD |
| C: Interruptions | PASS: browser drops queued audio and sends heard-time truncation; unit tests cover zero-played and queued/completed items and ongoing tools | NOT RUN: natural spoken barge-in and real server truncation |
| D: Custom planner function | PASS: actual local handler and fixture result, argument errors, duplicate `call_id`, parallel calls, delayed tools, single continuation | NOT RUN: model choosing the function and speaking fixture values |
| E: Native web search | PASS: optional registration; server calls are displayed and never locally executed/continued | NOT RUN: Boson executing search and returning an answer |
| E: Selected voice | PASS: chosen ID is placed in session config; API rejection is visible | NOT RUN: accepted nondefault voice and audible difference |

No live capability is claimed as passed. No Boson preview defect was established;
service behavior remains unverified. The missing API key is the current blocker.
The [README](README.md) contains exact commands and the ordered live test procedure.
