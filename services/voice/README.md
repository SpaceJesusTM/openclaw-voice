# ClawVoice voice runtime

Pipecat + Boson Higgs Realtime 1.5 Preview voice for configurable OpenClaw
agents, with a per-agent hot-context cache and nonblocking, verified OpenClaw
jobs. One local process; the browser UI is in [`apps/web`](../../apps/web).

```sh
make setup     # first time (uv 0.13.5, locked Python 3.11 environment)
make dev       # http://127.0.0.1:7860  (needs the infra/openclaw demo running)
make check     # offline tests, Ruff, compile
```

Full guide: [docs/voice.md](../../docs/voice.md) · Results:
[docs/voice-validation.md](../../docs/voice-validation.md)

| Path | Contents |
| --- | --- |
| `agents/` | Agent configurations (`team.yaml`, `tari.yaml` live, `astra.yaml` fixture) and role Markdown |
| `clawvoice/bot.py`, `server.py`, `runtime.py` | Voice pipeline per connection, local server/API, process-level runtime |
| `clawvoice/voice/` | Verified Boson service, typed-input bridge, tools, instructions, speaking floor |
| `clawvoice/openclaw/` | Generic agent adapter: WebSocket (paired device) and HTTP Gateway transports |
| `clawvoice/context/` | Generic hot-context cache and built-in resolvers |
| `clawvoice/jobs/` | Background jobs: model, persistent store, verifiers, manager |
| `clawvoice/plugins/day_planner.py` | Planner/Calendar resolvers and verifiers over the agent's helper JSON |
| `clawvoice/testing/` | Fake Gateway and fixture helpers for tests and the portability demo |
| `probes/` | Live acceptance probes (WebRTC/RTVI and browser) |
| `tests/` | Offline tests |

Private state (device key, job records) is in ignored `.local/`; sanitized
traces and probe reports are in ignored `captures/`.
