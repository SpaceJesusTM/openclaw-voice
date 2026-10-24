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
  "You can start background work with start_task. When the user answers an approval request, just acknowledge it briefly; it's passed on for you. " +
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
  "You act only through tools: call_agent when a different agent should take over, and answer_approval when the user answers an approval request. " +
  "If nothing needs doing, reply with nothing at all.";

export const CALL_AGENT: FunctionTool = {
  type: "function",
  name: "call_agent",
  description:
    "Hand the conversation to another agent; the current one goes quiet. Use it when the user asks for an agent by name, " +
    "or when what the user just said clearly belongs to another agent (code -> coder, plans and general talk -> planner, jokes -> jester). " +
    "Judge only the user's own words, never the agents'. Most turns need no switch: not for small talk, thanks, 'okay', or a half-finished sentence. " +
    "Answering an approval request, or asking about a background task, is not a reason to switch: whoever is talking handles it. " +
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

/** Every agent's tool: start background work. Guidance lives here, not in the persona prompts. */
export const START_TASK: FunctionTool = {
  type: "function",
  name: "start_task",
  description:
    "Start a background job, for you or another agent, ONLY when the user explicitly asks for something to be made or done " +
    "as a separate piece of work ('code up a login page', 'write that doc', 'go build it'). Never for things you can just talk through " +
    "together ('let's plan the launch' is a conversation, not a job). Say a short line first, like 'On it, I'll let you know when it's done'.",
  parameters: {
    type: "object",
    properties: {
      title: { type: "string", description: "The work, in a few words, e.g. 'login page'." },
      for_agent: { type: "string", enum: AGENT_NAMES, description: "Who does it. Leave out to do it yourself." },
    },
    required: ["title"],
  },
};

/**
 * The overseer's second tool. Approvals are a control decision, like routing:
 * the voice agents, asked to both talk and call it, often said "Great, I'll
 * apply it" and never called it.
 */
export const ANSWER_APPROVAL: FunctionTool = {
  type: "function",
  name: "answer_approval",
  description:
    "Pass on the user's answer when an agent has just asked them to approve a background task (a note names the task). " +
    "'Yes', 'go ahead', 'approve it', 'sure' mean approve; 'no', 'stop', 'don't' mean deny. Only when the user is clearly answering it.",
  parameters: {
    type: "object",
    properties: {
      task_id: { type: "number", description: "The task's number, from the note about it." },
      // An explicit choice, not a boolean: with approve: true/false, a clear "yes, approve it" came through as false.
      decision: { type: "string", enum: ["approve", "deny"], description: "What the user said." },
    },
    required: ["task_id", "decision"],
  },
};

export const AGENT_TOOLS: FunctionTool[] = [START_TASK];
export const OVERSEER_TOOLS: FunctionTool[] = [CALL_AGENT, ANSWER_APPROVAL];

/** start_task's arguments; the owner defaults to the agent that called it. */
export function parseStartTask(args: string, caller: AgentName): { title: string; owner: AgentName } {
  try {
    const a = JSON.parse(args || "{}") as { title?: string; for_agent?: string };
    const owner = AGENT_NAMES.find((n) => n === String(a.for_agent ?? "").toLowerCase()) ?? caller;
    return { title: String(a.title ?? "").trim() || "a task", owner };
  } catch {
    return { title: "a task", owner: caller };
  }
}

/** answer_approval's arguments, or null if malformed. */
export function parseAnswer(args: string): { taskId: number; approve: boolean } | null {
  try {
    const a = JSON.parse(args || "{}") as { task_id?: unknown; decision?: unknown };
    const taskId = Number(a.task_id);
    const decision = String(a.decision ?? "").toLowerCase();
    return decision === "approve" || decision === "deny" ? { taskId, approve: decision === "approve" } : null;
  } catch {
    return null;
  }
}

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
