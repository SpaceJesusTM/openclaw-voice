# ClawVoice — Complete product vision

**Tagline:** **Your AI team, in the room.**  
**Hackathon context:** Boson AI × Higgs Realtime, October 23–24, 2026  
**Purpose of this document:** preserve the desired **eventual product**, not merely the next Claude implementation prompt.

## The product idea

Users should converse naturally with their persistent OpenClaw agents as if those agents were members of an available, coordinated team. Agents have independent memory, responsibilities, personalities, tools, identities and voices. They can take real actions, make progress during an ongoing conversation, update the user without forcing them to poll, and collaborate with each other when invited. The experience should feel like **one shared conversational environment**, not separate chatbots, isolated push-to-talk commands, or a chain of blocking API calls.

ClawVoice is a **reusable harness**: connect an arbitrary compatible OpenClaw agent, configure its name/voice/role/capabilities and cached context, and gain the same conversational infrastructure. Tari/Astra/Jester are demonstration personas, not built-in requirements of the platform.

## What makes it different

1. **Realtime speech plus persistent agent abilities.** Higgs handles natural dialogue, listening, speaking, quick turns and barge-in; OpenClaw supplies genuine work, persistent sessions, memory and tools.
2. **Fresh, configurable fast context.** Each agent preloads frequently requested, *approved* facts in the background. Fast queries need not wait for a complete OpenClaw inference/tool round-trip.
3. **Nonblocking work.** OpenClaw jobs continue while the user speaks about something else. The user can ask for status, explicitly cancel when safe, or hear verified completion later.
4. **Distinct agent identities in one room.** Every agent can have its own voice, role, behavior, OpenClaw backend and colored orb. Saying an agent's name selects it.
5. **Real collaboration.** Multiple independent agents exchange structured messages, not fake roleplay by one model; a coordinator controls who addresses the user and when.
6. **Long-term ownership boundaries.** Different humans can eventually bring their own agent teams into shared rooms without unrestricted cross-account data access.

## End-state user experiences

### A. Single personal assistant (Priority 1)

**Fast planner:** "Tari, what's in my day planner today?" → Higgs asks the ClawVoice context tool → receives Tari's **fresh actual** planner/checklist/calendar snapshot → speaks promptly. The answer is not generated from a stale invented summary or the experimental mock fixture. Topics, TTLs and resolvers are configurable for every agent.

**External action:** "Tari, create a Calendar event called Study Group on Monday at 3 PM." → Higgs delegates a structured job to Tari via OpenClaw → Tari uses its Calendar skill → verifies real event details → result is spoken. The assistant never announces success solely because a job was accepted.

**Concurrent work:** "Tari, work on that. Meanwhile, what's tomorrow's schedule?" → the job runs independently; the assistant answers the fresh cache-backed question. Interrupted audio does not cancel the pending job. A completion is announced when the speech floor is appropriate.

**Persistent continuity:** "Did that finish?" and "What were we doing before?" use a bounded recent-results log and stable OpenClaw session context, not invented status or uncontrolled transcript dumps.

### B. A team of independent agents (Priority 2a)

**Tari 🟢 — personal operations.** Calendar, planner/checklist, Gmail read-only summary and authorized personal tasks.

**Astra 🔵 — coding and work.** Tracks a project, answers build status, triggers safe tests and gives concise updates through its own OpenClaw tools.

**Jester 🩷 — fun and social.** Jokes, games, trivia and light entertainment via its own character and context.

These are configurable examples. Each agent has a distinct voice, role description (potentially Markdown), backend identity/session, capabilities, optional cache topics and visual identity. A configurable *lead/primary* agent can handle unnamed requests, but other agents do not have to route every operation through it.

**Explicit addressing:** "Hey Tari...", then "Astra...", then "Jester...". Only the addressed agent responds unless more than one was intentionally invited. Agents must not all speak at once. Switching should feel seamless.

### C. Intelligent routing and actual collaboration (Priority 2b)

When no name is said, route based on the request and agent roles, subject to uncertainty and permission checks. For example, project build status goes to Astra; scheduling to Tari. Do not let an inferred route silently authorize another user's actions.

**Genuine shared task:** "Hey guys, can we finish the project tomorrow?" Astra independently estimates remaining engineering work. Tari independently consults schedule availability. The coordinator delivers both views sequentially, allows the human to decide, and routes an explicitly approved booking to Tari. The agents exchange structured proposals/results, with provenance and permission boundaries, instead of one agent pretending to speak as everyone.

**Conversational floor:** humans get priority, overlapping speech is prevented, agent announcements are queued, agent-to-agent turns are deliberate, interruptions and changes of topic are respected, and irrelevant agents stay quiet.

### D. Multiple humans with independently owned agents (Priority 3 / stretch)

Users can share a voice room while retaining independent OpenClaw deployments, identities, private memories, tokens and permissions. Their agents can negotiate meeting times, exchange **only authorized** availability/proposals, and obtain human approvals before consequential cross-user actions. Support per-user consent, provenance and least-privilege sharing. **Never equate being in the same room with permission to read another person's inbox or memory.** This is not necessary for the first hackathon integration.

## Visual and interaction design goal

A polished, recognizably ClawVoice interface, eventually showing animated agent orbs and agent states:

| Agent (example) | Color | Core feel |
| --- | --- | --- |
| Tari | Green | Dependable personal coordinator |
| Astra | Blue | Focused technical teammate |
| Jester | Pink | Playful entertainer |

States: **idle, listening, speaking, working, waiting to speak**. Only active speakers use the speaking animation. Show current focus, queued work, recent verified completions and meaningful errors. Aesthetic quality matters for the hackathon demo, but the first working voice bridge must not be sacrificed for elaborate UI work. Colors/names/voices remain user-configurable.

## What's in scope when

- **Priority 1 — nonnegotiable initial build:** functional Boson Higgs 1.5 speech-to-speech + Pipecat + one real OpenClaw agent; reusable adapter; per-agent hot context; background refresh and read-after-write invalidation; async jobs; spoken verified completions; interruption-safe execution; functional local UI.
- **Priority 2a — next demo expansion:** two, preferably three, independently provisioned agents; individual voices/identities; name-directed routing; sequential floor control; visible agent orbs.
- **Priority 2b — next differentiation:** natural automatic agent selection; genuine structured collaboration; coordinated multi-agent speech; one demonstrable joint workflow.
- **Priority 3 — future expansion:** multiple people, independently owned agent environments, cross-user negotiation and approvals.

The original hackathon brief called **two real voiced agents** part of its hackathon minimum viable demo; this does **not** make multi-agent integration part of the immediately upcoming **Priority 1 single-agent Claude assignment**. The staged strategy is to make Priority 1 rock-solid, then reach the multi-agent demo target without re-architecting the harness.

## Non-goals and product principles

- Not merely "OpenClaw with a microphone"; fast context and continuous conversation are core.
- Not a replacement for OpenClaw memory, skills, Calendar integration or persistent workspaces.
- Not a system where one LLM roleplays supposedly independent agents.
- No false or unverified announcements of actions, task outcomes, permissions or freshness.
- No implicit modifications from read-only data or suggestions.
- No need for always-on public hosting, commercial subscription services, complex distributed infrastructure or cross-user federation in the initial build.
- The user's explicit current instruction is authoritative; external text (mail, calendar descriptions, project files) is *data*, not an instruction or authorization.

## Product north star

> **Give your agents a voice.** Your assistant, your developer, your entertainer—each capable of real independent work, all accessible through one natural conversation.
