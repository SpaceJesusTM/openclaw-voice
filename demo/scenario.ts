import type { AgentConfig } from "../src/agent/Agent";

/**
 * A standup with a planted conflict: Bob believes the design review is on
 * Friday, but it is Thursday, and the schedule is Alice's job. So she has a
 * reason to cut him off. The human's opening question concerns both of them,
 * so they may also have to settle who answers first.
 */
export const AGENTS: AgentConfig[] = [
  {
    name: "Alice",
    role: "the team's project manager, who owns the schedule.",
    facts: "the design review is on Thursday at 3 pm, and launch is the Monday after.",
    voice: "nora",
  },
  {
    name: "Bob",
    role: "the team's lead engineer. You think the design review is on Friday, but you are not sure; Alice keeps the schedule.",
    facts: "the demo build has one bug left, a login crash, that needs about one more day to fix.",
    voice: "jake",
  },
  {
    name: "Carol",
    role: "the team's designer.",
    facts: "the new onboarding screens are finished, but the icons stay placeholders until Wednesday.",
    voice: "eleanor",
  },
];

export const HUMAN_VOICE = "oliver";

export const HUMAN_LINES = [
  "Hi everyone, quick sync before the design review. Is the demo going to be ready in time?",
  "Okay. Anything you need from me before then?",
];
