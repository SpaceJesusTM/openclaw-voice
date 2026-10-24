# ClawVoice — OpenClaw Voice Team

A hackathon project exploring natural conversations with a team of independent
OpenClaw agents. Voice is powered by **Boson Higgs Realtime 1.5 Preview**
(`higgs-realtime-1.5-preview`), with **Pipecat** as the chosen voice framework.

## Current progress

- [x] Direct Higgs 1.5 realtime test — `experiments/higgs-realtime-1.5/`
- [x] Pipecat + Higgs 1.5 experiment — `experiments/pipecat-higgs-1.5/`
- [x] Standalone Tari with ChatGPT OAuth, Gmail reads and full Calendar management
- [x] Persistent Calendar-aware day planner, commute blocks and categorized checklist
- [x] **Priority 1:** talk to the real Tari through Higgs + Pipecat, with a generic
      OpenClaw agent adapter, per-agent hot-context cache, nonblocking verified
      jobs and spoken completions — `services/voice/`, `apps/web/`
- [ ] Multiple persistent agents, distinct voices, name routing and collaboration
- [ ] Optional multi-person shared conversations

## Talk to Tari

```bash
python3 infra/openclaw/manage.py start   # the isolated demo OpenClaw (see below)
cd services/voice
make setup                               # first time only
make dev                                 # open http://127.0.0.1:7860 and click Connect
```

Ask "What's on my planner today?" (instant, from cached real data), "Create a
Calendar event tomorrow from 3 to 4 PM called Project Meeting" (done by the
OpenClaw agent, independently verified, announced when finished), and keep
talking while it works. Guide: **[docs/voice.md](docs/voice.md)** · Evidence:
[docs/voice-validation.md](docs/voice-validation.md).

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
the speaking floor, and (next) multi-agent routing and collaboration.
