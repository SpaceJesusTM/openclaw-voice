# ClawVoice — OpenClaw Voice Team

A hackathon project exploring natural conversations with a team of independent
OpenClaw agents. Voice is powered by **Boson Higgs Realtime 1.5 Preview**
(`higgs-realtime-1.5-preview`), with **Pipecat** as the chosen voice framework.

## Current progress

- [x] Direct Higgs 1.5 realtime test — `experiments/higgs-realtime-1.5/`
- [x] Pipecat + Higgs 1.5 experiment — `experiments/pipecat-higgs-1.5/`
- [ ] Standalone OpenClaw demo agent with ChatGPT/Codex auth, Gmail and Calendar
      reads — scaffolded in `infra/openclaw/`; owner OAuth and runtime tests pending
- [ ] Server-side Pipecat/OpenClaw bridge with hot context and recent-result cache
- [ ] Multiple persistent agents, distinct voices, name routing and collaboration
- [ ] Optional multi-person shared conversations

Read the Pipecat [validation results](experiments/pipecat-higgs-1.5/TEST_RESULTS.md)
and [comparison](experiments/pipecat-higgs-1.5/COMPARISON.md).

## Start the OpenClaw foundation

```bash
python3 infra/openclaw/manage.py init --timezone America/Toronto
python3 infra/openclaw/manage.py start
```

Then follow **[OpenClaw + Google owner setup](docs/openclaw-setup.md)** for
ChatGPT-based GPT-6.1 Sol authentication and a separate Google account's
Gmail/Calendar read-only consent. These steps require the user's accounts;
they have not been performed through the public GitHub branch.

OpenClaw Gateway is local-only on port **18939** by default. Separate generated
credentials, OAuth files and agent state stay under ignored
`infra/openclaw/.local/`. It must never use the production Tari installation.

**[Architecture and future milestones](docs/architecture.md)** ·
[Developer agent instructions](AGENTS.md)

## Contribution boundary

Pipecat already handles audio transport, tools and multi-worker plumbing;
OpenClaw provides agent workspaces, memory and operations. ClawVoice will provide
the integration experience: configurable independent agent identities, fast
authorized context retrieval, appropriate agent speaking/floor decisions, and
cross-agent collaboration through verified jobs.

The foundation stage intentionally **does not** integrate voice and OpenClaw
yet or implement multi-agent routing. Focus first on proving isolated agent
authentication and Google access before the larger build.
