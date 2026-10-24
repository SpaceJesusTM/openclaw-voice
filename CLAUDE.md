# ClawVoice — Claude project entry point

> **Priority 1 engineering handoff, 2026-10-24.** These documents preserve the full product vision and architecture intent. They are not evidence that future features exist. Treat the latest checked-out code, tests, and explicit owner request as authoritative for implementation status.

## Read these in order

1. Existing repository `AGENTS.md`, `README.md`, `docs/architecture.md`, and current code/tests. Keep existing security and Git instructions.
2. [Product vision and complete end state](docs/PRODUCT_VISION.md): why ClawVoice exists, what the *finished product* must enable, agent-team and multi-person goals.
3. [Technical architecture and contracts](docs/ARCHITECTURE_AND_CONTRACTS.md): reusable agent adapter, realtime speech, generic hot context, nonblocking work, orchestration, isolation, integration boundaries.
4. [Roadmap, acceptance tests, and demo](docs/ROADMAP_ACCEPTANCE_DEMO.md): what is mandatory *now* vs future phases and how each phase is proved.
5. [Original hackathon vision (historical)](references/ORIGINAL_HACKATHON_VISION.md): the user's original, unchanged concept brief. Its early implementation-status descriptions are historical, not a current status report.

These docs are **durable product and architecture intent**, not permission to implement all phases in one task. The latest explicit owner prompt determines the current assignment. If the latest assignment is **Priority 1**, complete Priority 1 only while keeping future milestones structurally possible. Avoid unrequested multi-agent federation, added subscriptions, public hosting, or speculative services.

## Product identity

**ClawVoice — “Your AI team, in the room.”** A reusable, real-time voice harness for **arbitrary OpenClaw agents**, powered by **Boson Higgs Realtime 1.5 Preview** and **Pipecat**. The voice model is the conversational foreground, OpenClaw is the persistent tool/memory/action backend, and ClawVoice coordinates cache, async execution, identity, and who speaks. Example agents: **Tari 🟢** personal/planning, **Astra 🔵** coding/work, **Jester 🩷** entertainment. Names, roles, colors, voices and capabilities are **examples and configurable**, not hard-coded platform assumptions.

## Phase boundary in one sentence

**P1:** one *real* voiced Tari, fast generic per-agent cache and ongoing nonblocking OpenClaw jobs. **P2a:** independently voiced, name-addressed agents in one room. **P2b:** automatic selection and genuine agent collaboration with turn/floor control. **P3:** multiple humans and independently owned agent teams with permissions/approvals.

## Verified foundation and references (snapshot, re-check before work)

- Repository: https://github.com/SpaceJesusTM/openclaw-voice; reference Tari: https://github.com/SpaceJesusTM/tari-public.
- Foundation branch: `feat/openclaw-google-foundation`; planner integration verified at commit `e77e9c42a5e44e1c0810986452da6dcd81a997d8`. Priority 1 implementation branch: `feat/voice-single-agent-adapter`, created from that commit. Confirm actual local/remote HEAD before modifying code.
- OpenClaw isolated demo in `infra/openclaw/`, local Gateway `127.0.0.1:18939` by default, with `openclaw/tari` agent selection and backend-only bearer auth. The underlying agent model is configured independently; Tari was verified using `openai/gpt-6.1-sol` with ChatGPT subscription OAuth.
- `tari-read`: Gmail and Calendar read-only OAuth; `tari-calendar-write`: independent Calendar management OAuth. Real reads and CLI create/update/read-back/delete passed; agent-led Calendar creation was exercised too. Gmail **must remain read-only**.
- The real Tari Public day planner has been ported and committed: `docs/planner.md`, `infra/openclaw/workspace/bin/day-planner`, and `infra/openclaw/workspace/skills/tari-day-planner/SKILL.md`. Its audit reports **78 offline tests passing**, live persistence across Docker restart, and agent-invoked planner reads/writes. Planner/checklist state remains local; Calendar entries remain separate. Check the active Mac installation, including `manage.py install-planner` if needed; remote audit is not proof of the current local checkout.
- The Pipecat/Boson experiment in `experiments/pipecat-higgs-1.5/` completed actual realtime preview, speech/audio, interruption, native tool, and mock worker tests. Inspect its documented compatibility fixes. Working reference pins: `pipecat-ai==1.12.0`, `pipecat-boson==0.1.4`, Python 3.11. The exact Higgs model is **`higgs-realtime-1.5-preview`**, not legacy `higgs-realtime`.
- **Timezone result:** Codex inspected the live smoke-test event: API stored 19:00Z for the requested 3 PM Toronto, correctly equivalent to `2026-10-26T15:00:00-04:00`. The 7 PM UI display is consistent with UTC display settings, **not** an event-writing bug. Preserve IANA `America/Toronto` conversions and validate future recurring/DST writes.

## Hard boundaries

- Never access, copy or modify **production Tari on the MSI GS66**, its keys, state or mailbox. Mac demo is an independent installation.
- Never print/commit secrets, provider tokens, redirect/code URLs, raw private mail, or private transcripts. Sensitive state is local/ignored; keep Gateway/Boson credentials on the server.
- Keep Google/Calendar business logic in the authorized OpenClaw agent; voice invokes and observes it, not a parallel privileged Gmail/Calendar implementation. A spoken request is not automatically a confirmed write; require actual verified backend results.
- Preserve existing OpenClaw sessions, OAuth profiles, day-planner Markdown, and working experiments. Do not force-push, reinitialize the agent, re-add obsolete model-policy enforcement, or replace the validated Pipecat stack casually.
- Model speech interruptions and background-job cancellation independently: an interruption stops spoken playback **without assuming** that an external action was cancelled.
- The default local single-agent voice test needs no multi-agent collaboration and no new cloud deployment. Do not add LiveKit or a redundant audio framework.

## Implementation philosophy

Do the **smallest coherent engineering work that makes the active phase genuinely usable and testable**. Reuse native Pipecat worker/job and WebRTC capabilities. Keep the cache/resolvers and adapter reusable; they should not branch on `agent_id == "tari"`. Validate observable source data instead of accepting plausible LLM claims as proof. Test live capabilities separately from simulated/mock paths. Report what's working, what's partial, what's not tested, and exact startup commands.
