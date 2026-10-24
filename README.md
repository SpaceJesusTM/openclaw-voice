# ClawVoice — OpenClaw Voice Team

A hackathon project exploring natural conversations with a team of independent
OpenClaw agents. Voice is powered by **Boson Higgs Realtime 1.5 Preview**
(`higgs-realtime-1.5-preview`), with **Pipecat** as the chosen voice framework.

## Status: Priority 1 baseline (`release/p1-stable`, tag `v0.1.0`)

**Implemented** (`services/voice/`, `apps/web/`, `infra/openclaw/`):

- Single-agent realtime voice with Higgs 1.5 Preview over Pipecat (WebRTC, local browser UI)
- Real OpenClaw integration through a generic, configuration-driven agent adapter (Tari is the live agent)
- Per-agent hot-context cache for instant answers, with declared coverage and a live
  OpenClaw fallback for anything outside it
- Nonblocking background jobs, independent read-back before "done", spoken completion notices
- Gmail strictly read-only, latest-email retrieval matching the Gmail Primary tab
- Unified Calendar and day planner (new timed entries are Calendar events), local checklist
- America/Toronto timezone handling, including DST
- Push-to-talk, natural interruptions, and "stop talking" without resuming or replaying

**Priority 2a, branch `feat/p2a-team-voice` (for owner testing):** Astra, a second
real OpenClaw agent (coding, isolated workspace and local preview), and **Team
mode**: Tari and Astra in one voice room on one connection, addressed by name,
working concurrently, with one shared speaking floor and queued replies and
completion announcements. Tari-only and Astra-only modes remain.
See [docs/team-voice.md](docs/team-voice.md).

**PFinal 3, branch `feat/pfinal3-jester`:** **Jester**, a voice-only entertainment
agent (jokes, banter, roasts; Higgs voice `jake`) with its own tab, and a third seat
in Team mode, where the Higgs overseer routes to it like any other agent. Jester has
no OpenClaw backend, tools or jobs (`backend: voice` in `agents/jester.yaml`).

**Still future:** agent-to-agent collaboration; Priority 3 multiple people and
cross-user permissions.
See the [roadmap](docs/ROADMAP_ACCEPTANCE_DEMO.md).

## Talk to Tari

```bash
python3 infra/openclaw/manage.py start              # the isolated demo OpenClaw (see below)
python3 infra/openclaw/manage.py update-workspace   # keep Tari's skills current (safe to repeat)
cd services/voice
make setup                                          # first time only
make dev                                            # open http://127.0.0.1:7860 and click Connect
```

**[Getting started: run and test](docs/GETTING_STARTED.md)** · full guide:
[docs/voice.md](docs/voice.md) · evidence: [docs/voice-validation.md](docs/voice-validation.md)

## Start the OpenClaw foundation

```bash
python3 infra/openclaw/manage.py init --timezone America/Toronto
python3 infra/openclaw/manage.py start
```

Then follow **[OpenClaw + Google owner setup](docs/openclaw-setup.md)** for
ChatGPT-based GPT-6.1 Sol authentication and a separate Google account's
Gmail read-only consent and full Calendar authorization. See the [foundation audit](docs/openclaw-audit.md)
for runtime evidence and [planner usage](docs/planner.md) for direct and conversational commands.

OpenClaw Gateway is local-only on port **18939** by default. Separate generated
credentials, OAuth files and agent state stay under ignored
`infra/openclaw/.local/`. It must never use the production Tari installation.

**[Architecture and milestones](docs/architecture.md)** ·
[Product vision](docs/PRODUCT_VISION.md) ·
[Contracts](docs/ARCHITECTURE_AND_CONTRACTS.md) ·
[Roadmap](docs/ROADMAP_ACCEPTANCE_DEMO.md) ·
[Developer agent instructions](AGENTS.md)

Earlier Pipecat [validation results](experiments/pipecat-higgs-1.5/TEST_RESULTS.md)
and [comparison](experiments/pipecat-higgs-1.5/COMPARISON.md) remain as references.

## Contribution boundary

Pipecat handles audio transport, the realtime service, tools and the OpenClaw
Gateway protocol client; OpenClaw provides agent workspaces, memory, skills and
operations. ClawVoice provides the integration experience: configurable agent
identities, fast authorized context retrieval, nonblocking verified agent work,
the speaking floor, and (next, Priority 2a) multi-agent voice and routing.
