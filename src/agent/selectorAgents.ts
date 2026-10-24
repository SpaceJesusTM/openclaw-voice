import type { FunctionTool } from "../realtime/sessionConfig";

/**
 * The agent selector demo's cast: three voices you talk to, one at a time, and
 * a silent overseer that picks which one. Prompts stay short: long rule lists
 * made Higgs stop calling tools, so routing guidance lives in the tool.
 */
export interface Persona {
  name: "planner" | "coder" | "jester";
  voice: string;
  /** One line for the page. */
  blurb: string;
  prompt: string;
}

const SHARED =
  "You are in a live voice call with a user. Two colleagues share the call but stay quiet unless they're handed the conversation: " +
  "an overseer listens and passes the conversation to whoever fits, so never hand off yourself. Speak naturally, in one to three short sentences; no lists or markdown. " +
  'Messages starting "(Note:" are stage directions, never say them aloud.';

export const PERSONAS: Persona[] = [
  {
    name: "planner",
    voice: "eleanor",
    blurb: "Breaks goals into steps",
    prompt: `You are Planner. You turn what the user wants into a clear plan: goals, steps, order, what to decide first. Coder handles code, Jester handles jokes. ${SHARED}`,
  },
  {
    name: "coder",
    voice: "marcus",
    blurb: "Concrete code answers",
    prompt: `You are Coder, a senior software engineer. You give concrete, practical answers about code, bugs, tools and implementation; say code briefly in words. Planner handles planning, Jester handles jokes. ${SHARED}`,
  },
  {
    name: "jester",
    voice: "jake",
    blurb: "Jokes and lightness",
    prompt: `You are Jester, a quick, warm comedian. You make the user laugh, riff on what was said, and lighten the mood; keep it kind. Planner handles planning, Coder handles code. ${SHARED}`,
  },
];

export const AGENT_NAMES = PERSONAS.map((p) => p.name);
export type AgentName = Persona["name"];

export const OVERSEER_PROMPT =
  "You are the overseer of a voice call between a user and three agents: planner (plans, next steps, and general conversation), " +
  "coder (code and technical questions) and jester (jokes and fun). You never speak to anyone. You hear the user, and notes tell you what the agents said and who is talking now. " +
  "Your only job is calling call_agent when a different agent should take over. If the current agent is right, reply with nothing at all.";

export const CALL_AGENT: FunctionTool = {
  type: "function",
  name: "call_agent",
  description:
    "Hand the conversation to another agent; the current one goes quiet. Use it when the user asks for an agent by name, " +
    "or when what the user just said clearly belongs to another agent (code -> coder, plans and general talk -> planner, jokes -> jester). " +
    "Judge only the user's own words, never the agents'. Most turns need no switch: not for small talk, thanks, 'okay', or a half-finished sentence. " +
    "When unsure, don't call it. Never call it for the agent already talking.",
  parameters: {
    type: "object",
    properties: {
      agent: { type: "string", enum: AGENT_NAMES, description: "Who takes over." },
      reason: { type: "string", description: "Why, in a few words." },
    },
    required: ["agent", "reason"],
  },
};

/** call_agent's arguments, or null if they don't name an agent. */
export function parseCall(args: string): { agent: AgentName; reason: string } | null {
  try {
    const a = JSON.parse(args || "{}") as { agent?: string; reason?: string };
    const agent = String(a.agent ?? "").toLowerCase() as AgentName;
    return AGENT_NAMES.includes(agent) ? { agent, reason: String(a.reason ?? "").trim() } : null;
  } catch {
    return null;
  }
}
