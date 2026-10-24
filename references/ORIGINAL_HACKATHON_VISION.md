# Tari Voice — Giving OpenClaw Agents a Voice

**Boson AI × Higgs Realtime Hackathon | October 23–24, 2026**

## 1. Project Overview

**Tagline: Your AI team, in the room.**

Our goal is to build a reusable, real-time voice harness for OpenClaw that enables natural conversations with individual AI agents and entire teams of agents.

Rather than interacting with AI through traditional text interfaces or one-off voice commands, users can speak naturally to their agents, switch between them, have agents perform tasks in the background, and hear them collaborate.

Each agent has its own voice, personality, responsibilities, memory, and tools.

For example, a user might have:

- **Tari 🟢 — Personal Assistant:** Manages schedules, reminders, emails, daily planning, and general personal tasks.
- **Astra 🔵 — Coding/Work Assistant:** Manages coding projects, monitors development progress, runs tasks, and provides technical updates.
- **Jester 🩷 — Entertainment Assistant:** Tells jokes, plays games, runs trivia, and provides casual entertainment.

These are example agents. The system should support arbitrary OpenClaw agents rather than being hard-coded for these specific roles.

**The core idea is to make interacting with AI agents feel like talking to a real team of assistants rather than using several disconnected chatbots.**



---



## 2. Main Features and Development Priorities

We will develop the project in stages, allowing us to reduce scope if time becomes limited.

### Priority 1 — Single-Agent Voice Harness

Build a reusable adapter connecting Boson Higgs Realtime with any compatible OpenClaw agent.

The harness should provide:

**A. Natural conversational interaction**

- Real-time, low-latency voice input and output.
- Natural back-and-forth conversations.
- Ability to interrupt, redirect, and ask follow-up questions.
- Access to the agent's existing OpenClaw memory, tools, and capabilities.

**B. Fast-access context/cache system**

Each agent maintains a configurable cache of frequently needed information so it can answer common questions quickly without repeatedly fetching data.

Examples:

- Tari: Today's schedule, upcoming deadlines, current reminders.
- Astra: Active projects, task progress, recent development updates.
- Jester: Active games, conversation context, entertainment preferences.

The cache should:

- Be configurable for different kinds of agents.
- Refresh periodically (potentially using OpenClaw heartbeats) or when relevant data changes.
- Track freshness to avoid outdated answers.
- Allow the agent to retrieve information not available in the cache.

**C. Background task execution**

The user should be able to continue conversations while OpenClaw performs longer-running operations.

Example:

User: "Astra, run the tests on my project."

Astra: "Sure, I'll let you know when they're done."

The user continues chatting with another agent or discussing something unrelated.

When the tests finish, Astra automatically announces:

"Hey, the tests finished! Everything passed."

**Key requirement:** The voice conversation must not block while tasks run, and agents should be able to proactively announce results when appropriate.

### Priority 2a — Multiple Independent Voiced Agents

Extend the harness to support multiple OpenClaw agents simultaneously.

Each agent should have:

- A distinct voice and personality.
- Its own OpenClaw backend and persistent context.
- Configurable responsibilities and capabilities.
- A unique visual identity.
- The ability to independently execute tasks.

**Explicit agent addressing**

Users can directly speak to particular agents by name.

Examples:

- "Hey Tari, what's on my schedule?"
- "Astra, how's the project coming along?"
- "Jester, tell me a joke."

Only the addressed agent should respond.

The user should be able to seamlessly switch between agents within the same conversational environment.

**Shared voice interface**

A central coordinator determines which agent receives each request and which agent is allowed to speak.

This prevents agents from responding unnecessarily or talking over one another.

### Priority 2b — Intelligent Agent Routing and Collaboration

Extend the system so users don't always need to explicitly name an agent.

**A. Automatic agent selection**

The system determines which agent should respond based on the user's request.

Example:

User: "Can you add my project deadline to my calendar?"

Tari responds because scheduling belongs to its responsibilities.

User: "What's the progress on our project?"

Astra responds because it manages development work.

**B. Agent-to-agent collaboration**

Agents can communicate with each other and coordinate work.

Example:

User: "Hey guys, can we finish the project tomorrow?"

Astra: "I have about two hours of coding left."

Tari: "You have an open block tomorrow morning. I can schedule two hours for coding."

User: "Perfect, do that."

Tari: "Done!"

The communication between agents should be genuine: each agent processes the task using its own OpenClaw backend.

Internally, agents can exchange structured messages rather than literally sending voice audio to one another. Their responses are then spoken aloud using their distinct voices.

**C. Conversational floor management**

A central coordinator manages who is allowed to speak.

It should:

- Prevent agents from talking over each other.
- Give human speakers priority.
- Allow agents to request speaking turns.
- Queue background task completion announcements.
- Support user interruptions and redirection.
- Avoid unnecessary agent chatter.
- Allow multiple agents to respond sequentially when appropriate.

### Priority 3 — Multiple Humans and Their Agent Teams (Stretch Goal)

Eventually, extend the system to support conversations involving multiple humans and independently owned AI agents.

Example:

Two people are discussing scheduling a meeting.

User A: "Tari, can you find a meeting time that works for both of us?"

Tari contacts User B's assistant.

Both agents check their respective users' availability, negotiate possible times, and propose a meeting.

Both human users approve before the meeting is scheduled.

Potential capabilities include:

- Shared voice conversation rooms.
- Multiple humans and multiple agents.
- Communication between separately owned OpenClaw environments.
- Agent-to-agent scheduling, research, and negotiation.
- Permission-controlled data sharing.
- Human approval for consequential actions.

This is a longer-term extension rather than a requirement for the initial hackathon demonstration.



---



## 3. Proposed Technical Architecture

The system will have four main components.

### Component A — Boson Higgs Realtime Voice Layer

Responsible for:

- Microphone input and streaming audio.
- Speech understanding and voice responses.
- Real-time conversation and interruptions.
- Agent-specific voice identities.

Ideally, each agent would use an independently configured Higgs Realtime session.

However, we can fall back to a shared listening session with separate voice generation for individual agents if managing multiple concurrent realtime sessions proves too complicated.

### Component B — Voice Team Coordinator

The central orchestration layer.

Responsibilities:

- Identify which agent the user is addressing.
- Route speech and requests to the correct agent.
- Manage conversational turn-taking.
- Prevent overlapping speech.
- Track agent activity and background tasks.
- Deliver proactive task completion announcements.
- Support agent-to-agent communication.
- Manage shared conversation context.

The coordinator should support a configurable primary/lead agent, such as Tari, without requiring all requests to pass through that agent.

### Component C — Generic OpenClaw Agent Adapter

Provides a consistent interface between the voice harness and OpenClaw.

The adapter should support:

- Connecting to arbitrary configured OpenClaw agents.
- Sending requests and receiving responses.
- Maintaining persistent agent sessions.
- Accessing each agent's memory and tools through OpenClaw.
- Tracking task execution and completion.
- Providing configurable, agent-specific hot context.

Agent identities, responsibilities, voices, and cache providers should be configurable, potentially using Markdown files and a central team configuration.

### Component D — Visual Interface

Create a polished, visually appealing interface displaying the active agent team.

Each agent is represented by a colored, animated audio orb:

- Tari: Green
- Astra: Blue
- Jester: Pink

The orbs should visually communicate agent states:

- Idle
- Listening
- Speaking
- Working on a task
- Waiting to speak

Only the currently speaking agent should have an active speech animation.

The interface could also display background task progress, current agent activity, and recent actions.



---



## 4. Existing Foundation

We already have two useful starting points.

**Tari Public**

A portable, Docker-based OpenClaw personal assistant environment with an authenticated Gateway, persistent agent state, and general assistant capabilities.

Repository:[ https://github.com/SpaceJesusTM/tari-public](https://github.com/SpaceJesusTM/tari-public)

We should build against an isolated development instance rather than interfere with the existing personal Tari installation or production credentials.

**Higgs Realtime Prototype**

We also have an existing working Node.js Higgs Realtime playground that has successfully connected to Boson, streamed microphone audio, and received generated speech.

The immediate next step is integrating this prototype with OpenClaw and validating an end-to-end spoken request that invokes a real agent.



---



## 5. Implementation Roadmap

### Milestone 1 — Working Single-Agent Integration

- Connect the existing Higgs prototype to an OpenClaw agent.
- Support real spoken requests and spoken agent responses.
- Test actual tool execution.
- Establish a stable conversation session.
- Implement basic interruption handling.

**Success condition:** A user can speak naturally to a real OpenClaw agent and get useful results.

### Milestone 2 — Context Caching and Background Tasks

- Implement a generalized hot-context mechanism.
- Add at least one real cache provider.
- Support asynchronous background execution.
- Implement task completion events.
- Allow the voice agent to proactively announce completed tasks.

**Success condition:** The agent answers common questions quickly and remains conversational while handling longer-running work.

### Milestone 3 — Multi-Agent Voice Team

- Configure at least two independent OpenClaw agents.
- Give them distinct voices and personalities.
- Implement name-based routing.
- Implement a central voice floor manager.
- Allow seamless switching between agents.
- Add the animated multi-agent interface.

**Success condition:** Two or three agents can participate in the same voice environment and only respond when appropriately addressed.

### Milestone 4 — Intelligent Collaboration

- Implement automatic relevance-based agent routing.
- Support structured agent-to-agent messages.
- Enable multiple agents to contribute to the same task.
- Add turn-taking for agent-to-agent spoken dialogue.
- Demonstrate a real collaborative workflow.

**Success condition:** The user can give the team a shared task and hear agents collaborate without uncontrolled interruptions or duplicate actions.

### Milestone 5 — Stretch Features

If time permits:

- Multi-human voice rooms.
- Cross-user agent communication.
- Shared agent collaboration across different environments.
- More sophisticated interruptions and routing.
- Advanced proactive conversation.
- Additional visual polish.



---



## 6. Hackathon Demonstration / Storyboard

**Scene 1 — Morning: Tari**

The user is getting ready for the day.

"Hey Tari, what's my schedule looking like?"

Tari immediately responds with the day's schedule and reminders using its fast-access context.

A green audio orb pulses while Tari speaks.

**Scene 2 — Afternoon: Astra**

The user is working at their computer.

"Hey Astra, how's the code coming along?"

Astra responds in a distinct voice with the latest project status.

The user asks Astra to run additional tests. Astra starts working in the background.

**Scene 3 — Evening: Jester**

The user is hanging out with friends.

"Hey Jester, tell us a joke!"

Jester's pink orb lights up and tells a joke.

The user and their friends continue casually interacting with Jester.

**Scene 4 — Background Task Completion**

While the user is talking with another agent, Astra's background work finishes.

When the current speaker is done, Astra speaks up:

"Quick update! The tests finished, and the integration is ready for review."

This demonstrates that agents can independently complete tasks and naturally rejoin conversations.

**Scene 5 — Agents Collaborating**

The user asks:

"Hey guys, can we get the project finished tomorrow?"

Astra estimates the remaining coding work.

Tari checks the schedule and suggests an available work block.

The user approves the plan, and Tari schedules it.

The agents coordinate without talking over one another.

**Scene 6 — Goodnight**

The user puts their phone on the nightstand.

"Alright guys, good work today. Goodnight!"

Each agent responds in turn, using its own personality and voice.

The three colored orbs fade out.

**Closing message:**

"Your assistant. Your developer. Your entertainer. Your entire AI team, always just a conversation away."

**GIVE YOUR AGENTS A VOICE.**

*Powered by OpenClaw × Boson Higgs.*



---



## 7. Hackathon Strategy and Scope

Our main objective is to demonstrate a compelling, working multi-agent voice experience rather than attempting to implement every possible feature.

**Minimum viable demo:**

- At least one real OpenClaw agent connected to Higgs.
- Natural voice conversations.
- Background task execution and proactive completion announcements.
- Basic fast-context caching.
- At least two independent agents with distinct voices.
- Explicit agent routing and controlled speaking turns.
- A simple visual interface.

**Target demo:**

- Three agents: Tari, Astra, and Jester.
- Smooth switching between agents.
- Fast cached responses.
- Continued conversation during background tasks.
- Proactive task completion announcements.
- One genuine agent-to-agent collaboration workflow.
- Animated, agent-specific visual identities.

**Stretch demo:**

- Automatic agent selection.
- Advanced agent collaboration.
- Shared conversations with multiple humans and their independent agent teams.

We should validate concurrent Higgs sessions, interruption behavior, and agent-specific voice generation early because these are important technical uncertainties.

We should also measure response latency, routing accuracy, task-completion delivery, and whether unintended overlapping speech occurs.



---



## 8. Core Differentiation

The innovation isn't simply connecting a voice model to OpenClaw or giving different AI characters different voices.

We are building a **real-time conversational orchestration system for independent AI agents**.

The main technical contributions are:

1. A reusable voice harness compatible with different OpenClaw agents.
2. A generalized fast-context system for low-latency responses.
3. Nonblocking voice conversations with asynchronous agent execution.
4. Proactive spoken notifications from completed background tasks.
5. Multi-agent voice routing and speaker/floor management.
6. Genuine coordination between independently configured agents.
7. A foundation for future human-and-agent group conversations.

**Long-term vision:** Every person can have their own team of AI agents, each with a unique voice, personality, memory, and set of capabilities. These agents can communicate naturally with their owner, collaborate with one another, and eventually interact with other people's agents.

The ultimate goal is to make working with AI agents feel less like operating software and more like talking to a team of capable collaborators.
